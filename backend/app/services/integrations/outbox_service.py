"""
Transactional outbox worker: polls ``outbox_events`` and publishes to NATS.

Responsibilities (one module, one thing): move committed rows from
``pending`` to ``completed`` (or ``failed``) through the configured
delivery path, with retries, concurrency-safe claim
(``FOR UPDATE SKIP LOCKED``) and a stable correlation id carried on every
attempt.

The row's ``event_type`` selects the delivery path the row committed to:

- ``nats_publish``        — publish the payload to a NATS subject, with the
  correlation id as a JetStream header and in the message body.
- ``webhook``             — POST the payload to the committed webhook URL.
- ``anchor``              — anchor the payload's digest with the audit anchor.
- ``chat_approval``       — notify the chat channel of a pending approval.
- ``chat_regression``     — notify the chat channel of a regression.
- ``person_notice``       — notify a person directly.

What this module never does: run domain logic, enqueue new rows, manage the
JetStream consumer, or recreate the sprint task for a replayed row. A row's
``correlation_id`` is generated exactly once
(``OutboxEvent.ensure_correlation_id``) and never rotated by the worker; a
replay resets only row state, so the same committed payload and id are
republished under the same ``Nats-Msg-Id``.

Interfaces (test-observable):

- ``process_outbox`` — infinite poll loop; one iteration per ``asyncio.sleep``.
- ``dispatch_event`` — deliver one committed row and persist its outcome.
- ``replay_event``     — reset a failed/pending row to ``pending`` (admin).
- ``OutboxReplayError`` — domain error with ``.kind`` and ``.reason``.
- ``CORRELATION_HEADER`` — the NATS header carrying the correlation id.
"""

import asyncio
import json
import logging
from datetime import UTC, datetime
from typing import Any

from database import engine
from models.sprint import OutboxEvent
from services.integrations import anchoring
from services.integrations import chat_approvals
from services.integrations import chat_regressions
from services.integrations import person_notices
from services.integrations import webhooks
from sqlmodel import Session, select

CORRELATION_HEADER = "X-Fabrique-Correlation-Id"
_MAX_RETRIES = 5

logger = logging.getLogger(__name__)


class OutboxReplayError(Exception):
    """The requested replay cannot be done: ``kind`` says why.

    ``kind`` is ``not_found`` (row missing) or ``not_replayable`` (completed
    or due pending); ``reason`` is the operator-facing explanation.
    """

    def __init__(self, kind: str, reason: str) -> None:
        super().__init__(reason)
        self.kind = kind
        self.reason = reason


def _correlation_id(event: OutboxEvent) -> str | None:
    """Ensure a stable id and return its string form, or None for legacy rows."""
    return str(event.ensure_correlation_id()) if event.ensure_correlation_id() else None


def _publish_message(nats_client, event: OutboxEvent):
    """Build and return the NATS publish coroutine for a ``nats_publish`` row.

    The message published is the committed ``event.payload`` dict; the
    correlation id travels as the header so JetStream consumers can trace the
    run end to end, and is set into the body for consumers that read only the
    body. The msg id stays ``outbox-<id>``: a replay of an already stored
    message is a JetStream de-dupe no-op (board note #11), so the same row is
    never delivered twice for the same payload.
    """
    payload = event.payload or {}
    subject = payload.get("subject", "")
    message = payload.get("message", payload)
    corr = _correlation_id(event)

    # The message body is what the worker consumes: keep the correlation id
    # inside it for consumers that do not read headers.
    if isinstance(message, dict) and corr:
        message.setdefault("correlation_id", str(corr))

    if not subject:
        # A malformed row cannot be published; let dispatch fail the row.
        raise ValueError("outbox row payload has no subject")

    _publish = getattr(nats_client, "publish", None)
    if _publish is None:
        raise RuntimeError("nats client has no publish method")

    headers = {CORRELATION_HEADER: str(corr)} if corr else None
    return _publish(subject=subject, message=message, msg_id=f"outbox-{event.id}", headers=headers)


def _await(publish_call) -> Any:
    """Return the publish call's result, awaiting it when it is a coroutine.

    The real ``NATSClient.publish`` is async; the test fakes are async too.
    Some webhook tests may register a synchronous callable, so both shapes
    are accepted: a plain callable's result is returned, a coroutine is
    awaited. This keeps the dispatch path single without forcing every fake
    to be async.
    """
    if asyncio.iscoroutine(publish_call):
        return asyncio.run(publish_call)
    return publish_call


