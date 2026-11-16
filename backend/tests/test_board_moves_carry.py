""" "Move to sprint…" on the board (``services/loop/carry_over.py``): a blocked
(or To do) task carried into another open sprint and queued there, into a
planned one where it waits, or into a new sprint; the refusals (another
company's sprint, a task its sprint's acceptance tests check, a task waiting
for unfinished ones, a locked task or company); a carry re-reads the task and
the target under the company's lock, so it happens once. The shared rows are
in ``board_moves_helpers``.
"""

import board_moves_helpers
import pytest
from board_moves_helpers import _assignments, _company, _history, _item, _sprint
from models import (
    BacklogItem,
    Specification,
    Sprint,
)
from services.evidence import acceptance
from services.loop import board_moves, carry_over
from services.platform import locks
from sqlmodel import Session, select

# The shared fixtures.
db = board_moves_helpers.db


# ── Move to sprint… ──────────────────────────────────────────────────────────


def test_a_blocked_task_is_carried_into_an_open_sprint_and_queued_there(
    test_client, db
):
    company = _company(db)
    source = _sprint(db, company)
    target = _sprint(db, company, status="active", name="Sprint 1")
    _sprint(db, company, status="completed", name="Old")
    item = _item(db, source)

    targets = test_client.get(f"/v1/backlog/{item.id}/carry_targets").json()["sprints"]
    assert [t["id"] for t in targets] == [target.id]

    response = test_client.post(
        f"/v1/backlog/{item.id}/carry_over", json={"target_sprint_id": target.id}
    )

    assert response.status_code == 200, response.text
    body = response.json()
    assert (body["sprint_id"], body["queued"], body["created_sprint"]) == (
        target.id,
        True,
        False,
    )
    db.expire_all()
    clone = db.get(BacklogItem, body["backlog_item_id"])
    assert (clone.sprint_id, clone.status, clone.title) == (target.id, "todo", "Login")
    assert clone.comments[0]["text"].startswith(f"Carried over from task {item.id}")
    assert len(_assignments(db, clone.id)) == 1
    original = db.get(BacklogItem, item.id)
    assert carry_over.carried_over(original)
    assert _history(db, original) == [] and _history(db, clone) == []

    again = test_client.post(
        f"/v1/backlog/{item.id}/carry_over", json={"target_sprint_id": target.id}
    )
    assert again.status_code == 409
    assert "already carried" in again.json()["detail"]


def test_a_task_carried_into_a_planned_sprint_waits_for_it_to_start(test_client, db):
    company = _company(db)
    source = _sprint(db, company)
    target = _sprint(db, company, status="planned", name="Later")
    item = _item(db, source)

    body = test_client.post(
        f"/v1/backlog/{item.id}/carry_over", json={"target_sprint_id": target.id}
    ).json()

    assert (body["queued"], body["sprint_status"]) == (False, "planned")
    assert body["reason"] == "the sprint is planned: start it to run its tasks"


def test_without_a_target_the_task_gets_a_new_sprint(test_client, db):
    company = _company(db)
    source = _sprint(db, company, name="Hardening")
    item = _item(db, source)

    body = test_client.post(f"/v1/backlog/{item.id}/carry_over", json={}).json()

    assert (body["created_sprint"], body["queued"], body["sprint_status"]) == (
        True,
        True,
        "active",
    )
    db.expire_all()
    new = db.get(Sprint, body["sprint_id"])
    assert (new.company_id, new.sprint_name) == (company.id, "Carried from Hardening")


def test_a_todo_task_is_blocked_here_and_carried(test_client, db):
    company = _company(db)
    source = _sprint(db, company, status="paused")
    target = _sprint(db, company, status="active", name="Sprint 1")
    item = _item(
        db, source, status="todo", comments=[{"author": "Developer", "text": "draft"}]
    )

    body = test_client.post(
        f"/v1/backlog/{item.id}/carry_over", json={"target_sprint_id": target.id}
    ).json()

    assert body["queued"] is True
    db.expire_all()
    original = db.get(BacklogItem, item.id)
    assert original.status == "blocked" and carry_over.carried_over(original)
    # It stays for a person here even with a deliverable on its thread.
    assert carry_over.waits_for_a_person(original)
    assert _history(db, original) == []


@pytest.mark.parametrize(
    ("status", "target_status", "detail"),
    [
        ("done", "active", "Only a blocked or To do task"),
        ("blocked", "completed", "pick an open sprint"),
    ],
)
def test_carry_refusals(test_client, db, status, target_status, detail):
    company = _company(db)
    source = _sprint(db, company)
    target = _sprint(db, company, status=target_status, name="T")
    item = _item(db, source, status=status)

    response = test_client.post(
        f"/v1/backlog/{item.id}/carry_over", json={"target_sprint_id": target.id}
    )

    assert response.status_code == 409 and detail in response.json()["detail"]


