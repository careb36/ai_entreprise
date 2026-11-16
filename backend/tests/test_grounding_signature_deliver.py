"""The deliver stage records the daemon's ``workspace.signatures`` reply (issue
#444, ``services/tools/signature_lookup.py``) through a fake NATS that runs
the real daemon: a delivery calling project code wrongly is verified with the
finding, an older daemon leaves the signatures not observed, and a delivery
that was not written asks nothing. The shared daemon is in
``grounding_signatures_helpers``.
"""

from __future__ import annotations

import asyncio

import grounding_signatures_helpers
import pytest
import signatures as daemon_signatures  # the daemon's module, on sys.path via conftest
import worker_stages
from models.agent_run import AgentRun, Verification
from models.company import Company, ProjectDiscovery
from models.sprint import BacklogItem, Sprint
from services.loop.worker import deliver as worker_deliver
from services.tools import signature_lookup
from sqlmodel import Session, select

# The shared fixtures.
daemon = grounding_signatures_helpers.daemon


# ---------------------------------------------------------------------------
# Through the task worker, with the real daemon
# ---------------------------------------------------------------------------


class _DaemonOverNATS:
    """Fake NATS client: each subject runs the real daemon's handler."""

    jetstream_enabled = False

    def __init__(self, daemon, signatures: bool = True):
        self.daemon = daemon
        self.signatures = signatures
        self.requests = []
        self.published = []

    async def request(self, subject, payload, timeout=5.0):
        self.requests.append((subject, payload, timeout))
        if subject == "workspace.generate":
            return self.daemon._deliver(
                payload["workspace_root"],
                payload["files"],
                payload.get("git"),
                self.daemon.write_files,
                self.daemon._resolve_target_path,
            )
        if subject == "workspace.signatures" and self.signatures:
            return daemon_signatures.signatures(
                payload["workspace_root"], payload, self.daemon._resolve_target_path
            )

        class NoRespondersError(Exception):
            pass

        raise NoRespondersError("nats: no responders available for request")

    async def publish(self, subject, payload, msg_id=None):
        self.published.append((subject, payload))


@pytest.fixture()
def db(monkeypatch, sqlite_session):
    bind = sqlite_session.get_bind()
    ProjectDiscovery.__table__.create(bind, checkfirst=True)
    worker_stages.use_engine(monkeypatch, bind)
    return sqlite_session


def _task(db: Session, project: str):
    company = Company(company_name=f"Sig Corp {project[-6:]}", autonomy_level=2)
    db.add(company)
    db.commit()
    db.refresh(company)
    db.add(ProjectDiscovery(company_id=company.id, project_path=project))
    sprint = Sprint(company_id=company.id, sprint_name="S", scope_size="M")
    sprint.status = "active"
    db.add(sprint)
    db.commit()
    db.refresh(sprint)
    item = BacklogItem(
        sprint_id=sprint.id,
        title="Use the pricing helper",
        description="Call price() from the new module",
        status="in_progress",
        priority="high",
        assignee="Developer",
    )
    db.add(item)
    db.commit()
    db.refresh(item)
    return (
        {"id": item.id, "title": item.title, "description": item.description},
        {"role": "Developer", "name": "Dev", "model": "m", "served_model": "m"},
        {"id": sprint.id, "company_id": company.id, "name": sprint.sprint_name},
    )


def _verification(db, item_id):
    db.expire_all()
    run = db.exec(
        select(AgentRun)
        .where(AgentRun.external_ref == f"task-{item_id}")
        .order_by(AgentRun.version.desc())
    ).first()
    return db.exec(
        select(Verification).where(Verification.agent_run_id == run.id)
    ).first()


ANSWER = """I used the pricing helper.

```python path=shop/checkout.py
from shop.pricing import price


def total(items):
    return sum(price(item) for item in items) + price(1, 2, 3)
```
"""


def _shop(tmp_path):
    project = tmp_path / "shop-project"
    (project / "shop").mkdir(parents=True)
    (project / "shop" / "__init__.py").write_text("")
    (project / "shop" / "pricing.py").write_text(
        "def price(item, discount=0):\n    return item\n"
    )
    return project


def test_a_delivery_calling_project_code_wrongly_is_verified_with_the_finding(
    db, tmp_path, monkeypatch, daemon
):
    monkeypatch.setenv("AUDIT_LOG_PATH", str(tmp_path / "audit"))
    project = _shop(tmp_path)
    item, agent, sprint = _task(db, str(project))
    nats = _DaemonOverNATS(daemon)

    asyncio.run(worker_deliver._deliver_artifacts(nats, item, agent, sprint, ANSWER))

    assert [subject for subject, *_ in nats.requests] == [
        "workspace.generate",
        "workspace.signatures",
    ]
    assert nats.requests[1][1]["wanted"] == [
        {"path": "shop/pricing.py", "names": ["price"]}
    ]
    verification = _verification(db, item["id"])
    assert verification.verdict == "verified"
    record = verification.evidence["signatures"]
    assert record["observed"] is True and record["files"][0]["sha256"]
    grounded = {c["name"]: c for c in verification.checks}["grounded"]
    assert grounded["outcome"] == "pass" and grounded["signatures_observed"] is True
    (finding,) = grounded["signature_mismatches"]
    assert finding["callee"] == "price" and finding["line"] == 5
    assert finding["problem"] == "takes at most 2 positional, 3 given"
    assert finding["signature"] == "price(item, discount=…)"
    assert finding["source"] == "daemon"
    assert finding["sha256"] == record["files"][0]["sha256"]


def test_an_older_daemon_leaves_the_signatures_not_observed(
    db, tmp_path, monkeypatch, daemon
):
    monkeypatch.setenv("AUDIT_LOG_PATH", str(tmp_path / "audit"))
    project = _shop(tmp_path)
    item, agent, sprint = _task(db, str(project))
    nats = _DaemonOverNATS(daemon, signatures=False)

    asyncio.run(worker_deliver._deliver_artifacts(nats, item, agent, sprint, ANSWER))

    verification = _verification(db, item["id"])
    assert verification.verdict == "verified"
    record = verification.evidence["signatures"]
    assert record["observed"] is False and "older daemon" in record["reason"]
    grounded = {c["name"]: c for c in verification.checks}["grounded"]
    assert grounded["signatures_observed"] is False
    assert grounded["signature_mismatches"] == []
    assert "older daemon" in grounded["observation"]


def test_a_delivery_that_was_not_written_asks_nothing():
    evidence = {"workspace_root": "/x", "workspace_writes": None}
    files = [{"path": "a.py", "content": "from b import f\nf()\n"}]

    class _Nats:
        async def request(self, *args, **kwargs):  # pragma: no cover - never called
            raise AssertionError("asked")

    record = asyncio.run(signature_lookup.observe(_Nats(), evidence, files))
    assert record["observed"] is False and evidence["signatures"] is record
    assert asyncio.run(signature_lookup.observe(_Nats(), None, files)) is None
    assert (
        asyncio.run(
            signature_lookup.observe(_Nats(), {}, [{"path": "a.js", "content": ""}])
        )
        is None
    )
