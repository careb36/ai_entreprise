"""The worker and the dependencies decide from a task's events (issue #324).

Once the events render a task's whole thread, every decision the loop takes
(review the deliverable or block it, how many rework attempts are spent,
whether a blocked task is retried or waits for a person, whether it is frozen,
a dependency hold, the carry-over, the ScrumMaster's prompt, the procedures'
numbers) reads the events through ``services/task_projection.TaskRecord``,
never the comment text: a comment altered after its event was written does
not change the decision. A thread the events do not cover (a legacy one) is
still read from its comments. A loop over many tasks loads their events in
one query.
"""

from __future__ import annotations

import asyncio
from uuid import uuid4

import pytest
from models import AgentRun, BacklogItem, Company, Sprint, Verification
from services import (
    carry_over,
    dependencies,
    procedures,
    scrum_master,
    task_events,
    task_projection,
    task_worker,
)
from sqlalchemy import text
from sqlmodel import Session

DELIVERABLE = "# Artifact: src/a.py\n\nprint('a')\n" + "word " * 120
TRIAGE = "QA quality gate failed — task blocked pending manual triage."


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
    monkeypatch.setattr(task_worker, "engine", sqlite_session.get_bind())
    monkeypatch.setattr(scrum_master, "ask_triage", lambda *a, **k: "")
    yield sqlite_session
    sqlite_session.rollback()
    sqlite_session.exec(
        text(
            "UPDATE sprints SET status = 'cancelled' WHERE company_id IN "
            "(SELECT id FROM companies WHERE company_name LIKE 'Dec %')"
        )
    )
    sqlite_session.commit()


def _company(session: Session, autonomy: int = 2) -> Company:
    company = Company(company_name=f"Dec {uuid4().hex[:6]}", autonomy_level=autonomy)
    session.add(company)
    session.commit()
    session.refresh(company)
    return company


def _sprint(session: Session, company: Company, status: str = "active") -> Sprint:
    sprint = Sprint(
        company_id=company.id, sprint_name="S", scope_size="S", status=status
    )
    session.add(sprint)
    session.commit()
    session.refresh(sprint)
    return sprint


def _item(session: Session, sprint: Sprint, **fields) -> BacklogItem:
    item = BacklogItem(
        sprint_id=sprint.id,
        title=fields.pop("title", f"Task {uuid4().hex[:8]}"),
        assignee=fields.pop("assignee", "Developer"),
        **fields,
    )
    session.add(item)
    session.commit()
    session.refresh(item)
    return item


def _kinds(session: Session, item_id: int) -> list[str]:
    return [e.event_type for e in task_events.for_task(session, item_id)]


def _alter(session: Session, item_id: int, index: int, **changes) -> None:
    """Rewrite one comment behind the events' back; its event id stays."""
    session.expire_all()
    item = session.get(BacklogItem, item_id)
    comments = [dict(c) for c in item.comments]
    comments[index].update(changes)
    item.comments = comments
    session.add(item)
    session.commit()


def _verified_run(session: Session, company: Company, item: BacklogItem) -> None:
    run = AgentRun(
        company_id=company.id,
        external_ref=f"task-{item.id}",
        producer=task_worker.TASK_WORKER_PRODUCER,
        claims={"files": ["src/a.py"]},
        artifact_digest="d",
        artifact_size=1,
        status="verified",
    )
    session.add(run)
    session.commit()
    session.add(
        Verification(
            company_id=company.id,
            agent_run_id=run.id,
            verdict="verified",
            checks=[{"name": "files_written", "outcome": "pass", "observation": "x"}],
        )
    )
    session.commit()


def _review(monkeypatch, session, item_id, reply, prompts=None):
    title = session.get(BacklogItem, item_id).title

    def _answer(agent, prompt, **kwargs):
        if title not in prompt:
            return "APPROVED: not this test's task"
        if prompts is not None:
            prompts.append(prompt)
        return reply

    monkeypatch.setattr(task_worker, "_call_llm_for_review", _answer)
    asyncio.run(task_worker._review_cycle(_FakeNATS()))


def _blocked_for_a_person(session, sprint, **fields) -> BacklogItem:
    item = _item(session, sprint, status="blocked", **fields)
    task_events.record(
        session, item, "blocked", "system", status="blocked", text=TRIAGE
    )
    session.commit()
    return item


# --- the review loop ----------------------------------------------------------


