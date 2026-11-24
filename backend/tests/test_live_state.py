"""What the Live page says is happening, from the records (issue #766).

``GET /v1/sprints/{id}/live_state``: who is working now (a run in flight,
only while the sprint runs), why a paused sprint is paused, when a closed
one closed, and each health tile from its record. The Live page read all of
it from the event stream before: sprint 8, completed with every task done,
showed two agents working and an open rework.
"""

from __future__ import annotations

from datetime import datetime

from models import Sprint
from models.progress import SprintProgressSnapshot
from services.loop import (
    daemon_gate,
    in_flight,
    live_state,
    preconditions,
    sprint_budget,
    sprint_close,
    sprint_control,
)
from services.platform import locks
from services.tasks import task_events
from task_views_helpers import _company, _sprint, _task


def _state(test_client, sprint):
    response = test_client.get(f"/v1/sprints/{sprint.id}/live_state")
    assert response.status_code == 200
    return response.json()


def _with_status(session, sprint, status, reason=None):
    sprint.status = status
    sprint.pause_reason = reason
    session.add(sprint)
    session.commit()
    return sprint


def test_an_active_sprint_shows_the_runs_in_flight(test_client, sqlite_session):
    company = _company(sqlite_session)
    sprint = _with_status(sqlite_session, _sprint(sqlite_session, company), "active")
    building = _task(sqlite_session, sprint, status="in_progress", assignee="Dev")
    reviewed = _task(sqlite_session, sprint, status="in_review", assignee="Dev")
    idle = _task(sqlite_session, sprint, status="in_progress", assignee="DevOps")
    in_flight.reset()
    engine = sqlite_session.get_bind()
    with (
        locks.held(engine, locks.TASK, building.id),
        in_flight.running(sprint.id, reviewed.id, "QA", in_flight.REVIEW),
    ):
        state = _state(test_client, sprint)
    after = _state(test_client, sprint)

    working = {(w["role"], w["task_id"], w["kind"]) for w in state["working"]}
    # The QA review in flight, and the task whose lock a replica holds; the
    # in-progress task nobody holds (a dead replica's) is not worked on.
    assert working == {("QA", reviewed.id, "review"), ("Dev", building.id, "task")}
    assert (idle.id) not in {w["task_id"] for w in state["working"]}
    assert state["running"] is True
    assert state["tiles"]["active_agents"] == 2
    # Once the runs end, nobody works.
    assert after["working"] == []
    assert after["tiles"]["active_agents"] == 0


def test_a_completed_sprint_has_nobody_working(test_client, sqlite_session):
    company = _company(sqlite_session)
    sprint = _with_status(sqlite_session, _sprint(sqlite_session, company), "completed")
    task = _task(sqlite_session, sprint, status="done", assignee="QA")
    sqlite_session.add(
        SprintProgressSnapshot(
            company_id=company.id,
            sprint_id=sprint.id,
            close_number=1,
            digest="sha256:0",
            created_at=datetime(2026, 11, 24, 14, 46, 40),
        )
    )
    sqlite_session.commit()
    engine = sqlite_session.get_bind()
    with (
        locks.held(engine, locks.TASK, task.id),
        in_flight.running(sprint.id, task.id, "QA", in_flight.REVIEW),
    ):
        state = _state(test_client, sprint)
    assert state["working"] == []
    assert state["running"] is False
    assert state["tiles"]["active_agents"] == 0
    assert state["closed_at"] == "2026-11-24T14:46:40"


def test_a_paused_sprint_has_nobody_working_and_says_why(test_client, sqlite_session):
    company = _company(sqlite_session)
    reason = sprint_close.nothing_done_reason(["blocked"])
    sprint = _with_status(
        sqlite_session, _sprint(sqlite_session, company), "paused", reason
    )
    task = _task(sqlite_session, sprint, status="in_progress", assignee="Dev")
    with in_flight.running(sprint.id, task.id, "Dev"):
        state = _state(test_client, sprint)
    assert state["working"] == []
    assert state["pause_kind"] == "nothing_done"
    assert state["pause_reason"] == reason
    assert state["closed_at"] is None


