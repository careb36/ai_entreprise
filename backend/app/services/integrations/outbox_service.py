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
-------------------
``SELECT ... FOR UPDATE SKIP LOCKED`` acquires row-level locks so that multiple
worker replicas running in Kubernetes never process the same event simultaneously.

Correlation:
------------
Each row carries a ``correlation_id`` (a UUID) generated exactly once at
enqueue time. It is never regenerated on retry or redelivery, and it is
distinct from ``Nats-Msg-Id`` which keeps its dedup role. The id rides in the
published header and payload, so the worker can set the same id on its spans
and on the ``AgentRun`` it creates.

Retry policy:
-------------
- Up to 5 delivery attempts per event.
- After 5 failures the row is marked ``"failed"`` and excluded from future polls.
- A ``retry_count`` column tracks the number of failed attempts.

Replay:
-------
``replay_event`` (``POST /v1/admin/outbox-events/{id}/replay``, issue #736)
returns a ``failed`` row, or a ``pending`` row waiting out its backoff, to a
due ``pending`` row: ``retry_count`` 0 and no ``processed_at``. The payload and
the correlation id stay as committed, so the poller republishes the same
message under the same ``Nats-Msg-Id``; nothing that created the row runs again.
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


class OutboxReplayError(Exception):
    """A replay that cannot be done: ``kind`` is ``not_found`` or ``not_replayable``."""

    def __init__(self, kind: str, reason: str) -> None:
        super().__init__(reason)
        self.kind = kind
        self.reason = reason


def replay_event(session: Session, event_id: int) -> OutboxEvent:
    """Make outbox row *event_id* due again, commit, and return it.

    A ``failed`` row, or a ``pending`` row that has failed before (so it waits
    for its backoff), becomes ``pending`` with ``retry_count`` 0 and no
    ``processed_at``. A ``completed`` row, or a ``pending`` row that is due
    already, raises ``OutboxReplayError("not_replayable")``.
    """
    event = session.exec(
        select(OutboxEvent).where(OutboxEvent.id == event_id).with_for_update()
    ).first()
    if event is None:
        raise OutboxReplayError("not_found", f"Outbox event {event_id} not found")
    stuck = event.status == "pending" and (event.retry_count or 0) > 0
    if event.status != "failed" and not stuck:
        raise OutboxReplayError(
            "not_replayable",
            f"Outbox event {event_id} is {event.status} and due: nothing to replay",
        )
    event.ensure_correlation_id()
    event.status = "pending"
    event.retry_count = 0
    event.processed_at = None
    session.add(event)
    session.commit()
    session.refresh(event)
    logger.info(
        "Outbox event_id=%s replayed correlation_id=%s", event.id, event.correlation_id
    )
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

                        if event.event_type == "nats_publish":
                            subject = payload["subject"]
                            message = payload["message"]
                            # Stable id travels with the message so the worker
                            # can set it on spans and AgentRun. Never regenerate.
                            message["correlation_id"] = str(correlation_id)
                            logger.info(
                                "Outbox dispatching event_id=%s subject=%s sprint_id=%s item_id=%s correlation_id=%s",
                                event.id,
                                subject,
                                message.get("sprint_id"),
                                message.get("backlog_item_id"),
                                correlation_id,
                            )
                            # Publish the inner message to the specified NATS subject.
                            # Durable subjects return only once JetStream has
                            # stored the message; the id makes a retry after a
                            # lost acknowledgement a no-op.
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
                            await chat_approvals.deliver(
                                session, event.event_type, payload, event.id
                            )
                        elif event.event_type in chat_regressions.EVENT_TYPES:
                            # Regressions, information only (issue #682).
                            await chat_regressions.deliver(session, payload, event.id)
                        elif event.event_type in person_notices.EVENT_TYPES:
                            # A personal notification's email copy (issue #683).
                            await person_notices.deliver_email(session, payload)

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
