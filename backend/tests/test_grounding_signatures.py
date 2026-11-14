"""The symbol part of the ``grounded`` check (issue #444): a call to a project
function or class with arguments its signature does not take is a finding.

The daemon's ``workspace.signatures`` (``vscode-daemon/signatures.py``) runs
for real against ``tmp_path``; the comparison
(``services/evidence/signatures.py``) reads only what the daemon reported
and the delivered text; the deliver stage records the daemon's reply
(``services/tools/signature_lookup.py``) through a fake NATS that runs the
real daemon.
"""

from __future__ import annotations

import asyncio
import importlib.util
import json
from pathlib import Path

import pytest
import signatures as daemon_signatures  # the daemon's module, on sys.path via conftest
import worker_stages
from models.agent_run import AgentRun, Verification
from models.company import Company, ProjectDiscovery
from models.sprint import BacklogItem, Sprint
from services.evidence import grounding
from services.evidence import signatures as symbol_check
from services.loop.worker import deliver as worker_deliver
from services.tools import signature_lookup
from sqlmodel import Session, select

_DAEMON_PATH = Path(__file__).resolve().parents[2] / "vscode-daemon" / "daemon.py"


def _load_daemon():
    spec = importlib.util.spec_from_file_location("fabrique_daemon_sig", _DAEMON_PATH)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


@pytest.fixture(scope="module")
def daemon():
    return _load_daemon()


# ---------------------------------------------------------------------------
# The daemon: workspace.signatures
# ---------------------------------------------------------------------------


LIB = """
import functools
from dataclasses import dataclass


def every_kind(a, /, b, c=1, *args, d, e=2, **kw):
    return a


def plain(x, y, password="hunter2-very-secret"):
    return x


async def later(x, *, retries=3):
    return x


class Point:
    def __init__(self, x, y=0):
        self.x = x


class Bare:
    pass


class Child(Point):
    pass


@dataclass
class Record:
    name: str


@functools.lru_cache
def cached(x):
    return x


def twice(x):
    return x


twice = functools.partial(twice, 1)

if True:
    def conditional(x):
        return x
"""


def _ask(daemon, root, wanted):
    return daemon_signatures.signatures(
        str(root), {"wanted": wanted}, daemon._resolve_target_path
    )


@pytest.fixture()
def project(tmp_path):
    (tmp_path / "pkg").mkdir()
    (tmp_path / "pkg" / "__init__.py").write_text("")
    (tmp_path / "pkg" / "lib.py").write_text(LIB)
    return tmp_path


def test_the_daemon_reports_each_parameter_kind(daemon, project):
    names = ["every_kind", "plain", "later"]
    row = _ask(daemon, project, [{"path": "pkg/lib.py", "names": names}])["files"][0]
    assert row["status"] == "read" and len(row["sha256"]) == 64
    kind = row["names"]["every_kind"]
    assert kind["status"] == "found" and kind["kind"] == "function"
    assert kind["positional_only"] == ["a"]
    assert kind["positional"] == ["b", "c"]
    assert kind["positional_defaults"] == 1
    assert kind["varargs"] is True and kind["varkw"] is True
    assert kind["keyword_only"] == [
        {"name": "d", "default": False},
        {"name": "e", "default": True},
    ]
    assert row["names"]["later"]["keyword_only"] == [
        {"name": "retries", "default": True}
    ]
    # Only whether a default exists, never its value.
    assert "hunter2" not in json.dumps(row)


def test_the_daemon_reports_constructors_and_leaves_guesses_unknown(daemon, project):
    wanted = [
        "Point",
        "Bare",
        "Child",
        "Record",
        "cached",
        "twice",
        "conditional",
        "nowhere",
    ]
    names = _ask(daemon, project, [{"path": "pkg/lib.py", "names": wanted}])["files"][
        0
    ]["names"]
    assert names["Point"]["status"] == "found" and names["Point"]["kind"] == "class"
    assert names["Point"]["positional"] == ["x", "y"]
    assert names["Point"]["positional_defaults"] == 1
    assert names["Bare"]["status"] == "found" and names["Bare"]["positional"] == []
    assert names["Child"]["status"] == "unknown"  # inherited constructor
    assert names["Record"]["status"] == "unknown"  # a decorator writes __init__
    assert names["cached"]["status"] == "unknown"  # decorated
    assert names["twice"]["status"] == "unknown"  # rebound
    assert names["conditional"]["status"] == "unknown"  # not a plain definition
    assert names["nowhere"] == {"status": "missing"}


