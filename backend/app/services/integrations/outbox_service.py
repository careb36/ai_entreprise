"""
Transactional Outbox processor for reliable NATS message delivery.

Background worker that continuously polls the ``outbox_events`` table for
pending rows and publishes them to NATS, ensuring at-least-once delivery.
For durable subjects (``task.assign``) a row is marked completed only after
JetStream has stored the message, so it reaches the task worker even if the
worker is down at that moment.

Why the outbox pattern?
-----------------------
A naive approach (publish to NATS inside the HTTP handler) risks data loss if
the process crashes after the database transaction commits but before the NATS
message is sent.  The outbox pattern solves this by writing the event to the
database **in the same transaction** as the domain change, then delivering it
asynchronously from here.

Concurrency safety:
------------------
``SELECT ... FOR UPDATE SKIP LOCKED`` acquires row-level locks so that multiple
worker replicas running in Kubernetes never process the same event simultaneously.

Correlation:
------------
Each row carries a ``correlation_id`` (a UUID) generated exactly once at
enqueue time. It is never regenerated on retry or redelivery, and it is
distinct from ``Nats-Msg-Id`` which keeps its dedup role. The id rides in the
published header and payload, so the worker can set the same id on its spans
and on the ``AgentRun`` it creates.

Operator replay:
----------------
A row that exhausted its retries (``status='failed'``) or a row stuck
``pending`` past its retry window would otherwise need a person to edit the
database or the NATS stream by hand. ``replay_event`` returns such a row to a
pollable state under a row lock and lets ``process_outbox`` republish its
**committed** payload; it never invokes the domain logic that first created the
event, so no second backlog row or task is produced. The ``Nats-Msg-Id`` keeps
a replay of an already-committed durable message a no-op.

Retry policy:
-------------
- Up to 5 delivery attempts per event.
- After 5 failures the row is marked ``"failed"`` and excluded from future polls.
- A ``retry_count`` column tracks the number of failed attempts.
"""

import asyncio
import json
import uuid
from typing import Any

from database import engine
from logging_config import logger
from models.sprint import OutboxEvent
from sqlmodel import Session, select, text

from services.integrations import (
    anchoring,
    chat_approvals,
    chat_regressions,
    person_notices,
    webhooks,
)

# Header name consumed by the task worker (services/loop/worker/intake.py).
CORRELATION_HEADER = "X-Correlation-Id"

# Statuses an operator may replay: a row that gave up (failed) or one that is
# still pending but stuck (the poller never surfaced it). A completed row holds
# nothing left to deliver.
REPLAYABLE_STATUSES = ("failed", "pending")


class OutboxReplayError(Exception):
    """A replay was refused; ``reason`` says what to do about it.

    ``kind`` is ``"not_found"`` (no such row), ``"not_replayable"`` (the row
    completed, so its message was already delivered) or ``"already_pending"``
    (the row is pending and already due, so the poller will publish it on its
    own without an operator reset).
    """

    def __init__(self, kind: str, reason: str) -> None:
        super().__init__(reason)
        self.kind = kind
        self.reason = reason


def enqueue_outbox_event(
    session: Session,
    event_type: str,
    payload: dict[str, Any],
    *,
    correlation_id: uuid.UUID | None = None,
) -> OutboxEvent:
    """Persist one outbox row and return it, correlation id assigned exactly once.

    Call inside the same transaction as the domain change that triggers the
    publish, so the two commit atomically. A caller that generated the id
    upstream (e.g. when a task.assign is produced) keeps its own value;
    otherwise one is created here.
    """
    event = OutboxEvent(
        event_type=event_type,
        payload=payload,
        correlation_id=correlation_id or uuid.uuid4(),
    )
    session.add(event)
    session.flush()
    logger.debug(
        "Outbox enqueued event_type=%s correlation_id=%s",
        event_type,
        event.correlation_id,
    )
    return event


async def dispatch_event(
    session: Session,
    nats_client,
    event: OutboxEvent,
    payload: dict[str, Any],
    correlation_id: uuid.UUID,
) -> None:
    """Deliver one already-loaded outbox row to its destination.

    The single place that knows how each ``event_type`` is delivered: the
    NATS publish branch and the per-channel ``deliver`` functions. It publishes
    the committed ``payload`` and nothing else, so the poller and the operator
    replay (``replay_event``) share one code path. It does not mark the row;
    the caller decides completion and retry accounting. Raising signals a
    delivery failure the caller must record (``process_outbox``).
    """
    if event.event_type == "nats_publish":
        subject = payload["subject"]
        message = payload["message"]
        # Stable id travels with the message so the worker can set it on
        # spans and AgentRun. Never regenerate.
        message["correlation_id"] = str(correlation_id)
        logger.info(
            "Outbox dispatching event_id=%s subject=%s sprint_id=%s item_id=%s correlation_id=%s",
            event.id,
            subject,
            message.get("sprint_id"),
            message.get("backlog_item_id"),
            correlation_id,
        )
        # Publish the inner message to the specified NATS subject. Durable
        # subjects return only once JetStream has stored the message; the id
        # makes a retry after a lost acknowledgement a no-op.
        await nats_client.publish(
            subject,
            message,
            msg_id=f"outbox-{event.id}",
            headers={CORRELATION_HEADER: str(correlation_id)},
        )
    elif event.event_type == webhooks.EVENT_TYPE:
        await webhooks.deliver(session, payload, event.id)
    elif event.event_type in anchoring.EVENT_TYPES:
        # Audit anchors (issue #542): taken, posted or timestamped.
        await anchoring.deliver(
            session,
            event.event_type,
            payload,
            event.id,
            event.retry_count,
        )
    elif event.event_type in chat_approvals.EVENT_TYPES:
        await chat_approvals.deliver(session, event.event_type, payload, event.id)
    elif event.event_type in chat_regressions.EVENT_TYPES:
        # Regressions, information only (issue #682).
        await chat_regressions.deliver(session, payload, event.id)
    elif event.event_type in person_notices.EVENT_TYPES:
        # A personal notification's email copy (issue #683).
        await person_notices.deliver_email(session, payload)


