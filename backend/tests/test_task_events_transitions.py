"""Each transition of a task records its events.

Each review-loop outcome, the worker's helpers, a dependency hold and its
release, a carry-over on both tasks, a sprint stopped and a credit pause, a
person's review, the ScrumMaster's decision and a person's change on the board.

A task's life as events (issue #324): ``services/task_events.record`` writes
every transition as an event and renders it as the board comment; the
projections over the events (``services/tasks/task_projection.py``) answer the
board's questions as the comment readers (``services/tasks/board.py``) do. The
shared rows and fakes are in ``task_events_helpers``.
"""

from __future__ import annotations

import asyncio

import pytest
import task_events_helpers
from models import AgentRun, BacklogItem, Verification
from services.learning import scrum_master
from services.loop import carry_over, run_review, sprint_control, task_worker
from services.loop.worker import board_writes, qa_review
from services.loop.worker import review as worker_review
from services.tasks import board, dependencies, task_events, task_projection
from task_events_helpers import (
    DELIVERABLE,
    _company,
    _FakeNATS,
    _headers,
    _item,
    _kinds,
    _sprint,
    assert_projection_agrees,
)

# The shared fixtures.
db = task_events_helpers.db
signing_key = task_events_helpers.signing_key


def _delivered_item(session, autonomy=2, verdict="verified", history=()):
    """A task whose whole thread was written through events, waiting for review."""
    company = _company(session, autonomy)
    sprint = _sprint(session, company)
    item = _item(session, sprint)
    for kind, actor, author, line in history:
        task_events.record(session, item, kind, actor, author=author, text=line)
    task_events.record(
        session,
        item,
        "delivered",
        "Developer",
        author="Dev",
        text=DELIVERABLE,
        status="in_review",
    )
    session.commit()
    if verdict is not None:
        run = AgentRun(
            company_id=company.id,
            external_ref=f"task-{item.id}",
            producer=task_worker.TASK_WORKER_PRODUCER,
            claims={"files": ["src/a.py"]},
            artifact_digest="d",
            artifact_size=1,
            status=verdict,
        )
        session.add(run)
        session.commit()
        session.add(
            Verification(
                company_id=company.id,
                agent_run_id=run.id,
                verdict=verdict,
                checks=[
                    {"name": "files_written", "outcome": verdict, "observation": "x"}
                ],
            )
        )
        session.commit()
    return item.id


def _review(monkeypatch, session, item_id, reply):
    title = session.get(BacklogItem, item_id).title

    def _answer(agent, prompt, **kwargs):
        if title not in prompt:
            return "APPROVED: not this test's task"
        if reply is None:
            raise RuntimeError("model down")
        return reply

    monkeypatch.setattr(qa_review, "_call_llm_for_review", _answer)
    asyncio.run(worker_review._review_cycle(_FakeNATS()))


GATE_LIMIT = qa_review.AUTOPILOT_MAX_REWORK_CYCLES
_GATE_REWORK = ("reviewed", "gate", "Verifier", "NEEDS WORK: delivery refuted — x")
_QA_REWORK = ("reviewed", "QA", "QA", "NEEDS WORK: add tests")

SCENARIOS = {
    "approved": (2, "verified", (), "APPROVED: complete", ["reviewed"], "done"),
    "risk_hold_at_autonomy_1": (
        1,
        "verified",
        (),
        "APPROVED: complete",
        ["reviewed", "risk_hold"],
        "blocked",
    ),
    "qa_rework": (
        2,
        "verified",
        (),
        "NEEDS WORK: add tests",
        ["reviewed", "sent_back"],
        "todo",
    ),
    "qa_rework_exhausted": (
        2,
        "verified",
        (_QA_REWORK,) * GATE_LIMIT,
        "NEEDS WORK: still no tests",
        ["reviewed", "blocked", "escalated"],
        "blocked",
    ),
    "gate_rework": (2, "refuted", (), "APPROVED: x", ["reviewed"], "todo"),
    "gate_exhausted": (
        2,
        "refuted",
        (_GATE_REWORK,) * GATE_LIMIT,
        "APPROVED: x",
        ["blocked", "escalated"],
        "blocked",
    ),
    "review_failed": (2, "verified", (), None, ["review_failed"], "in_review"),
}


@pytest.mark.parametrize("name", sorted(SCENARIOS))
def test_each_review_transition_records_its_events_and_the_projection_agrees(
    db, monkeypatch, name
):
    autonomy, verdict, history, reply, kinds, status = SCENARIOS[name]
    item_id = _delivered_item(db, autonomy, verdict, history)
    before = len(_kinds(db, item_id))

    _review(monkeypatch, db, item_id, reply)

    assert _kinds(db, item_id)[before:] == kinds
    db.expire_all()
    assert db.get(BacklogItem, item_id).status == status
    assert_projection_agrees(db, item_id)


