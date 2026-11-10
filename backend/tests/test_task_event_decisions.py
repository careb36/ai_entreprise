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
import worker_stages
from models import AgentRun, BacklogItem, Company, Sprint, Verification
from services import (
    board,
    carry_over,
    dependencies,
    procedures,
    scrum_master,
    sprint_control,
    task_events,
    task_projection,
    task_worker,
)
from services.loop.worker import intake, qa_review
from services.loop.worker import review as worker_review
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
    worker_stages.use_engine(monkeypatch, sqlite_session.get_bind())
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

    monkeypatch.setattr(qa_review, "_call_llm_for_review", _answer)
    asyncio.run(worker_review._review_cycle(_FakeNATS()))


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
    limit = qa_review.AUTOPILOT_MAX_REWORK_CYCLES
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

    asyncio.run(intake._dispatch_todo_items(nats))

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


# --- review findings ------------------------------------------------------------


def _risk_hold(session, item, reason="high tier"):
    task_events.record(
        session,
        item,
        "risk_hold",
        "system",
        status="blocked",
        text=f"{board.RISK_HOLD_PREFIX} ({reason}). Mark it done to accept it.",
    )
    session.commit()


def test_the_agents_acceptance_takes_the_chain_lock_before_claiming_the_row(
    db, monkeypatch
):
    from services import manager_actions
    from sqlalchemy import event

    item = _item(db, _sprint(db, _company(db, autonomy=1)), status="blocked")
    _risk_hold(db, item)
    order: list[str] = []
    real = task_events.lock_chain

    def spied(session, key):
        order.append(f"lock:{key}")
        real(session, key)

    def seen(conn, cursor, statement, *_a):
        if statement.lstrip().upper().startswith("UPDATE BACKLOG_ITEMS"):
            order.append("update")

    monkeypatch.setattr(task_events, "lock_chain", spied)
    bind = db.get_bind()
    event.listen(bind, "before_cursor_execute", seen)
    try:
        manager_actions._accept(db, item, "Accepted by the Manager agent.")
        db.commit()
    finally:
        event.remove(bind, "before_cursor_execute", seen)

    assert order[0] == f"lock:{item.id}"
    assert order.index("update") > 0
    assert db.get(BacklogItem, item.id).status == "done"


def test_an_integration_note_never_hides_a_risk_hold(db):
    from models.integration import SprintIntegration
    from services import integration_events

    sprint = _sprint(db, _company(db, autonomy=1))
    item = _item(db, sprint, status="blocked")
    _risk_hold(db, item, sprint_control.HUMAN_LED_REASON)
    # The agent accepts it, the sprint is integrated, the client overrides.
    task_events.record(
        db,
        item,
        "moved",
        "Manager agent",
        author="Manager agent",
        status="done",
        text="Accepted by the Manager agent.",
    )
    row = SprintIntegration(
        company_id=sprint.company_id,
        sprint_id=sprint.id,
        trigger="manual",
        requested_by="ana",
        base="main",
        branch=f"fabrique/sprint-{sprint.id}",
        status="untested",
        tasks=[{"task_id": item.id, "state": "merged", "merge_commit": "abc"}],
    )
    db.add(row)
    db.flush()
    assert integration_events.record_for_tasks(db, row) == [item.id]
    task_events.record(
        db,
        item,
        "moved",
        "person",
        status="blocked",
        text="Override: ana took back the Manager agent's acceptance.",
    )
    db.commit()
    item = db.get(BacklogItem, item.id)
    record = task_projection.record_of(db, item)

    assert record.source == "events"
    assert record.awaits_risk_acceptance()
    assert record.block_reason().startswith(board.RISK_HOLD_PREFIX)
    assert not carry_over.carried_over(item, record)
    # The comments say the same where the events do not cover the thread.
    assert board.awaits_risk_acceptance(item.comments)
    assert board.block_reason(item.comments).startswith(board.RISK_HOLD_PREFIX)
    legacy = task_projection.TaskRecord(item.comments, [], item.assignee)
    assert legacy.awaits_risk_acceptance()


def test_an_autonomy_one_hold_is_told_from_its_event(db):
    from services import manager_inbox

    company = _company(db, autonomy=1)
    item = _item(db, _sprint(db, company), status="blocked")
    _risk_hold(db, item, sprint_control.HUMAN_LED_REASON)
    _alter(db, item.id, 0, text=f"{board.RISK_HOLD_PREFIX} (high tier).")
    item = db.get(BacklogItem, item.id)
    record = task_projection.record_of(db, item)

    entry = manager_inbox._task_item(db, company, item, None, record)

    assert entry["facts"]["human_led_hold"] is True
    assert entry["category"] == "accept_delivery"


def test_a_backfilled_carry_over_event_still_tells_its_direction(db):
    company = _company(db)
    sprint = _sprint(db, company, status="completed")
    original = _item(db, sprint, status="blocked")
    clone = _item(db, _sprint(db, company))
    # As migration 0014 wrote them: the comment's text, no direction.
    for item, text_ in (
        (original, f'{board.CARRIED_TO_PREFIX} 9 ("S") as task {clone.id} by ana.'),
        (clone, f'{board.CARRIED_FROM_PREFIX} task {original.id} ("S"), blocked.'),
    ):
        task_events.record(
            db,
            item,
            "carried_over",
            "system",
            text=text_,
            payload={"backfilled": True},
        )
    db.commit()
    original = db.get(BacklogItem, original.id)
    clone = db.get(BacklogItem, clone.id)
    first = task_projection.record_of(db, original)
    second = task_projection.record_of(db, clone)

    assert first.source == second.source == "events"
    assert carry_over.carried_over(original, first)
    assert not carry_over.is_carry_over(original, first)
    assert carry_over.is_carry_over(clone, second)
    assert not carry_over.carried_over(clone, second)


def test_histories_survive_a_commit_without_a_query_per_task(db):
    from sqlalchemy import event

    sprint = _sprint(db, _company(db))
    items = [_blocked_for_a_person(db, sprint) for _ in range(3)]
    histories = task_projection.histories_for(db, items)
    db.commit()  # what the review loop does after each task
    queried: list[str] = []

    def seen(conn, cursor, statement, *_a):
        if "task_events" in statement:
            queried.append(statement)

    bind = db.get_bind()
    event.listen(bind, "before_cursor_execute", seen)
    try:
        for item in items:
            record = task_projection.record_of(db, item, histories)
            assert record.source == "events"
            assert record.is_settled("blocked")
    finally:
        event.remove(bind, "before_cursor_execute", seen)

    assert queried == []