def test_qa_reviews_the_delivered_event_when_its_comment_was_altered(db, monkeypatch):
    company = _company(db)
    item = _item(db, _sprint(db, company))
    task_events.record(
        db,
        item,
        "delivered",
        "Developer",
        author="Dev",
        text=DELIVERABLE,
        status="in_review",
    )
    db.commit()
    _verified_run(db, company, item)
    # Read from the thread, this would be a rework request: nothing to review.
    _alter(db, item.id, 0, text="NEEDS WORK: forged after the fact")
    prompts: list[str] = []

    _review(monkeypatch, db, item.id, "APPROVED: complete", prompts)

    db.expire_all()
    assert db.get(BacklogItem, item.id).status == "done"
    assert prompts and "print('a')" in prompts[0]
    assert _kinds(db, item.id) == ["delivered", "reviewed"]


def test_the_rework_budget_is_counted_from_the_events(db, monkeypatch):
    company = _company(db)
    item = _item(db, _sprint(db, company))
    limit = task_worker.AUTOPILOT_MAX_REWORK_CYCLES
    for _ in range(limit):
        task_events.record(
            db, item, "reviewed", "QA", author="QA", text="NEEDS WORK: add tests"
        )
    task_events.record(
        db,
        item,
        "delivered",
        "Developer",
        author="Dev",
        text=DELIVERABLE,
        status="in_review",
    )
    db.commit()
    _verified_run(db, company, item)
    # Signed by someone else on the board, the requests would not count as QA's.
    for index in range(limit):
        _alter(db, item.id, index, author="Somebody")

    _review(monkeypatch, db, item.id, "NEEDS WORK: still no tests")

    db.expire_all()
    assert db.get(BacklogItem, item.id).status == "blocked"
    assert _kinds(db, item.id)[limit + 1 :] == ["reviewed", "blocked", "escalated"]


def test_a_blocked_task_waits_for_a_person_by_its_event(db, monkeypatch):
    sprint = _sprint(db, _company(db))
    item = _blocked_for_a_person(db, sprint)
    # Read from the thread, a transient agent error is retried once.
    _alter(db, item.id, 0, text="Agent error: boom")
    record = task_projection.record_of(db, db.get(BacklogItem, item.id))
    assert record.source == "events" and record.next_step() is None
    assert task_projection.TaskRecord(record.comments).next_step() == "retry"

    _review(monkeypatch, db, item.id, "APPROVED: x")

    db.expire_all()
    assert db.get(BacklogItem, item.id).status == "blocked"
    assert _kinds(db, item.id) == ["blocked"]
    assert dependencies.sprint_settled(db, sprint.id)


def test_a_legacy_thread_still_decides_by_its_comments(db, monkeypatch):
    sprint = _sprint(db, _company(db))
    item = _item(
        db,
        sprint,
        status="blocked",
        comments=[{"author": "System", "text": "Agent error: boom"}],
    )
    assert task_projection.record_of(db, item).source == "comments"

    _review(monkeypatch, db, item.id, "APPROVED: x")

    db.expire_all()
    assert db.get(BacklogItem, item.id).status == "todo"
    assert _kinds(db, item.id) == ["retried"]
    # One comment still names no event: the thread stays read from its comments.
    assert task_projection.record_of(db, db.get(BacklogItem, item.id)).source == (
        "comments"
    )


def test_a_task_frozen_by_its_event_stays_out_of_dispatch(db):
    sprint = _sprint(db, _company(db))
    item = _item(db, sprint, status="todo")
    task_events.record(
        db, item, "note", "person", text="Freeze after repeated sleep churn."
    )
    db.commit()
    _alter(db, item.id, 0, text="Carry on.")
    nats = _FakeNATS()

    asyncio.run(task_worker._dispatch_todo_items(nats))

    db.expire_all()
    assert db.get(BacklogItem, item.id).status == "blocked"
    assert not [p for s, p in nats.published if p.get("backlog_item_id") == item.id]


# --- dependencies, carry-over, triage, procedures ------------------------------


def test_a_dependency_hold_is_read_from_its_event(db):
    sprint = _sprint(db, _company(db))
    stuck = _blocked_for_a_person(db, sprint)
    waiting = _item(db, sprint, status="todo", depends_on=[stuck.id])
    dependencies.settle(db, sprint.id)
    db.commit()
    [hold] = task_events.for_task(db, waiting.id)
    assert hold.payload["hold"] == task_projection.DEPENDENCY_HOLD
    # The comment no longer says it is a hold; the event does.
    _alter(db, waiting.id, 0, text="Something else entirely.")
    stuck = db.get(BacklogItem, stuck.id)
    task_events.record(
        db, stuck, "approved_by_person", "person", text="done by hand", status="done"
    )
    db.commit()

    assert dependencies.settle(db, sprint.id)["released"] == [waiting.id]
    db.commit()
    assert _kinds(db, waiting.id) == ["blocked", "released"]