def test_every_pause_reason_has_its_kind():
    def kind(reason):
        return live_state.pause_kind("paused", reason)

    assert kind(None) == "person"
    assert kind("Paused by Ana") == "person"
    assert kind(sprint_control.credit_reason("deepseek")) == "no_credit"
    assert kind(sprint_close.nothing_done_reason(["blocked", "blocked"])) == (
        "nothing_done"
    )
    assert kind(sprint_budget.reason_reached(500, 400)) == "budget"
    assert kind(sprint_budget.reason_unknown("no price", 400)) == "budget"
    missing = [{"kind": "tests_off"}]
    assert kind(preconditions.pause_reason(missing)) == "acknowledgement"
    assert kind(daemon_gate.REASON) == "daemon"
    assert kind("Something else") == "other"
    assert live_state.pause_kind("active", "No credit at x") is None


def test_each_tile_comes_from_its_record(test_client, sqlite_session):
    company = _company(sqlite_session)
    earlier = _with_status(
        sqlite_session, _sprint(sqlite_session, company, "Before"), "completed"
    )
    _task(sqlite_session, earlier, status="done")
    _task(sqlite_session, earlier, status="blocked")
    sprint = _with_status(sqlite_session, _sprint(sqlite_session, company), "active")

    # A rework QA asked for and nobody delivered yet: open.
    reworked = _task(sqlite_session, sprint, status="in_review", assignee="Dev")
    task_events.record(
        sqlite_session,
        reworked,
        "reviewed",
        "QA",
        status="todo",
        text="NEEDS WORK: fix",
    )
    # A rework answered by the task being done: not open.
    answered = _task(sqlite_session, sprint, status="in_review", assignee="Dev")
    task_events.record(
        sqlite_session, answered, "reviewed", "QA", status="todo", text="NEEDS WORK: a"
    )
    task_events.record(
        sqlite_session, answered, "reviewed", "QA", status="done", text="APPROVED: ok"
    )
    blocked = _task(sqlite_session, sprint, status="todo")
    task_events.record(
        sqlite_session,
        blocked,
        "blocked",
        "system",
        status="blocked",
        text="Blocked: x",
    )
    task_events.record(
        sqlite_session, blocked, "review_failed", "system", text="QA review failed: y"
    )
    task_events.record(
        sqlite_session, blocked, "retried", "system", status="todo", text="Retry 1/3"
    )
    sqlite_session.commit()

    tiles = _state(test_client, sprint)["tiles"]

    assert tiles["open_reworks"] == 1
    assert tiles["blocked_tasks"] == 0  # the retry moved it back to todo
    assert tiles["agent_failures"] == 2
    # The newest event an agent (not the system, a gate or a person) wrote.
    assert tiles["last_agent_event_at"] is not None
    assert tiles["previous_sprint"] == {
        "id": earlier.id,
        "name": "Before",
        "done": 1,
        "total": 2,
    }
    assert tiles["daemon"]["state"] in {"online", "stale", "offline", "not_observed"}
    assert tiles["active_agents"] == 0


def test_a_sprint_with_no_agent_event_has_no_last_event(test_client, sqlite_session):
    company = _company(sqlite_session)
    sprint = _sprint(sqlite_session, company)
    task = _task(sqlite_session, sprint)
    task_events.record(sqlite_session, task, "claimed", "system", status="in_progress")
    sqlite_session.commit()
    tiles = _state(test_client, sprint)["tiles"]
    assert tiles["last_agent_event_at"] is None
    assert tiles["previous_sprint"] is None
    assert sqlite_session.get(Sprint, sprint.id) is not None


def test_an_unknown_sprint_is_not_found(test_client):
    assert test_client.get("/v1/sprints/999999/live_state").status_code == 404