def test_the_daemon_says_when_a_file_does_not_parse(daemon, project):
    (project / "pkg" / "broken.py").write_text("def f(:\n")
    row = _ask(daemon, project, [{"path": "pkg/broken.py", "names": ["f"]}])["files"][0]
    assert row["status"] == "does not parse" and row["sha256"]


def test_the_daemon_never_reads_secrets_symlinks_out_or_vcs(daemon, project, tmp_path):
    (project / "secrets.py").write_text("def key(): pass\n")
    outside = tmp_path.parent / f"{tmp_path.name}-outside.py"
    outside.write_text("def f(x): pass\n")
    (project / "out.py").symlink_to(outside)
    (project / ".git").mkdir()
    (project / ".git" / "hook.py").write_text("def f(x): pass\n")
    rows = _ask(
        daemon,
        project,
        [
            {"path": "secrets.py", "names": ["key"]},
            {"path": "out.py", "names": ["f"]},
            {"path": ".git/hook.py", "names": ["f"]},
            {"path": "../x.py", "names": ["f"]},
            {"path": "README.md", "names": ["f"]},
            {"path": "pkg/none.py", "names": ["f"]},
        ],
    )["files"]
    assert [row["status"] for row in rows] == [
        "refused",
        "refused",
        "refused",
        "refused",
        "refused",
        "unreadable",
    ]
    assert all("names" not in row for row in rows)


def test_the_daemon_refuses_a_request_over_its_bounds(daemon, project):
    from workspace_read import ReadError

    too_many_paths = [
        {"path": f"m{i}.py", "names": ["f"]}
        for i in range(daemon_signatures.MAX_PATHS + 1)
    ]
    with pytest.raises(ReadError) as paths:
        _ask(daemon, project, too_many_paths)
    assert paths.value.status == 400 and "paths" in paths.value.message
    too_many_names = [
        {"path": "pkg/lib.py", "names": [f"n{i}" for i in range(401)]},
    ]
    with pytest.raises(ReadError) as names:
        _ask(daemon, project, too_many_names)
    assert "names" in names.value.message
    assert daemon_signatures.MAX_PATHS == symbol_check.MAX_PATHS
    assert daemon_signatures.MAX_NAMES == symbol_check.MAX_NAMES


def test_the_daemon_answers_workspace_signatures_over_nats(daemon, project):
    assert (
        "workspace.signatures",
        daemon_signatures.handle_signatures,
    ) in daemon.signatures.HANDLERS
    replies = []

    class _Msg:
        def __init__(self, body):
            self.data = json.dumps(body).encode()
            self.reply = "inbox"

    async def reply(nc, msg, body):
        replies.append(body)

    async def run():
        for body in (
            {
                "workspace_root": str(project),
                "wanted": [{"path": "pkg/lib.py", "names": ["plain"]}],
            },
            {"wanted": []},
            {"workspace_root": str(project), "wanted": "nope"},
        ):
            await daemon_signatures.handle_signatures(
                None,
                _Msg(body),
                reply,
                daemon._request_body,
                daemon._resolve_target_path,
            )

    asyncio.run(run())
    assert replies[0]["files"][0]["names"]["plain"]["positional"] == [
        "x",
        "y",
        "password",
    ]
    assert replies[1]["error"]["status"] == 400
    assert replies[2]["error"]["status"] == 400


# ---------------------------------------------------------------------------
# The comparison
# ---------------------------------------------------------------------------


def _sig(**fields):
    base = {
        "status": "found",
        "kind": "function",
        "line": 1,
        "positional_only": [],
        "positional": [],
        "positional_defaults": 0,
        "varargs": False,
        "keyword_only": [],
        "varkw": False,
    }
    return {**base, **fields}


def _evidence(names, files=("pkg/lib.py", "pkg/__init__.py")):
    return {
        "project_files": {"files": list(files), "truncated": False},
        "signatures": {
            "observed": True,
            "files": [{"path": "pkg/lib.py", "sha256": "ab" * 32, "names": names}],
        },
    }


def _audit(code, names, path="app.py"):
    return symbol_check.audit(_evidence(names), [{"path": path, "content": code}])


