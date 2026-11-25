"""
Operator replay of failed or stuck outbox events (issue: harden async
task-worker observability and failure replay).

Acceptance criteria covered here, each named after its id:

- AC-replay-returns-id: the endpoint returns the replayed event id, resets the
  row to ``pending`` without losing its committed payload or correlation id,
  and only a support admin may call it.
- AC-replay-publishes: a replayed row is due for ``process_outbox`` at once
  (``pending``, ``retry_count`` 0), keeping its message, subject and correlation
  id; a row waiting out its backoff is replayable, a due one is not.
- AC-replay-no-new-task: a replay re-publishes the committed payload only; it
  never creates a second ``BacklogItem`` for the sprint the event was about.

The database is the shared SQLite session; the poller's own query is
PostgreSQL SQL, so these tests check the row state it selects on.
"""

import asyncio
from datetime import UTC, datetime
from uuid import uuid4

import pytest
from config import settings
from fastapi import HTTPException
from models.company import Company
from models.sprint import BacklogItem, OutboxEvent, Sprint
from routers import outbox_admin
from services.integrations import outbox_service
from sqlmodel import select


def _company_sprint_item(session) -> tuple[Company, Sprint, BacklogItem]:
    company = Company(company_name=f"Replay {uuid4().hex[:8]}", autonomy_level=2)
    session.add(company)
    session.commit()
    session.refresh(company)
    sprint = Sprint(company_id=company.id, sprint_name="R", scope_size="S")
    session.add(sprint)
    session.commit()
    session.refresh(sprint)
    item = BacklogItem(sprint_id=sprint.id, title="Replayed task", status="todo")
    session.add(item)
    session.commit()
    session.refresh(item)
    return company, sprint, item


def _task_assign_event(
    session, sprint: Sprint, item: BacklogItem, **overrides
) -> OutboxEvent:
    payload = {
        "subject": "task.assign",
        "message": {
            "sprint_id": sprint.id,
            "backlog_item_id": item.id,
            "role": "Dev",
        },
    }
    event = OutboxEvent(
        aggregate_type="backlog_item",
        aggregate_id=str(item.id),
        event_type="nats_publish",
        payload=payload,
        correlation_id=uuid4(),
        **overrides,
    )
    session.add(event)
    session.commit()
    session.refresh(event)
    return event


# ---------------------------------------------------------------------------
# AC-replay-returns-id
# ---------------------------------------------------------------------------


def test_AC_replay_returns_id_resets_a_failed_row(sqlite_session):
    _company, sprint, item = _company_sprint_item(sqlite_session)
    event = _task_assign_event(
        sqlite_session,
        sprint,
        item,
        status="failed",
        retry_count=5,
        processed_at=datetime.now(UTC).replace(tzinfo=None),
    )
    original_payload = event.payload
    correlation_id = event.correlation_id

    replayed = outbox_service.replay_event(sqlite_session, event.id)

    assert replayed.id == event.id
    assert replayed.status == "pending"
    assert replayed.retry_count == 0
    assert replayed.processed_at is None
    # The committed payload and correlation id are untouched.
    assert replayed.payload == original_payload
    assert replayed.correlation_id == correlation_id


def test_AC_replay_returns_id_via_endpoint(sqlite_session, monkeypatch):
    _company, sprint, item = _company_sprint_item(sqlite_session)
    event = _task_assign_event(
        sqlite_session, sprint, item, status="failed", retry_count=5
    )

    # A support admin is authenticated by email.
    monkeypatch.setattr(settings, "SUPPORT_ADMIN_EMAILS", "ops@example.com")
    admin = _User("ops@example.com")

    result = asyncio.run(
        outbox_admin.replay_outbox_event(event.id, session=sqlite_session, user=admin)
    )

    assert result["event_id"] == event.id
    assert result["status"] == "pending"
    assert result["correlation_id"] == str(event.correlation_id)


def test_AC_replay_returns_id_refuses_a_non_admin(sqlite_session, monkeypatch):
    monkeypatch.setattr(settings, "SUPPORT_ADMIN_EMAILS", "ops@example.com")
    monkeypatch.setattr(settings, "AUTH_REQUIRED", True)
    _company, sprint, item = _company_sprint_item(sqlite_session)
    event = _task_assign_event(
        sqlite_session, sprint, item, status="failed", retry_count=5
    )
    stranger = _User("someone@example.com")

    with pytest.raises(HTTPException) as excinfo:
        asyncio.run(
            outbox_admin.replay_outbox_event(
                event.id, session=sqlite_session, user=stranger
            )
        )
    assert excinfo.value.status_code == 403
    # The refused call left the row untouched.
    sqlite_session.expire_all()
    assert sqlite_session.get(OutboxEvent, event.id).status == "failed"