def test_the_worker_helpers_record_the_kind_they_are_given(db):
    item = _item(db, _sprint(db, _company(db)))
    board_writes._update_item_status(item.id, "in_progress", kind="claimed")
    board_writes._update_item_status(
        item.id,
        "in_review",
        "deliverable",
        "Dev",
        kind="delivered",
        actor="Developer",
    )
    board_writes._update_item_status(
        item.id, "blocked", "Agent error: boom", "System", kind="blocked"
    )
    assert _kinds(db, item.id) == ["claimed", "delivered", "blocked"]
    [delivered] = [
        e for e in task_events.for_task(db, item.id) if e.event_type == "delivered"
    ]
    assert delivered.actor == "Developer"
    assert_projection_agrees(db, item.id)
    db.expire_all()
    # An agent error is retried once, then waits for a person.
    assert board.blocked_item_next_step(
        db.get(BacklogItem, item.id).comments, "Developer"
    ) in ("review", "retry")


def test_a_dependency_hold_and_its_release_are_events(db):
    sprint = _sprint(db, _company(db))
    stuck = _item(db, sprint, status="blocked")
    task_events.record(
        db,
        stuck,
        "blocked",
        "system",
        status="blocked",
        text="QA quality gate failed — task blocked pending manual triage.",
    )
    waiting = _item(db, sprint, status="todo", depends_on=[stuck.id])
    db.commit()

    dependencies.settle(db, sprint.id)
    db.commit()
    assert _kinds(db, waiting.id) == ["blocked"]
    assert waiting.status == "blocked"
    assert_projection_agrees(db, waiting.id)

    stuck = db.get(BacklogItem, stuck.id)
    task_events.record(
        db, stuck, "approved_by_person", "person", text="done by hand", status="done"
    )
    db.commit()
    dependencies.settle(db, sprint.id)
    db.commit()
    assert _kinds(db, waiting.id) == ["blocked", "released"]
    assert_projection_agrees(db, waiting.id)


def test_a_carry_over_is_an_event_on_both_tasks(db):
    company = _company(db)
    sprint = _sprint(db, company, status="completed")
    item = _item(db, sprint, status="blocked")
    task_events.record(
        db,
        item,
        "blocked",
        "system",
        status="blocked",
        text="QA quality gate failed — task blocked pending manual triage.",
    )
    db.commit()
    nxt = _sprint(db, company)

    [clone] = carry_over.carry_over(db, company.id, [item.id], nxt, "ana", "Manager")
    db.commit()

    # The confirmed relation between the attempts is on both chains (#533).
    assert _kinds(db, item.id) == ["blocked", "carried_over", "relation"]
    assert _kinds(db, clone.id) == ["carried_over", "relation"]
    directions = [
        e.payload["direction"]
        for e in task_events.for_task(db, item.id) + task_events.for_task(db, clone.id)
        if e.event_type == "carried_over"
    ]
    assert directions == ["to", "from"]
    assert_projection_agrees(db, item.id)
    assert_projection_agrees(db, clone.id)


def test_stopping_a_sprint_and_a_credit_pause_are_events(db, monkeypatch):
    company = _company(db)
    sprint = _sprint(db, company)
    item = _item(db, sprint, status="todo")
    other = _item(db, sprint, status="in_progress")

    assert sprint_control.stop(db, sprint) == 2
    db.commit()
    assert _kinds(db, item.id) == ["blocked"]
    assert _kinds(db, other.id) == ["blocked"]
    assert_projection_agrees(db, item.id)

    paused_sprint = _sprint(db, company)
    paused = _item(db, paused_sprint, status="in_progress")
    sprint_control.pause_task_for_credit(
        db.get_bind(), paused.id, paused_sprint.id, "deepseek"
    )
    assert _kinds(db, paused.id) == ["paused"]
    assert_projection_agrees(db, paused.id)