def replay_event(session: Session, event_id: int) -> OutboxEvent:
    """Return a failed or stuck outbox row to a pollable state; return it.

    Runs under ``SELECT ... FOR UPDATE`` so it is atomic against the running
    ``process_outbox`` poller: a row the poller holds is not reset mid-publish,
    and a row reset here is not double-published. The row's committed
    ``payload`` and ``correlation_id`` are untouched — only the delivery
    bookkeeping is reset — so the next poll republishes the same event with the
    same ``Nats-Msg-Id`` and no domain logic runs again.

    Refuses a completed row (its message was already delivered) and a row that
    is pending and already due (the poller will publish it without a reset).
    """
    event = session.exec(
        select(OutboxEvent)
        .where(OutboxEvent.id == event_id)
        .with_for_update()
    ).first()
    if event is None:
        raise OutboxReplayError("not_found", f"outbox event {event_id} not found")
    if event.status == "completed":
        raise OutboxReplayError(
            "not_replayable",
            f"outbox event {event_id} is completed; its message was already delivered",
        )
    if event.status == "pending" and event.processed_at is None:
        # Due now: the poller's first branch (retry_count == 0) will pick it up.
        raise OutboxReplayError(
            "already_pending",
            f"outbox event {event_id} is pending and due; the poller will publish it",
        )
    if event.status not in REPLAYABLE_STATUSES:
        raise OutboxReplayError(
            "not_replayable",
            f"outbox event {event_id} has status {event.status!r} and cannot be replayed",
        )
    session.exec(
        text(
            """
            UPDATE outbox_events
            SET status = 'pending', processed_at = NULL, retry_count = 0
            WHERE id = :id
            """
        ).bindparams(id=event.id)
    )
    logger.info(
        "Outbox replay requested event_id=%s correlation_id=%s previous_status=%s",
        event.id,
        event.ensure_correlation_id(),
        event.status,
    )
    session.commit()
    session.refresh(event)
    return event


async def process_outbox(nats_client) -> None:
    """
    Background coroutine that drains the ``outbox_events`` table into NATS.

    Intended to run for the entire application lifetime as an ``asyncio.Task``
    (started in ``main.startup``).

    Args:
        nats_client: Active ``NATSClient`` instance used for publishing.
                     The loop sleeps and retries until the client is available.
    """
    while True:
        # Wait until the NATS client is ready (e.g. after application startup).
        if not nats_client:
            await asyncio.sleep(2)
            continue

        try:
            with Session(engine) as session:
                # Fetch up to 10 pending events; SKIP LOCKED prevents worker conflicts.
                # Select the row objects, not raw tuples, so correlation_id is
                # available to the publish step without a second round-trip.
                rows = session.exec(
                    select(OutboxEvent)
                    .where(
                        OutboxEvent.status == "pending",
                        (
                            (OutboxEvent.retry_count == 0)
                            | (OutboxEvent.processed_at.is_(None))
                            | (
                                OutboxEvent.processed_at
                                < text(
                                    "NOW() - (retry_count * retry_count) * INTERVAL '10 seconds'"
                                )
                            )
                        ),
                    )
                    .order_by(OutboxEvent.created_at)
                    .with_for_update(skip_locked=True)
                    .limit(10)
                ).all()

                if rows:
                    logger.debug("Outbox: processing %d pending event(s)", len(rows))

                for event in rows:
                    try:
                        payload = (
                            json.loads(event.payload)
                            if isinstance(event.payload, str)
                            else event.payload
                        )
                        correlation_id = event.ensure_correlation_id()

                        # One helper knows how each event type is delivered, so
                        # the operator replay reuses the exact same publish path.
                        await dispatch_event(
                            session, nats_client, event, payload, correlation_id
                        )

                        # Mark the event as successfully delivered.
                        event.status = "completed"
                        event.processed_at = None  # SQL NOW() below fills it
                        session.exec(
                            text(
                                "UPDATE outbox_events SET status = 'completed', processed_at = NOW() WHERE id = :id"
                            ).bindparams(id=event.id)
                        )
                        logger.debug(
                            "Outbox event_id=%s correlation_id=%s marked completed",
                            event.id,
                            correlation_id,
                        )
                    except Exception as row_e:  # noqa: BLE001 - Retain failed rows for retry without stopping delivery.
                        logger.error(
                            "Failed to process outbox event_id=%s correlation_id=%s (retry=%d): %s",
                            event.id,
                            event.ensure_correlation_id(),
                            event.retry_count,
                            row_e,
                            exc_info=True,
                        )
                        # Increment retry counter; mark as failed after 5 attempts.
                        session.exec(
                            text(
                                """
                                UPDATE outbox_events
                                SET retry_count = retry_count + 1,
                                    status = CASE WHEN retry_count >= 5 THEN 'failed' ELSE 'pending' END,
                                    processed_at = NOW()
                                WHERE id = :id
                                """
                            ).bindparams(id=event.id)
                        )

                session.commit()

        except Exception as e:  # noqa: BLE001 - Keep the outbox polling loop alive across failures.
            logger.error(f"Outbox processor error: {e}")

        # Poll every 2 seconds — keeps end-to-end NATS latency low.
        await asyncio.sleep(2)