def test_AC_replay_returns_id_refuses_a_completed_row(sqlite_session):
    _company, sprint, item = _company_sprint_item(sqlite_session)
    event = _task_assign_event(
        sqlite_session,
        sprint,
        item,
        status="completed",
        processed_at=datetime.now(UTC).replace(tzinfo=None),
    )

    with pytest.raises(outbox_service.OutboxReplayError) as excinfo:
        outbox_service.replay_event(sqlite_session, event.id)
    assert excinfo.value.kind == "not_replayable"
    sqlite_session.expire_all()
    assert sqlite_session.get(OutboxEvent, event.id).status == "completed"


def test_AC_replay_returns_id_a_missing_event_is_404(sqlite_session):
    with pytest.raises(outbox_service.OutboxReplayError) as excinfo:
        outbox_service.replay_event(sqlite_session, 987654321)
    assert excinfo.value.kind == "not_found"


# ---------------------------------------------------------------------------
# AC-replay-publishes
# ---------------------------------------------------------------------------


def test_AC_replay_publishes_leaves_the_row_due_with_its_message(sqlite_session):
    _company, sprint, item = _company_sprint_item(sqlite_session)
    event = _task_assign_event(
        sqlite_session,
        sprint,
        item,
        status="failed",
        retry_count=5,
        processed_at=datetime.now(UTC).replace(tzinfo=None),
    )

    outbox_service.replay_event(sqlite_session, event.id)

    sqlite_session.expire_all()
    row = sqlite_session.get(OutboxEvent, event.id)
    # What process_outbox selects at once: pending, never retried.
    assert (row.status, row.retry_count, row.processed_at) == ("pending", 0, None)
    assert row.event_type == "nats_publish"
    assert row.payload["subject"] == "task.assign"
    assert row.payload["message"]["backlog_item_id"] == item.id
    assert row.correlation_id == event.correlation_id


def test_AC_replay_publishes_a_row_in_backoff_is_replayable(sqlite_session):
    _company, sprint, item = _company_sprint_item(sqlite_session)
    event = _task_assign_event(
        sqlite_session,
        sprint,
        item,
        status="pending",
        retry_count=3,
        processed_at=datetime.now(UTC).replace(tzinfo=None),
    )

    replayed = outbox_service.replay_event(sqlite_session, event.id)

    assert (replayed.status, replayed.retry_count, replayed.processed_at) == (
        "pending",
        0,
        None,
    )


def test_AC_replay_publishes_a_due_row_is_refused(sqlite_session):
    _company, sprint, item = _company_sprint_item(sqlite_session)
    event = _task_assign_event(sqlite_session, sprint, item, status="pending")

    with pytest.raises(outbox_service.OutboxReplayError) as excinfo:
        outbox_service.replay_event(sqlite_session, event.id)
    assert excinfo.value.kind == "not_replayable"


# ---------------------------------------------------------------------------
# AC-replay-no-new-task
# ---------------------------------------------------------------------------


def test_AC_replay_no_new_task_creates_no_backlog_item(sqlite_session):
    _company, sprint, item = _company_sprint_item(sqlite_session)
    before_items = sqlite_session.exec(
        select(BacklogItem).where(BacklogItem.sprint_id == sprint.id)
    ).all()
    event = _task_assign_event(
        sqlite_session, sprint, item, status="failed", retry_count=5
    )

    outbox_service.replay_event(sqlite_session, event.id)

    sqlite_session.expire_all()
    after_items = sqlite_session.exec(
        select(BacklogItem).where(BacklogItem.sprint_id == sprint.id)
    ).all()
    assert [i.id for i in after_items] == [i.id for i in before_items]
    assert len(after_items) == 1


# ---------------------------------------------------------------------------
# helpers
# ---------------------------------------------------------------------------


class _User:
    """The minimal signed-in user ``require_support_admin`` reads (its email)."""

    def __init__(self, email: str):
        self.id = 1
        self.email = email
        self.full_name = email
        self.password_hash = ""