def test_a_transient_model_failure_queues_the_task_again_a_few_times(db):
    """A provider timeout or a dropped proxy call blocked the task for a
    person (sprint 3, tasks 12 and 15, issue #766); it is queued again, at
    most MAX_TRANSIENT_RETRIES times, and a real failure still blocks."""
    from services.loop.worker import execute, intake

    company = _company(db)
    sprint = _sprint(db, company)
    item = _item(db, sprint, status="in_progress")
    data = {"backlog_item_id": item.id, "sprint_id": sprint.id}
    timeout = execute.LLMCallError(
        "LLM call failed: deepseek 502 upstream_timeout: no complete answer within 300s"
    )
    for _ in range(intake.MAX_TRANSIENT_RETRIES):
        asyncio.run(intake._record_failure(_FakeNATS(), data, timeout))
        db.expire_all()
        assert db.get(BacklogItem, item.id).status == "todo"
    asyncio.run(intake._record_failure(_FakeNATS(), data, timeout))
    db.expire_all()
    assert db.get(BacklogItem, item.id).status == "blocked"
    assert _kinds(db, item.id) == ["retried"] * intake.MAX_TRANSIENT_RETRIES + [
        "blocked"
    ]
    assert_projection_agrees(db, item.id)

    other = _item(db, sprint, status="in_progress")
    refused = execute.LLMCallError("LLM call failed: deepseek 400 invalid request")
    asyncio.run(
        intake._record_failure(
            _FakeNATS(), {"backlog_item_id": other.id, "sprint_id": sprint.id}, refused
        )
    )
    db.expire_all()
    assert db.get(BacklogItem, other.id).status == "blocked"
    assert intake.is_transient_llm_failure(
        "LLM call failed: llm-proxy unreachable proxy_connection_lost: closed"
    )


def _task_run(session, item, company_id):
    run = AgentRun(
        company_id=company_id,
        external_ref=f"task-{item.id}",
        producer=task_worker.TASK_WORKER_PRODUCER,
        subject={"backlog_item_id": item.id},
        claims={},
        artifact_digest="d",
        artifact_size=1,
        status="verified",
    )
    session.add(run)
    session.commit()
    session.refresh(run)
    return run


def test_a_persons_review_records_its_events(db):
    company = _company(db, autonomy=1)
    sprint = _sprint(db, company)
    item = _item(db, sprint)
    task_events.record(
        db, item, "delivered", "Developer", author="Dev", text=DELIVERABLE
    )
    task_events.record(
        db, item, "reviewed", "QA", author="QA", text="APPROVED: ok", status="blocked"
    )
    task_events.record(
        db,
        item,
        "risk_hold",
        "system",
        text=f"{board.RISK_HOLD_PREFIX} (human-led). Mark it done to accept it.",
    )
    db.commit()
    run = _task_run(db, item, company.id)
    assert task_projection.awaits_risk_acceptance(db, item)

    review = run_review.submit_review(
        db,
        run,
        event="approve",
        body="fine",
        author_name="Ana",
        author_user_id=None,
    )
    db.commit()
    assert review.outcome == "accepted"
    assert _kinds(db, item.id)[-1] == "approved_by_person"
    assert_projection_agrees(db, item.id)

    # Changes requested on a task that waits for a person.
    held = _item(db, sprint)
    task_events.record(
        db, held, "delivered", "Developer", author="Dev", text=DELIVERABLE
    )
    task_events.record(
        db,
        held,
        "blocked",
        "system",
        status="blocked",
        text="QA quality gate failed — task blocked pending manual triage.",
    )
    db.commit()
    held_run = _task_run(db, held, company.id)
    review = run_review.submit_review(
        db,
        held_run,
        event="request_changes",
        body="rename it",
        author_name="Ana",
        author_user_id=None,
    )
    db.commit()
    assert review.outcome == "sent_back"
    assert _kinds(db, held.id)[-2:] == ["sent_back", "reviewed"]
    db.expire_all()
    assert task_projection.pending_rework_feedback(
        db, db.get(BacklogItem, held.id)
    ) == ["user: NEEDS WORK: review by Ana: rename it"]
    assert_projection_agrees(db, held.id)


def test_the_scrum_masters_decision_is_an_event_without_a_comment(db):
    company = _company(db)
    sprint = _sprint(db, company)
    item = _item(db, sprint, status="blocked")
    message = scrum_master.record_decision(
        db,
        company.id,
        sprint.id,
        item.id,
        item.assignee,
        {"decision": scrum_master.DECISION_RETRY, "reason": "r", "brief": "b"},
        answers=None,
    )
    db.commit()
    [event] = task_events.for_task(db, item.id)
    assert event.event_type == "decided" and event.actor == "ScrumMaster"
    assert event.payload["message_id"] == message.id
    assert not item.comments


def test_a_persons_change_on_the_board_is_an_event(test_client, db, signing_key):
    company = _company(db)
    item = _item(db, _sprint(db, company), status="blocked")
    headers = _headers(db, company.id, "Manager")

    response = test_client.patch(
        f"/v1/backlog/{item.id}",
        json={"status": "todo", "comment": f"{board.SEND_BACK_MARKER}: retry it"},
        headers=headers,
    )
    assert response.status_code == 200, response.text
    db.expire_all()
    [event] = task_events.for_task(db, item.id)
    assert event.event_type == "sent_back" and event.actor == "person"
    assert response.json()["comments"][-1]["event_id"] == event.id
    assert_projection_agrees(db, item.id)
