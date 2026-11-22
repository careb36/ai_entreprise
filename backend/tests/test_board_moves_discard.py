"""A person discards a task (``PATCH /v1/backlog/{id}`` with ``discarded``,
``services/loop/board_moves.py``): the reason is the comment, only a task no
worker holds (To do or Blocked) can be discarded, the move is a ``discarded``
event from the person, the task counts as settled so its sprint can close, a
task depending on it is held for a person, and moving it to To do reopens it.
``history_check`` refuses a discarded task with no person's reason."""

import board_moves_helpers
import history_check
import pytest
from board_moves_helpers import _assignments, _company, _history, _item, _sprint
from models import BacklogItem, TaskEvent
from services.loop import board_moves
from services.tasks import dependencies, task_projection
from sqlmodel import select

db = board_moves_helpers.db

REASON = "Overlaps an existing feature; redefine it later."


def _discard(client, item, comment=REASON):
    return client.patch(
        f"/v1/backlog/{item.id}", json={"status": "discarded", "comment": comment}
    )


def _events(session, item):
    return session.exec(
        select(TaskEvent).where(TaskEvent.task_id == item.id).order_by(TaskEvent.id)
    ).all()


@pytest.mark.parametrize("status", ["blocked", "todo"])
def test_a_blocked_or_todo_task_is_discarded_with_its_reason(test_client, db, status):
    company = _company(db)
    sprint = _sprint(db, company, status="active")
    item = _item(db, sprint, status=status)

    response = _discard(test_client, item)

    assert response.status_code == 200, response.text
    assert response.json()["status"] == "discarded"
    db.expire_all()
    task = db.get(BacklogItem, item.id)
    last = _events(db, task)[-1]
    assert (last.event_type, last.task_status) == ("discarded", "discarded")
    assert last.payload["text"] == REASON and last.payload["author"] == "user"
    assert task_projection.is_settled(db, task)
    assert not task_projection.waits_for_a_person(db, task)  # not a block
    assert _assignments(db, item.id) == []  # never dispatched
    assert _history(db, task) == []


def test_the_reason_is_required(test_client, db):
    item = _item(db, _sprint(db, _company(db), status="active"))
    for comment in ("", "   "):
        response = _discard(test_client, item, comment)
        assert response.status_code == 422
        assert "why" in response.json()["detail"]
    db.expire_all()
    assert db.get(BacklogItem, item.id).status == "blocked"


@pytest.mark.parametrize("status", ["in_progress", "in_review", "done"])
def test_a_task_a_worker_may_hold_or_done_is_not_discarded(test_client, db, status):
    item = _item(db, _sprint(db, _company(db), status="active"), status=status)
    response = _discard(test_client, item)
    assert response.status_code == 409
    assert "To do or Blocked" in response.json()["detail"]
    db.expire_all()
    assert db.get(BacklogItem, item.id).status == status


def test_a_discarded_task_is_not_carried_over(test_client, db):
    company = _company(db)
    item = _item(db, _sprint(db, company, status="active"))
    _discard(test_client, item)
    target = _sprint(db, company, status="active", name="Sprint 1")
    response = test_client.post(
        f"/v1/backlog/{item.id}/carry_over", json={"target_sprint_id": target.id}
    )
    assert response.status_code == 409


def test_a_task_waiting_on_a_discarded_one_is_held_for_a_person(test_client, db):
    company = _company(db)
    sprint = _sprint(db, company, status="active")
    first = _item(db, sprint, status="blocked", title="First")
    second = _item(
        db, sprint, status="todo", comments=[], title="Second", depends_on=[first.id]
    )
    _discard(test_client, first)

    dependencies.settle(db, sprint.id)
    db.commit()
    db.expire_all()

    held = db.get(BacklogItem, second.id)
    assert held.status == "blocked"
    assert "waits for a person" in held.comments[-1]["text"]
    assert dependencies.sprint_settled(db, sprint.id)


def test_moving_a_discarded_task_to_todo_reopens_it(test_client, db):
    company = _company(db)
    sprint = _sprint(db, company, status="active")
    item = _item(db, sprint)
    _discard(test_client, item)

    response = test_client.patch(f"/v1/backlog/{item.id}", json={"status": "todo"})

    assert response.status_code == 200, response.text
    db.expire_all()
    assert db.get(BacklogItem, item.id).status == "todo"
    assert len(_assignments(db, item.id)) == 1


def test_the_history_checker_wants_a_persons_reason():
    task = {"id": 1, "status": "discarded", "comments": [], "depends_on": []}
    assert any(
        v.startswith("discarded_without_person")
        for v in history_check.check_task(task, [], [], {}, [])
    )
    comments = [{"author": "user", "text": REASON, "event_id": 7}]
    machine = [
        {
            "id": 7,
            "kind": "moved",
            "task_status": "discarded",
            "payload": {"author": "user", "text": REASON},
        },
    ]
    found = history_check.check_task(
        {**task, "comments": comments}, [], [], {}, machine
    )
    assert "discarded_without_event" in found
    person = [{**machine[0], "kind": "discarded"}]
    assert (
        history_check.check_task({**task, "comments": comments}, [], [], {}, person)
        == []
    )


def test_the_valid_statuses_name_it():
    assert board_moves.DISCARDED == "discarded"
