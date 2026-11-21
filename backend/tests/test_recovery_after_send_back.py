"""A person's send-back is not undone by the blocked-task recovery (issue #766).

After phase 0 the owner sent tasks 17 and 18 back to work; ten seconds later
the review cycle "recovered" them to review and the gate judged their old
delivery again, spending a rework of the new attempt's budget. The cycle's
session still held them as ``blocked``, and ``next_step`` read the delivery
from before the send-back as one to review.
"""

from __future__ import annotations

import asyncio

from models import BacklogItem
from services.loop.worker import review as worker_review
from services.tasks import board, task_events, task_projection
from sqlmodel import Session
from test_task_event_decisions import (
    DELIVERABLE,
    _company,
    _FakeNATS,
    _item,
    _sprint,
    db,  # noqa: F401 - the fixture
)


def _blocked_with_a_delivery(db):  # noqa: F811
    item = _item(db, _sprint(db, _company(db)))
    task_events.record(
        db,
        item,
        "delivered",
        "Developer",
        author="Dev",
        text=DELIVERABLE,
        status="in_review",
    )
    task_events.record(
        db,
        item,
        "blocked",
        "system",
        author="System",
        text="Stalled delivery",
        status="blocked",
    )
    db.commit()
    db.refresh(item)
    return item


def test_a_delivery_before_a_send_back_is_not_reviewed_again(db):  # noqa: F811
    item = _blocked_with_a_delivery(db)
    record = task_projection.record_of(db, item)
    assert record.next_step() == "review"

    task_events.record(
        db,
        item,
        "sent_back",
        task_events.PERSON,
        text="Sent back to work by a person: try again",
        status="todo",
    )
    task_events.record(
        db,
        item,
        "blocked",
        "system",
        author="System",
        text="Stalled again",
        status="blocked",
    )
    db.commit()
    db.refresh(item)
    assert task_projection.record_of(db, item).next_step() != "review"
    # The board's reading of the comments says the same.
    assert board.blocked_item_next_step(item.comments, item.assignee) != "review"


def test_a_send_back_during_the_cycle_is_not_undone(db, monkeypatch):  # noqa: F811
    """The person sends the task back after the cycle listed the blocked
    tasks and read their histories, before it reaches this one."""
    item = _blocked_with_a_delivery(db)
    real = worker_review.sprint_control.is_halted

    def send_back_now(status):
        with Session(db.get_bind()) as person:
            fresh = person.get(BacklogItem, item.id)
            if fresh.status == "blocked":
                task_events.record(
                    person,
                    fresh,
                    "sent_back",
                    task_events.PERSON,
                    text="Sent back to work by a person: again",
                    status="todo",
                )
                person.commit()
        return real(status)

    monkeypatch.setattr(worker_review.sprint_control, "is_halted", send_back_now)
    asyncio.run(worker_review._retry_blocked(db, _FakeNATS(), set()))

    db.expire_all()
    assert db.get(BacklogItem, item.id).status == "todo"
    kinds = [e.event_type for e in task_events.for_task(db, item.id)]
    assert "recovered" not in kinds
