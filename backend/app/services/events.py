"""Task lifecycle events: the board is a projection of them.

Events are appended (never edited) and read back to rebuild a task's state.
This module only writes and reads; the projection logic lives in the board.
"""

from __future__ import annotations

from typing import Any

from models import TaskEvent
from sqlmodel import Session, select

from services import task_events


def record(
    session: Session,
    company_id: int,
    sprint_id: int,
    task_id: int | None,
    event_type: str,
    payload: dict[str, Any] | None = None,
) -> TaskEvent:
    event = TaskEvent(
        company_id=company_id,
        sprint_id=sprint_id,
        task_id=task_id,
        event_type=event_type,
        payload=payload or {},
    )
    task_events.seal(session, event)
    session.commit()
    session.refresh(event)
    return event


def list_for_sprint(session: Session, sprint_id: int) -> list[TaskEvent]:
    return list(
        session.exec(
            select(TaskEvent)
            .where(TaskEvent.sprint_id == sprint_id)
            .order_by(TaskEvent.id)
        ).all()
    )