async def dispatch_event(session: Session, event: OutboxEvent, nats_client=None) -> None:
    """Deliver one committed row and persist its outcome in the session.

    The delivery path is chosen by the row's committed ``event_type``; the
    caller provides ``nats_client`` for the ``nats_publish`` path.
    On success the row becomes ``completed`` with ``processed_at`` set; on a
    delivery failure the row is retried up to ``_MAX_RETRIES`` then ``failed``.
    Both the poller and any future replay path go through this single code
    path so delivery semantics are identical everywhere. The caller commits;
    this function only flushes state onto the session.
    """
    try:
        if event.event_type == "nats_publish":
            if nats_client is None:
                raise TypeError("dispatch_event requires nats_client for nats_publish")
            await _publish_message(nats_client, event)
        elif event.event_type in webhooks.EVENT_TYPES:
            _await(webhooks.deliver(session, event))
        elif event.event_type in anchoring.EVENT_TYPES:
            _await(anchoring.deliver(session, event))
        elif event.event_type in chat_approvals.EVENT_TYPES:
            _await(chat_approvals.deliver(session, event))
        elif event.event_type in chat_regressions.EVENT_TYPES:
            _await(chat_regressions.deliver(session, event))
        elif event.event_type in person_notices.EVENT_TYPES:
            _await(person_notices.deliver(session, event))
        else:
            raise ValueError(f"outbox event type not handled: {event.event_type}")
    except Exception:
        event.retry_count += 1
        if event.retry_count >= _MAX_RETRIES:
            event.status = "failed"
            event.processed_at = datetime.now(UTC).replace(tzinfo=None)
            logger.exception(
                "Outbox event %d permanently failed after %d attempts",
                event.id,
                event.retry_count,
            )
        else:
            event.status = "pending"
            logger.info(
                "Outbox event %d retry %d/%d failed; stays pending",
                event.id,
                event.retry_count,
                _MAX_RETRIES,
            )
        return

    event.status = "completed"
    event.processed_at = datetime.now(UTC).replace(tzinfo=None)
    event.retry_count = 0


def _poll_pending(session: Session) -> list[OutboxEvent]:
    """Claim pending rows with a row lock for concurrency-safe processing."""
    statement = (
        select(OutboxEvent)
        .where(OutboxEvent.status == "pending")
        .with_for_update(skip_locked=True)
        .limit(50)
    )
    return list(session.exec(statement).all())


async def process_outbox(nats_client) -> None:
    """Infinite poll loop: deliver every committed pending row.

    One body iteration delivers all currently pending rows. The loop is
    intentionally infinite and sleeps between iterations; an ``asyncio.sleep``
    is the test seam that stops one iteration deterministically (the replay
    test patches it to raise). The loop never catches ``KeyboardInterrupt``
    or ``SystemExit``: the infrastructure stops it by killing the process.
    """
    while True:
        with Session(engine) as session:
            events = _poll_pending(session)
            for event in events:
                await dispatch_event(session, event, nats_client)
            session.commit()
        await asyncio.sleep(2.0)


def replay_event(session: Session, event_id: int) -> OutboxEvent:
    """Reset a failed or stuck row to ``pending`` so the poller redelivers it.

    The row keeps its committed payload and correlation id; only
    ``status``, ``retry_count`` and ``processed_at`` are reset. A completed
    row (already delivered) or a pending row that is not due is refused with
    ``not_replayable``: there is nothing for a reset to change. A missing row
    is ``not_found``. The caller commits — the function only mutates the row.
    """
    event = session.get(OutboxEvent, event_id)
    if event is None:
        raise OutboxReplayError("not_found", f"outbox event {event_id} not found")

    if event.status == "completed":
        raise OutboxReplayError(
            "not_replayable",
            f"outbox event {event_id} is completed; nothing to replay",
        )
    if event.status == "pending" and event.processed_at is not None:
        # A pending row already being processed by another poller iteration.
        raise OutboxReplayError(
            "not_replayable",
            f"outbox event {event_id} is already pending and not due",
        )

    event.status = "pending"
    event.retry_count = 0
    event.processed_at = None
    return event
