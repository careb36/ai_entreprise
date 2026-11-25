"""
Operator replay of failed or stuck outbox events (issue: harden async
task-worker observability and failure replay).

Acceptance criteria covered here, each named after its id:

- AC-replay-returns-id: the endpoint returns the replayed event id, resets the
  row to ``pending`` without losing its committed payload or correlation id,
  and only a support admin may call it.
- AC-replay-publishes: after replay, ``process_outbox`` republishes the same
  committed event exactly once and the poller marks it completed.
- AC-replay-no-new-task: a replay re-publishes the committed payload only; it
  never creates a second ``BacklogItem`` for the sprint the event was about.

NATS is faked in-process; the database is the shared SQLite session the other
outbox-facing tests use. No network or clock dependency: ``process_outbox`` is
advanced by a single iteration against a scripted client.
"""

import asyncio
import json
from datetime import UTC, datetime
from uuid import uuid4

import pytest
from fastapi import HTTPException
from models.sprint import BacklogItem, OutboxEvent, Sprint
from models.company import Company
from routers import outbox_admin
from services.integrations import outbox_service
from sqlmodel import select


class _RecordingNATS:
    """A NATS client that records publishes and never fails."""

    def __init__(self) -> None:
        self.published: list[dict] = []

    async def publish(self, subject, message, msg_id=None, headers=None):
        self.published.append(
            {
                "subject": subject,
                "message": message,
                "msg_id": msg_id,
                "headers": headers or {},
            }
        )


class _RaisingNATS:
    async def publish(self, subject, message, msg_id=None, headers=None):
        raise RuntimeError("nats: connection closed")


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


def _task_assign_event(session, sprint: Sprint, item: BacklogItem, **overrides) -> OutboxEvent:
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
    event = _task_assign_event(sqlite_session, sprint, item, status="failed", retry_count=5)

    # A support admin is authenticated by email.
    monkeypatch.setenv("SUPPORT_ADMIN_EMAILS", "ops@example.com")
    from config import Settings

    monkeypatch.setattr("config.settings", Settings())
    admin = _User("ops@example.com")

    result = asyncio.run(
        outbox_admin.replay_outbox_event(event.id, session=sqlite_session, user=admin)
    )

    assert result["event_id"] == event.id
    assert result["status"] == "pending"
    assert result["correlation_id"] == str(event.correlation_id)


def test_AC_replay_returns_id_refuses_a_non_admin(sqlite_session, monkeypatch):
    monkeypatch.setenv("SUPPORT_ADMIN_EMAILS", "ops@example.com")
    monkeypatch.setenv("AUTH_REQUIRED", "true")
    from config import Settings

    monkeypatch.setattr("config.settings", Settings())
    _company, sprint, item = _company_sprint_item(sqlite_session)
    event = _task_assign_event(sqlite_session, sprint, item, status="failed", retry_count=5)
    stranger = _User("someone@example.com")

    with pytest.raises(HTTPException) as excinfo:
        asyncio.run(
            outbox_admin.replay_outbox_event(event.id, session=sqlite_session, user=stranger)
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


def _drain_once(sqlite_session, monkeypatch, nats_client) -> None:
    """Run one iteration of ``process_outbox`` against the test session.

    The loop is infinite; ``asyncio.sleep`` is patched to stop it after the
    first body so the test observes exactly one pass with determinism.
    """

    class _Stop(Exception):
        pass

    async def _one_sleep(_seconds):
        raise _Stop

    monkeypatch.setattr(outbox_service.asyncio, "sleep", _one_sleep)
    monkeypatch.setattr(
        outbox_service, "Session", lambda _engine: _SessionWrapper(sqlite_session)
    )
    with pytest.raises(_Stop):
        asyncio.run(outbox_service.process_outbox(nats_client))


class _SessionWrapper:
    """A context manager returning the shared test session without closing it."""

    def __init__(self, session):
        self._session = session

    def __enter__(self):
        return self._session

    def __exit__(self, *exc):
        return False


def test_AC_replay_publishes_once_after_replay(sqlite_session, monkeypatch):
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

    nats = _RecordingNATS()
    _drain_once(sqlite_session, monkeypatch, nats)

    assert len(nats.published) == 1
    published = nats.published[0]
    assert published["subject"] == "task.assign"
    assert published["msg_id"] == f"outbox-{event.id}"
    assert published["headers"][outbox_service.CORRELATION_HEADER] == str(
        event.correlation_id
    )
    assert published["message"]["backlog_item_id"] == item.id
    assert published["message"]["correlation_id"] == str(event.correlation_id)

    sqlite_session.expire_all()
    assert sqlite_session.get(OutboxEvent, event.id).status == "completed"


def test_AC_replay_publishes_a_failing_delivery_stays_failed_after_five(
    sqlite_session, monkeypatch
):
    """A replay of a row that fails again is retried by the poller, then failed."""

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

    _drain_once(sqlite_session, monkeypatch, _RaisingNATS())
    sqlite_session.expire_all()
    retried = sqlite_session.get(OutboxEvent, event.id)
    assert retried.status == "pending"
    assert retried.retry_count == 1


# ---------------------------------------------------------------------------
# AC-replay-no-new-task
# ---------------------------------------------------------------------------


def test_AC_replay_no_new_task_creates_no_backlog_item(sqlite_session, monkeypatch):
    _company, sprint, item = _company_sprint_item(sqlite_session)
    before_items = sqlite_session.exec(
        select(BacklogItem).where(BacklogItem.sprint_id == sprint.id)
    ).all()
    event = _task_assign_event(sqlite_session, sprint, item, status="failed", retry_count=5)

    outbox_service.replay_event(sqlite_session, event.id)
    _drain_once(sqlite_session, monkeypatch, _RecordingNATS())

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
