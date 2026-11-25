"""
Operator replay for the transactional outbox (admin scope).

Endpoints:
- ``POST /v1/admin/outbox-events/{event_id}/replay`` — return a failed or
  stuck outbox event to a pollable state so the running ``process_outbox``
  poller republishes its committed payload; returns the replayed event id.

This is a recovery tool for an operator, not part of any run's normal flow. It
is scoped to a support admin (``require_support_admin``). It re-publishes only
the row's committed payload and correlation id — it never recreates the sprint
task the original event was about — so a replay cannot produce a second
backlog row or a second task run. The endpoint does not publish directly: it
resets the row and lets the poller do the publish, so the operator's call and
the worker never race on the same message.
"""

from auth_policy import current_user_if_required, require_support_admin
from database import get_session
from fastapi import APIRouter, Depends, HTTPException
from models import User
from services.integrations import outbox_service
from sqlmodel import Session

router = APIRouter(prefix="/v1/admin/outbox-events", tags=["Support"])


@router.post("/{event_id}/replay")
async def replay_outbox_event(
    event_id: int,
    session: Session = Depends(get_session),  # noqa: B008 - FastAPI dependency marker.
    user: User | None = Depends(current_user_if_required),  # noqa: B008 - FastAPI dependency marker.
):
    """Reset a failed or stuck outbox event to ``pending`` and return its id.

    The row keeps its committed payload and correlation id, so the poller
    republishes the same message (with the same ``Nats-Msg-Id``). A completed
    row, or a pending row that is already due, is refused with 409: there is
    nothing for a reset to change.
    """
    require_support_admin(user)
    try:
        event = outbox_service.replay_event(session, event_id)
    except outbox_service.OutboxReplayError as exc:
        status_code = 404 if exc.kind == "not_found" else 409
        raise HTTPException(status_code=status_code, detail=exc.reason) from exc
    return {
        "event_id": event.id,
        "correlation_id": str(event.correlation_id) if event.correlation_id else None,
        "status": event.status,
        "aggregate_type": event.aggregate_type,
        "aggregate_id": event.aggregate_id,
        "event_type": event.event_type,
    }
