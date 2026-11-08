"""Task lifecycle events router."""

from auth_policy import current_user_if_required, require_company_role
from database import get_session
from fastapi import APIRouter, Depends, HTTPException
from models import BacklogItem, Sprint, User
from services import events, task_events, task_projection
from sqlmodel import Session

router = APIRouter(prefix="/v1", tags=["Company"])


@router.get("/sprints/{sprint_id}/events")
async def sprint_events(
    sprint_id: int,
    session: Session = Depends(get_session),  # noqa: B008
    user: User | None = Depends(current_user_if_required),  # noqa: B008
):
    sprint = session.get(Sprint, sprint_id)
    if sprint is None:
        raise HTTPException(status_code=404, detail="Sprint not found")
    require_company_role(session, user, sprint.company_id, "Viewer")
    rows = events.list_for_sprint(session, sprint_id)
    return {
        "sprint_id": sprint_id,
        "events": [
            {
                "id": e.id,
                "task_id": e.task_id,
                "event_type": e.event_type,
                "payload": e.payload,
                "created_at": e.created_at.isoformat() if e.created_at else None,
            }
            for e in rows
        ],
    }


@router.get("/tasks/{task_id}/events")
async def task_event_log(
    task_id: int,
    session: Session = Depends(get_session),  # noqa: B008
    user: User | None = Depends(current_user_if_required),  # noqa: B008
):
    """A task's events in order, its chain's state and which record the board reads."""
    item = session.get(BacklogItem, task_id)
    sprint = session.get(Sprint, item.sprint_id) if item is not None else None
    if item is None or sprint is None:
        raise HTTPException(status_code=404, detail="Task not found")
    require_company_role(session, user, sprint.company_id, "Viewer")
    rows = [
        e for e in task_events.for_task(session, task_id) if e.company_id == sprint.company_id
    ]
    return {
        "task_id": task_id,
        "source": "events" if task_projection.covers(item, rows) else "comments",
        "chain": task_events.verify_chain(session, task_id),
        "events": [task_events.as_dict(e) for e in rows],
    }
