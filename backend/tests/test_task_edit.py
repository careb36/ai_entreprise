"""A person edits a task's title or description (issue #766).

``PUT /v1/backlog/{id}/content``: a Manager narrows a task the agents could
not finish, with a reason, while it is in To do or Blocked; the change is
the task's ``edited`` event, chained with the others, with the text before
and after; ``send_back`` also sends it back to work.
"""

from __future__ import annotations

import board_moves_helpers
import pytest
from board_moves_helpers import _company, _item, _sprint
from models import AuditLog, BacklogItem
from services.loop import task_edit
from services.tasks import task_events
from sqlmodel import select

db = board_moves_helpers.db

REASON = "Narrowed after three failed attempts"


def _put(client, item_id, **body):
    return client.put(f"/v1/backlog/{item_id}/content", json={"reason": REASON, **body})


def test_a_blocked_task_is_edited_and_the_change_is_on_its_record(test_client, db):
    item = _item(db, _sprint(db, _company(db), status="active"), comments=[])
    response = _put(
        test_client,
        item.id,
        title="Only the SSO check",
        description="Reject fab_ tokens with 401.",
    )
    assert response.status_code == 200, response.text
    body = response.json()
    assert body["title"] == "Only the SSO check"
    assert body["description"] == "Reject fab_ tokens with 401."
    assert body["status"] == "blocked"  # editing alone does not start it
    assert body["changed"] == ["title", "description"]

    db.expire_all()
    stored = db.get(BacklogItem, item.id)
    assert stored.title == "Only the SSO check"
    event = task_events.for_task(db, item.id)[-1]
    assert event.event_type == "edited" and event.actor == task_events.PERSON
    assert event.payload["before"] == {"title": "Login", "description": "d"}
    assert event.payload["after"]["description"] == "Reject fab_ tokens with 401."
    assert event.payload["reason"] == REASON
    assert len(event.payload["description_sha256"]["after"]) == 64
    assert stored.comments[-1]["text"].startswith(
        "Task edited by a person (title, description)"
    )
    assert task_events.verify_chain(db, item.id)["status"] == "intact"
    audit = db.exec(
        select(AuditLog).where(AuditLog.action == task_edit.AUDIT_ACTION)
    ).all()
    assert len(audit) == 1


def test_send_back_starts_the_edited_task_with_a_new_budget(test_client, db):
    item = _item(db, _sprint(db, _company(db), status="active"), comments=[])
    response = _put(test_client, item.id, description="Smaller scope.", send_back=True)
    assert response.status_code == 200, response.text
    assert response.json()["status"] == "todo"
    kinds = [e.event_type for e in task_events.for_task(db, item.id)]
    assert kinds[-2:] == ["edited", "sent_back"]


@pytest.mark.parametrize("status", ["in_progress", "in_review", "done"])
def test_a_task_an_agent_is_using_or_done_is_not_edited(test_client, db, status):
    item = _item(
        db, _sprint(db, _company(db), status="active"), status=status, comments=[]
    )
    response = _put(test_client, item.id, title="Other")
    assert response.status_code == 409
    db.expire_all()
    assert db.get(BacklogItem, item.id).title == "Login"
    assert task_events.for_task(db, item.id) == []


@pytest.mark.parametrize(
    "body",
    [
        {"title": "Login", "description": "d"},  # nothing changes
        {"title": "   "},  # an empty title
        {"title": "x", "reason": "short"},  # no real reason
    ],
)
def test_an_edit_that_says_nothing_is_refused(test_client, db, body):
    item = _item(db, _sprint(db, _company(db), status="active"), comments=[])
    assert _put(test_client, item.id, **body).status_code == 422
    assert task_events.for_task(db, item.id) == []


def test_an_unknown_task_is_404(test_client, db):
    assert _put(test_client, 999_999, title="x").status_code == 404