def test_another_companys_sprint_is_not_a_target(test_client, db):
    source = _sprint(db, _company(db))
    foreign = _sprint(db, _company(db), status="active")
    item = _item(db, source)

    response = test_client.post(
        f"/v1/backlog/{item.id}/carry_over", json={"target_sprint_id": foreign.id}
    )

    assert response.status_code == 404


def _clones(session, target):
    session.expire_all()
    return session.exec(
        select(BacklogItem).where(BacklogItem.sprint_id == target.id)
    ).all()


def test_a_task_its_sprints_acceptance_tests_check_is_not_carried(test_client, db):
    company = _company(db)
    spec = Specification(company_id=company.id, title="Greeting", status="approved")
    db.add(spec)
    db.commit()
    source = _sprint(db, company)
    source.specification_id = spec.id
    db.add(source)
    db.commit()
    target = _sprint(db, company, status="active", name="Sprint 1")
    _item(
        db,
        source,
        status="done",
        comments=[],
        title="Acceptance tests: REQ-1",
        kind=acceptance.ACCEPTANCE_KIND,
        covers=["REQ-1.1"],
    )
    item = _item(db, source, covers=["REQ-1.1"])

    response = test_client.post(
        f"/v1/backlog/{item.id}/carry_over", json={"target_sprint_id": target.id}
    )

    assert response.status_code == 409, response.text
    assert "acceptance tests" in response.json()["detail"]
    assert _clones(db, target) == []
    assert not carry_over.carried_over(db.get(BacklogItem, item.id))


def test_a_todo_task_waiting_for_unfinished_tasks_is_not_carried(test_client, db):
    company = _company(db)
    source = _sprint(db, company, status="active")
    target = _sprint(db, company, status="active", name="Sprint 1")
    first = _item(db, source, status="in_progress", comments=[], title="Schema")
    item = _item(
        db, source, status="todo", comments=[], title="API", depends_on=[first.id]
    )

    response = test_client.post(
        f"/v1/backlog/{item.id}/carry_over", json={"target_sprint_id": target.id}
    )

    assert response.status_code == 409, response.text
    assert "not done" in response.json()["detail"]
    assert _clones(db, target) == []
    assert db.get(BacklogItem, item.id).status == "todo"


def test_a_carry_rereads_the_task_another_request_carried_meanwhile(db):
    company = _company(db)
    source = _sprint(db, company)
    target = _sprint(db, company, status="active", name="Sprint 1")
    item = _item(db, source)
    engine = db.get_bind()
    with Session(engine) as first, Session(engine) as second:
        # Both requests loaded the task before either carried it.
        stale = (second.get(BacklogItem, item.id), second.get(Sprint, source.id))
        board_moves.carry_to_sprint(
            first,
            first.get(BacklogItem, item.id),
            first.get(Sprint, source.id),
            target.id,
            "a",
        )
        with pytest.raises(board_moves.BoardMoveError) as refused:
            board_moves.carry_to_sprint(second, *stale, target.id, "b")
    assert refused.value.status == 409 and "already carried" in refused.value.detail
    assert len(_clones(db, target)) == 1


def test_a_carry_while_the_task_or_the_company_is_locked_is_refused(test_client, db):
    company = _company(db)
    source = _sprint(db, company)
    target = _sprint(db, company, status="active", name="Sprint 1")
    item = _item(db, source)
    engine = db.get_bind()
    url = f"/v1/backlog/{item.id}/carry_over"

    for name, ident in ((locks.TASK, item.id), (locks.SPRINT_CLOSE, company.id)):
        with locks.held(engine, name, ident) as mine:
            assert mine
            response = test_client.post(url, json={"target_sprint_id": target.id})
        assert response.status_code == 409, response.text
        assert "try again" in response.json()["detail"]
    assert _clones(db, target) == []

    assert (
        test_client.post(url, json={"target_sprint_id": target.id}).status_code == 200
    )


def test_the_target_is_read_again_under_the_companys_lock(db):
    company = _company(db)
    source = _sprint(db, company)
    target = _sprint(db, company, status="active", name="Sprint 1")
    item = _item(db, source)
    engine = db.get_bind()
    with Session(engine) as request, Session(engine) as worker:
        loaded = request.get(Sprint, target.id)  # the request saw it open
        assert loaded.status == "active"
        worker.get(Sprint, target.id).status = "completed"  # the worker closed it
        worker.commit()
        with pytest.raises(board_moves.BoardMoveError) as refused:
            board_moves.carry_to_sprint(
                request,
                request.get(BacklogItem, item.id),
                request.get(Sprint, source.id),
                target.id,
                "a",
            )
    assert refused.value.status == 409 and "completed" in refused.value.detail
    assert _clones(db, target) == []