def test_the_carry_over_reads_what_blocked_the_task_from_its_events(db):
    company = _company(db)
    sprint = _sprint(db, company, status="completed")
    item = _blocked_for_a_person(db, sprint)
    _alter(db, item.id, 0, text="Agent error: boom")
    nxt = _sprint(db, company)

    [clone] = carry_over.carry_over(db, company.id, [item.id], nxt, "ana", "Manager")
    db.commit()

    assert TRIAGE in clone.comments[0]["text"]
    assert carry_over.carried_over(item, task_projection.record_of(db, item))


def test_the_triage_prompt_carries_the_rework_the_events_asked_for(db):
    item = _item(db, _sprint(db, _company(db)), status="blocked")
    task_events.record(
        db, item, "reviewed", "QA", author="QA", text="NEEDS WORK: add the tests"
    )
    db.commit()
    _alter(db, item.id, 0, text="nothing to see")

    prompt = scrum_master.triage_prompt(
        item, TRIAGE, task_record=task_projection.record_of(db, item)
    )

    assert "QA: NEEDS WORK: add the tests" in prompt
    assert "nothing to see" not in prompt


def test_loops_over_many_tasks_load_their_events_in_one_query(db, monkeypatch):
    company = _company(db, autonomy=3)
    closed = _sprint(db, company, status="completed")
    for _ in range(3):
        _blocked_for_a_person(db, closed)
    nxt = _sprint(db, company)
    calls = {"many": 0}
    real = task_events.for_tasks

    def counted(session, ids):
        calls["many"] += 1
        return real(session, ids)

    def refused(*_a, **_k):
        raise AssertionError("one query per task")

    monkeypatch.setattr(task_events, "for_tasks", counted)
    monkeypatch.setattr(task_events, "for_task", refused)

    assert dependencies.sprint_settled(db, closed.id)
    metrics = procedures.board_metrics(db, [closed.id])
    carried = carry_over.carry_over_on_the_companys_own(
        db, company.id, closed.id, nxt, room=5
    )
    db.commit()

    assert metrics["tasks"] == 3 and len(carried) == 3
    assert calls["many"] >= 3


def test_the_manager_agents_carry_over_mark_is_read_from_its_events(db):
    item = _blocked_for_a_person(db, _sprint(db, _company(db)))
    task_events.record(
        db,
        item,
        "note",
        "Manager agent",
        text=f"{carry_over.CARRY_MARK_PREFIX} under the company's policy.",
        payload={"carry_mark": True},
    )
    db.commit()
    _alter(db, item.id, 1, text="An ordinary note.")
    item = db.get(BacklogItem, item.id)
    assert carry_over.carry_mark(item) is None  # the thread alone no longer says
    assert carry_over.carry_mark(item, task_projection.record_of(db, item)) is True

    task_events.record(
        db,
        item,
        "note",
        "person",
        text=f"{carry_over.CARRY_UNMARK_PREFIX} by ana.",
        payload={"carry_mark": False},
    )
    db.commit()
    assert carry_over.carry_mark(item, task_projection.record_of(db, item)) is False


def test_qa_approval_and_a_persons_note_are_read_from_the_events(db):
    from datetime import UTC, datetime, timedelta

    from services import manager_inbox_extra

    item = _item(db, _sprint(db, _company(db)), status="done")
    task_events.record(
        db, item, "reviewed", "QA", author="QA", text="APPROVED: fine", status="done"
    )
    db.commit()
    _alter(db, item.id, 0, text="NEEDS WORK: forged")
    item = db.get(BacklogItem, item.id)
    record = task_projection.record_of(db, item)
    assert not manager_inbox_extra.qa_approved(item.comments)
    assert manager_inbox_extra.qa_approved(item.comments, record)

    before = datetime.now(UTC) - timedelta(minutes=1)
    assert not manager_inbox_extra.person_since(item.comments, before, record)
    task_events.record(db, item, "note", "person", text="Looked at it.")
    db.commit()
    record = task_projection.record_of(db, item)
    assert manager_inbox_extra.person_since(item.comments, before, record)