def test_too_many_and_too_few_positionals_are_found():
    names = {"f": _sig(positional=["a", "b"], positional_defaults=1)}
    code = "from pkg.lib import f\n\nf(1, 2, 3)\nf()\nf(1)\nf(1, 2)\n"
    result = _audit(code, names)
    problems = [(m["line"], m["problem"]) for m in result["mismatches"]]
    assert problems == [
        (3, "takes at most 2 positional, 3 given"),
        (4, "missing 'a'"),
    ]
    assert result["checked"] == 4
    first = result["mismatches"][0]
    assert first["source"] == "daemon" and first["sha256"] == "ab" * 32
    assert first["defined_in"] == "pkg/lib.py" and first["signature"] == "f(a, b=…)"
    assert first["passed"] == {"positional": 3, "keywords": []}


def test_keywords_are_held_to_the_parameter_names():
    names = {
        "f": _sig(
            positional_only=["a"],
            positional=["b"],
            keyword_only=[
                {"name": "c", "default": False},
                {"name": "d", "default": True},
            ],
        )
    }
    code = (
        "from pkg.lib import f\n"
        "f(1, 2, c=3, d=4)\n"  # fine
        "f(1, b=2, c=3)\n"  # fine
        "f(1, 2, c=3, z=9)\n"  # unknown keyword
        "f(1, 2)\n"  # missing keyword-only
        "f(a=1, b=2, c=3)\n"  # positional-only by keyword
        "f(1, 2, b=2, c=3)\n"  # given twice
    )
    problems = {m["line"]: m["problem"] for m in _audit(code, names)["mismatches"]}
    assert problems == {
        4: "no parameter 'z'",
        5: "missing 'c'",
        6: "'a' is positional-only; missing 'a'",
        7: "'b' given twice",
    }


def test_star_args_kwargs_defaults_and_unpacking_are_accepted():
    names = {
        "g": _sig(positional=["a"], varargs=True, varkw=True),
        "h": _sig(positional=["a", "b"], positional_defaults=2),
    }
    code = (
        "from pkg.lib import g, h\n"
        "g(1, 2, 3, x=1, y=2)\n"
        "h()\nh(1)\nh(b=2)\n"
        "args = (1, 2, 3)\nh(*args)\nh(**{'z': 1})\n"
    )
    result = _audit(code, names)
    assert result["mismatches"] == []
    assert result["checked"] == 4  # the two unpacking calls are skipped


def test_a_rebound_or_shadowed_name_is_left_alone():
    names = {"f": _sig(positional=["a"])}
    rebound = "from pkg.lib import f\nf = print\nf(1, 2, 3)\n"
    shadowed = "from pkg.lib import f\n\ndef run(f):\n    f(1, 2, 3)\n"
    assert _audit(rebound, names)["mismatches"] == []
    assert _audit(shadowed, names)["mismatches"] == []


def test_unknown_and_missing_names_are_left_alone():
    names = {"f": {"status": "unknown", "kind": "function"}, "g": {"status": "missing"}}
    result = _audit("from pkg.lib import f, g\nf(1, 2, 3)\ng()\n", names)
    assert result["mismatches"] == [] and result["checked"] == 0


def test_module_imports_reach_the_definition():
    names = {"f": _sig(positional=["a"])}
    code = (
        "import pkg.lib\nimport pkg.lib as m\nfrom pkg import lib\n\n"
        "pkg.lib.f()\nm.f(1, 2)\nlib.f(a=1, b=2)\n"
    )
    result = symbol_check.audit(_evidence(names), [{"path": "app.py", "content": code}])
    assert [m["callee"] for m in result["mismatches"]] == ["pkg.lib.f", "m.f", "lib.f"]


def test_a_delivered_files_own_definitions_are_checked():
    files = [
        {
            "path": "pkg/tools.py",
            "content": "class Box:\n    def __init__(self, size):\n        pass\n\n"
            "def make(n, *, label):\n    return Box(n, n)\n",
        },
        {"path": "app.py", "content": "from pkg.tools import make\nmake(1)\n"},
    ]
    result = symbol_check.audit({"project_files": {"files": []}}, files)
    problems = sorted((m["path"], m["problem"]) for m in result["mismatches"])
    assert problems == [
        ("app.py", "missing 'label'"),
        ("pkg/tools.py", "takes at most 1 positional, 2 given"),
    ]
    assert all(m["source"] == "delivered" for m in result["mismatches"])


