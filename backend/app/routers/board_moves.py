"""
A card moved to another sprint (``services/loop/board_moves.py``).

Endpoints:
- ``GET  /v1/backlog/{id}/carry_targets`` — the company's open sprints the
  task may move to (Viewer).
- ``POST /v1/backlog/{id}/carry_over``    — carry a blocked (or To do) task
  into ``target_sprint_id``, or into a new sprint when it is empty (Manager).
  Once per task (409 after); 404 for an unknown task or another company's
  sprint; 409 for an archived sprint, a task that is not blocked for a person,
  a task waiting on unfinished tasks, a task its sprint's acceptance tests
  check, a target that is not open, or a lock another request holds.

Moving a card to To do (``PATCH /v1/backlog/{id}``, ``routers/sprint.py``)
starts it; the rules are in the service.
"""

from auth_policy import current_user_if_required, require_company_role
from database import get_session
from fastapi import APIRouter, Depends, HTTPException
from models import BacklogItem, Sprint, User
from pydantic import BaseModel, ConfigDict
from services import board_moves
from sqlmodel import Session

router = APIRouter(prefix="/v1", tags=["Core"])


class CarryRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    target_sprint_id: int | None = None


def _task(
    session: Session, item_id: int, user: User | None, role: str
) -> tuple[BacklogItem, Sprint]:
    item = session.get(BacklogItem, item_id)
    sprint = session.get(Sprint, item.sprint_id) if item is not None else None
    if item is None or sprint is None:
        raise HTTPException(status_code=404, detail="Backlog item not found")
    require_company_role(session, user, sprint.company_id, role)
    return item, sprint


@router.get("/backlog/{item_id}/carry_targets")
async def carry_targets(
    item_id: int,
    session: Session = Depends(get_session),  # noqa: B008 - FastAPI dependency marker.
    user: User | None = Depends(current_user_if_required),  # noqa: B008 - FastAPI dependency marker.
):
    """The open sprints of the task's company, other than its own."""
    _, sprint = _task(session, item_id, user, "Viewer")
    return {"sprints": board_moves.open_targets(session, sprint)}


@router.post("/backlog/{item_id}/carry_over")
async def carry_task_over(
    item_id: int,
    req: CarryRequest,
    session: Session = Depends(get_session),  # noqa: B008 - FastAPI dependency marker.
    user: User | None = Depends(current_user_if_required),  # noqa: B008 - FastAPI dependency marker.
):
    """Carry the task into another sprint; the new task and where it went."""
    item, sprint = _task(session, item_id, user, "Manager")
    actor = user.email if user is not None and user.email else "a person"
    try:
        answer = board_moves.carry_to_sprint(
            session, item, sprint, req.target_sprint_id, actor
        )
    except board_moves.BoardMoveError as exc:
        session.rollback()
        raise HTTPException(status_code=exc.status, detail=exc.detail) from exc
    return {"original_id": item_id, **answer}
