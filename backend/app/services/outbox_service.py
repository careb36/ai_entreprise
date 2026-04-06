"""
Transactional Outbox processor for reliable NATS message delivery.

Background worker that continuously polls the ``outbox_events`` table for
pending rows and publishes them to NATS, ensuring at-least-once delivery.

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

Retry policy:
-------------
- Up to 5 delivery attempts per event.
- After 5 failures the row is marked ``"failed"`` and excluded from future polls.
- A ``retry_count`` column tracks the number of failed attempts.
"""

import asyncio
import json

from sqlmodel import Session, select, text

from database import engine
from models import OutboxEvent
from logging_config import logger


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
                rows = session.exec(
                    text(
                        """
                        SELECT id, event_type, payload, retry_count
                        FROM outbox_events
                        WHERE status = 'pending'
                        ORDER BY created_at ASC
                        FOR UPDATE SKIP LOCKED
                        LIMIT 10
                        """
                    )
                ).all()

                for event_id, event_type, payload_str, retries in rows:
                    try:
                        payload = json.loads(payload_str) if isinstance(payload_str, str) else payload_str

                        if event_type == "nats_publish":
                            # Publish the inner message to the specified NATS subject.
                            await nats_client.publish(payload["subject"], payload["message"])

                        # Mark the event as successfully delivered.
                        session.exec(
                            text("UPDATE outbox_events SET status = 'completed', processed_at = NOW() WHERE id = :id")
                            .bindparams(id=event_id)
                        )
                    except Exception as row_e:
                        logger.error(f"Failed to process outbox event {event_id}: {row_e}")
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
                            ).bindparams(id=event_id)
                        )

                session.commit()

        except Exception as e:
            logger.error(f"Outbox processor error: {e}")

        # Poll every 2 seconds — keeps end-to-end NATS latency low.
        await asyncio.sleep(2)