def test_a_relative_import_names_the_sibling_file():
    names = {"f": _sig(positional=["a"])}
    files = [{"path": "pkg/app.py", "content": "from .lib import f\nf()\n"}]
    result = symbol_check.audit(_evidence(names), files)
    assert result["mismatches"][0]["defined_in"] == "pkg/lib.py"


def test_a_standard_library_name_never_matches_a_nested_project_file():
    names = {"loads": _sig(positional=["a"])}
    evidence = _evidence(names, files=("pkg/json.py",))
    evidence["signatures"]["files"][0]["path"] = "pkg/json.py"
    result = symbol_check.audit(
        evidence, [{"path": "app.py", "content": "from json import loads\nloads()\n"}]
    )
    assert result["mismatches"] == [] and result["checked"] == 0


def test_wanted_asks_only_for_called_names_in_files_not_delivered_whole():
    files = [
        {
            "path": "app.py",
            "content": "from pkg.lib import f, g\nfrom pkg.mine import h\n"
            "f()\nh()\nprint(g)\n",
        },
        {"path": "pkg/mine.py", "content": "def h():\n    pass\n"},
    ]
    evidence = {
        "project_files": {"files": ["pkg/lib.py", "pkg/mine.py"], "truncated": False}
    }
    asked, capped = symbol_check.wanted(evidence, files)
    assert asked == [{"path": "pkg/lib.py", "names": ["f"]}] and capped is False


def test_wanted_keeps_within_the_daemons_bounds(monkeypatch):
    monkeypatch.setattr(symbol_check, "MAX_PATHS", 1)
    files = [
        {
            "path": "app.py",
            "content": "from pkg.a import f\nfrom pkg.b import g\nf()\ng()\n",
        }
    ]
    evidence = {"project_files": {"files": ["pkg/a.py", "pkg/b.py"]}}
    asked, capped = symbol_check.wanted(evidence, files)
    assert asked == [{"path": "pkg/a.py", "names": ["f"]}] and capped is True


def test_a_mismatch_is_a_finding_and_the_verdict_stays_verified(
    sqlite_session, test_client, monkeypatch, tmp_path
):
    monkeypatch.setenv("AUDIT_LOG_PATH", str(tmp_path / "audit"))
    company = Company(company_name="Signatures Corp")
    sqlite_session.add(company)
    sqlite_session.commit()
    sqlite_session.refresh(company)
    artifact = "```python path=app.py\nfrom pkg.lib import f\n\nf(1, 2, 3)\n```\n"
    run = test_client.post(
        "/v1/agent_runs",
        json={
            "company_id": company.id,
            "external_ref": "task-signatures",
            "producer": "le-fabrique-task-worker",
            "artifact": artifact,
            "claims": {"summary": "delivered app.py"},
        },
    ).json()
    response = test_client.post(
        f"/v1/agent_runs/{run['id']}/verify",
        json={
            "company_id": company.id,
            "evidence": _evidence({"f": _sig(positional=["a"])}),
        },
    ).json()
    checks = {check["name"]: check for check in response["checks"]}
    grounded = checks["grounded"]
    assert grounded["outcome"] == "pass"
    assert grounded["signatures_observed"] is True
    assert grounded["signature_mismatches"][0]["problem"] == (
        "takes at most 1 positional, 3 given"
    )
    assert "contradict a signature" in grounded["observation"]
    assert response["verdict"] == "verified"


def test_without_an_observation_the_check_says_so():
    files = [{"path": "app.py", "content": "from pkg.lib import f\nf(1, 2, 3)\n"}]
    evidence = {"project_files": {"files": ["pkg/lib.py"], "truncated": False}}
    check = grounding.check_grounded(evidence, files)
    assert check["outcome"] == "pass" and check["signatures_observed"] is None
    assert check["signature_mismatches"] == []
    assert "not compared" in check["observation"]
    evidence["signatures"] = {"observed": False, "reason": "no daemon answers it"}
    check = grounding.check_grounded(evidence, files)
    assert check["signatures_observed"] is False
    assert "no daemon answers it" in check["observation"]


def test_a_local_mismatch_alone_still_makes_a_check():
    files = [{"path": "app.py", "content": "def f(a):\n    pass\n\nf()\n"}]
    check = grounding.check_grounded({}, files)
    assert check["outcome"] == "pass"
    assert check["signature_mismatches"][0]["problem"] == "missing 'a'"
    assert grounding.check_grounded({}, [{"path": "a.py", "content": "x = 1\n"}]) is (
        None
    )


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
