import asyncio
import json

from sqlmodel import Session, select, text

from database import engine
from models import OutboxEvent
from logging_config import logger


async def process_outbox(nats_client) -> None:
    """
    Background worker that reliably delivers transactional outbox events to NATS.
    Uses SELECT ... FOR UPDATE SKIP LOCKED to prevent K8s worker collisions.
    Applies exponential-style retry: event is marked 'failed' after 5 attempts.
    """
    while True:
        if not nats_client:
            await asyncio.sleep(2)
            continue

        try:
            with Session(engine) as session:
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
                            await nats_client.publish(payload["subject"], payload["message"])
                        session.exec(
                            text("UPDATE outbox_events SET status = 'completed', processed_at = NOW() WHERE id = :id")
                            .bindparams(id=event_id)
                        )
                    except Exception as row_e:
                        logger.error(f"Failed to process outbox event {event_id}: {row_e}")
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

        await asyncio.sleep(2)
