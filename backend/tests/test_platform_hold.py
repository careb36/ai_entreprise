"""A delivery whose test command found no tests is held for a person, and
the sprint pauses once every delivery is (``services/loop/platform_hold.py``).

Run 2 of the self-hosting owner action (2026-11-24, #701: company 3, sprint
9): each delivery's test run found no tests, the gate sent each task back as
"NEEDS WORK: tests failed", and the run spent 2.1M tokens on reworks no agent
could make work. Here the same reply holds the task, spends no rework, asks
no ScrumMaster and pauses the sprint with the reason.
"""

import asyncio
from uuid import uuid4

import pytest
import worker_stages
from models.agent_run import AgentRun, Verification
from models.audit import AuditLog
from models.company import Company
from models.sprint import BacklogItem, Sprint
from services.evidence.verification import check_tests_run
from services.loop import live_state, platform_hold, task_worker
from services.loop.worker import messages, qa_review
from services.loop.worker import review as worker_review
from services.tasks import task_events, task_projection
from sqlmodel import Session, select

# Verification #117's test_run (run 2, task 38), as the daemon replied.
RUN2_TEST_RUN = {
    "command": "python -m pytest -p no:cacheprovider tests -v",
    "exit_code": 4,
    "output_tail": "rootdir: /workspace\ncollecting ... collected 0 items\n\n"
    "============================ no tests ran in 0.01s =============================\n"
    "ERROR: file or directory not found: tests\n\n",
}

DELIVERABLE = "# Artifact: backend/tests/test_x.py\n\nimport pytest\n" + "word " * 120


class _FakeNATS:
    jetstream_enabled = False

    def __init__(self):
        self.published = []

    async def publish(self, subject, payload, msg_id=None):
        self.published.append((subject, payload))

    async def request(self, subject, payload, timeout=5.0):
        raise TimeoutError


@pytest.fixture()
def db(monkeypatch, sqlite_session):
    worker_stages.use_engine(monkeypatch, sqlite_session.get_bind())
    return sqlite_session


def _sprint(session: Session) -> Sprint:
    company = Company(company_name=f"Hold {uuid4().hex[:6]}", autonomy_level=2)
    session.add(company)
    session.commit()
    sprint = Sprint(company_id=company.id, sprint_name="S", scope_size="S")
    session.add(sprint)
    session.commit()
    session.refresh(sprint)
    return sprint


def _delivered(session: Session, sprint: Sprint, test_run: dict) -> int:
    """A task QA delivered, verified with *test_run* as the daemon replied."""
    item = BacklogItem(
        sprint_id=sprint.id,
        title=f"Acceptance tests {uuid4().hex[:8]}",
        status="in_progress",
        assignee="QA",
        kind="acceptance_tests",
    )
    session.add(item)
    session.commit()
    task_events.record(
        session,
        item,
        "delivered",
        "QA",
        author="QA Fab",
        status="in_review",
        text=DELIVERABLE,
    )
    run = AgentRun(
        company_id=sprint.company_id,
        external_ref=f"task-{item.id}",
        producer=task_worker.TASK_WORKER_PRODUCER,
        claims={"files": ["backend/tests/test_x.py"]},
        artifact_digest="d",
        artifact_size=1,
        status="unverifiable",
    )
    session.add(run)
    session.commit()
    files = {
        "name": "files_written",
        "outcome": "pass",
        "observation": "1 file(s) written",
    }
    session.add(
        Verification(
            company_id=sprint.company_id,
            agent_run_id=run.id,
            verdict="unverifiable",
            checks=[files, check_tests_run({"test_run": dict(test_run)})],
        )
    )
    session.commit()
    return item.id


def _cycle(monkeypatch):
    asked = {"qa": 0, "triage": 0}
    monkeypatch.setattr(
        qa_review,
        "_call_llm_for_review",
        lambda *a, **k: asked.__setitem__("qa", asked["qa"] + 1),
    )
    monkeypatch.setattr(
        messages,
        "_triage_blocked_task",
        lambda *a, **k: asked.__setitem__("triage", asked["triage"] + 1),
    )
    nats = _FakeNATS()
    asyncio.run(worker_review._review_cycle(nats))
    return asked, nats


def test_run2s_delivery_is_held_for_a_person_and_the_sprint_pauses(db, monkeypatch):
    sprint = _sprint(db)
    item_id = _delivered(db, sprint, RUN2_TEST_RUN)

    asked, nats = _cycle(monkeypatch)

    db.expire_all()
    item, sprint = db.get(BacklogItem, item_id), db.get(Sprint, sprint.id)
    text = item.comments[-1]["text"]
    assert item.status == "blocked"
    assert not text.startswith("NEEDS WORK")
    assert "found no tests at tests" in text and "manual triage" in text
    assert asked == {"qa": 0, "triage": 0}
    record = task_projection.record_of(
        db, item, task_projection.histories_for(db, [item])
    )
    assert record.reworks() == 0  # no rework spent on configuration
    events = task_projection.histories_for(db, [item])[item.id]
    assert task_projection.next_step_from_events(events, "QA") is None  # nobody retries
    assert sprint.status == "paused"
    assert sprint.pause_reason.startswith(platform_hold.REASON_PREFIX)
    assert "found no tests at tests" in sprint.pause_reason
    assert (
        live_state.pause_kind(sprint.status, sprint.pause_reason) == "tests_not_found"
    )
    audits = db.exec(
        select(AuditLog).where(
            AuditLog.company_id == sprint.company_id,
            AuditLog.action == platform_hold.ACTION,
        )
    ).all()
    assert len(audits) == 1
    kinds = [p.get("semantic_type") for _s, p in nats.published if isinstance(p, dict)]
    assert "sprint.paused_tests_not_found" in kinds


def test_the_sprint_pauses_only_once_every_delivery_is_held(db, monkeypatch):
    sprint = _sprint(db)
    first = _delivered(db, sprint, RUN2_TEST_RUN)
    second = _delivered(db, sprint, RUN2_TEST_RUN)
    item = db.get(BacklogItem, first)

    assert (
        platform_hold.hold(db, item, sprint, "test command found no tests", "x")
        is False
    )
    assert db.get(Sprint, sprint.id).status == "active"
    paused = platform_hold.hold(
        db, db.get(BacklogItem, second), sprint, "test command found no tests", "x"
    )
    assert paused is True and db.get(Sprint, sprint.id).status == "paused"


def test_a_sprint_where_some_delivery_ran_its_tests_goes_on(db):
    sprint = _sprint(db)
    ran = _delivered(
        db,
        sprint,
        {"command": "pytest -q tests", "exit_code": 1, "output_tail": "1 failed"},
    )
    task_events.record(
        db,
        db.get(BacklogItem, ran),
        "reviewed",
        "gate",
        author="Verifier",
        status="todo",
        text="NEEDS WORK: tests failed — 1 failed.",
    )
    held = _delivered(db, sprint, RUN2_TEST_RUN)

    paused = platform_hold.hold(
        db, db.get(BacklogItem, held), sprint, "test command found no tests", "x"
    )
    assert paused is False
    assert db.get(Sprint, sprint.id).status == "active"
    assert db.get(BacklogItem, held).status == "blocked"
