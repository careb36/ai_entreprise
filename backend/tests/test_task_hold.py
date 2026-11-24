"""What holds a task back, as the board shows it (issue #766, ``task_hold``).

The board said "waits for a person" for every blocked task the worker would
not move on its own, a task that only waits for another task included, and
never said why a task was held. ``observed.hold`` names the hold from the
task's record; ``waits_for_person`` is true only for the holds a person
releases.
"""

from __future__ import annotations

from services.tasks import task_events
from task_views_helpers import _company, _sprint, _task


def _cards(test_client, sprint):
    board = test_client.get(f"/v1/sprints/{sprint.id}/board").json()
    return {i["id"]: i for column in board["columns"].values() for i in column}


def _blocked(session, sprint, text, kind="blocked", **flags):
    item = _task(session, sprint)
    task_events.record(
        session, item, kind, "system", status="blocked", text=text, flags=flags
    )
    session.commit()
    return item


def test_each_hold_is_named_from_the_record(test_client, sqlite_session):
    company = _company(sqlite_session)
    sprint = _sprint(sqlite_session, company)
    first = _blocked(
        sqlite_session, sprint, "Blocked: manual triage needed.", needs_person=True
    )
    waits = _blocked(
        sqlite_session,
        sprint,
        f'Blocked: waits for task {first.id} ("x"), which waits for a person.',
        hold="dependency",
        explicit_block=True,
    )
    waits.depends_on = [first.id]
    sqlite_session.add(waits)
    retry = _blocked(
        sqlite_session,
        sprint,
        "The model call failed; retried once on its own.",
        retryable=True,
        explicit_block=False,
        needs_person=False,
        retry=False,
    )
    frozen = _blocked(sqlite_session, sprint, "Frozen by an operator.", frozen=True)
    risk = _blocked(
        sqlite_session,
        sprint,
        "Approved, held for a person: high risk tier.",
        kind="risk_hold",
    )
    queued = _task(sqlite_session, sprint, status="todo", depends_on=[first.id])
    windowed = _task(sqlite_session, sprint, status="todo")
    task_events.record(sqlite_session, windowed, "run_window_wait", "system")
    free = _task(sqlite_session, sprint, status="todo")
    sqlite_session.commit()

    cards = _cards(test_client, sprint)

    def hold(item):
        return cards[item.id]["observed"]["hold"]

    def waits_person(item):
        return cards[item.id]["observed"]["waits_for_person"]

    assert hold(first) == {"kind": "person", "reason": "Blocked: manual triage needed."}
    assert waits_person(first) is True
    # It waits for task `first`, not for a person: the worker moves it.
    assert hold(waits) == {"kind": "dependency", "waiting_for": [first.id]}
    assert waits_person(waits) is False
    assert hold(retry)["kind"] == "retry_pending"
    assert waits_person(retry) is False
    assert hold(frozen) == {"kind": "frozen"}
    assert waits_person(frozen) is True
    assert hold(risk)["kind"] == "risk_hold"
    assert "high risk tier" in hold(risk)["reason"]
    assert hold(queued) == {"kind": "dependency", "waiting_for": [first.id]}
    assert waits_person(queued) is False
    assert hold(windowed) == {"kind": "run_window"}
    assert hold(free) is None


def test_a_released_run_window_holds_nothing(test_client, sqlite_session):
    company = _company(sqlite_session)
    sprint = _sprint(sqlite_session, company)
    item = _task(sqlite_session, sprint, status="todo")
    task_events.record(sqlite_session, item, "run_window_wait", "system")
    task_events.record(sqlite_session, item, "run_window_release", "system")
    sqlite_session.commit()
    assert _cards(test_client, sprint)[item.id]["observed"]["hold"] is None


def test_a_paused_sprint_says_why_on_the_board(test_client, sqlite_session):
    company = _company(sqlite_session)
    sprint = _sprint(sqlite_session, company)
    sprint.status = "paused"
    sprint.pause_reason = (
        "No task of this sprint is done and 2 wait for a person: unblock, finish "
        "or discard them, then resume the sprint."
    )
    sqlite_session.add(sprint)
    sqlite_session.commit()
    board = test_client.get(f"/v1/sprints/{sprint.id}/board").json()
    assert board["sprint"]["pause_kind"] == "nothing_done"
    assert board["sprint"]["pause_reason"].startswith("No task of this sprint")
