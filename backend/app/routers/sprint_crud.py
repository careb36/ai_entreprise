"""
Sprint update, delete/archive and unarchive (``services/loop/sprint_archive.py``).

Endpoints:
- ``PATCH  /v1/sprints/{id}``           — edit ``sprint_name``, ``goal`` or
  ``duration_days`` (Manager). Never the status (the controls'
  job, ``routers/sprint_control.py``), the company or the tasks; an archived
  sprint is refused (409). Audited as ``sprint_updated`` with each change.
- ``DELETE /v1/sprints/{id}``           — delete a halted sprint with no
  evidence, or archive it when there is some (Owner). A running sprint is
  refused (409, stop it first). Audited as ``sprint_deleted`` or
  ``sprint_archived``.
- ``POST   /v1/sprints/{id}/unarchive`` — bring an archived sprint back (Owner).
  Audited as ``sprint_unarchived``.

API tokens are at most Manager, so they never delete; neither does the
Manager agent (``delete_sprint`` is in ``manager_policy.ALWAYS_ESCALATE``).
"""

from typing import Annotated

from auth_policy import current_user_if_required, require_company_role
from database import get_session
from fastapi import APIRouter, Body, Depends, HTTPException
from models import Sprint, User
from pydantic import ConfigDict, Field, StringConstraints
from schemas import RequestModel
from services.loop import sprint_archive
from sqlmodel import Session

router = APIRouter(prefix="/v1", tags=["Core"])

SprintName = Annotated[
    str, StringConstraints(strip_whitespace=True, min_length=1, max_length=255)
]
Goal = Annotated[str, StringConstraints(strip_whitespace=True, max_length=2000)]
# Sprints are time boxes of at most a quarter.
MAX_DURATION_DAYS = 90


class SprintUpdate(RequestModel):
    """The fields a Manager may edit; any other field is refused (422)."""

    model_config = ConfigDict(extra="forbid")

    sprint_name: SprintName | None = None
    goal: Goal | None = None
    duration_days: int | None = Field(default=None, ge=1, le=MAX_DURATION_DAYS)


class SprintRemoval(RequestModel):
    model_config = ConfigDict(extra="forbid")

    reason: Annotated[str, StringConstraints(max_length=500)] | None = None


def _actor(user: User | None) -> str:
    return user.email if user is not None and user.email else "a person"


def _sprint(session: Session, sprint_id: int, user: User | None, role: str) -> Sprint:
    sprint = session.get(Sprint, sprint_id)
    if sprint is None:
        raise HTTPException(status_code=404, detail="Sprint not found")
    require_company_role(session, user, sprint.company_id, role)
    return sprint


def _serialize(sprint: Sprint) -> dict:
    return {
        "id": sprint.id,
        "company_id": sprint.company_id,
        "sprint_name": sprint.sprint_name,
        "goal": sprint.goal,
        "duration_days": sprint.duration_days,
        "status": sprint.status,
        "archived_at": sprint.archived_at.isoformat() if sprint.archived_at else None,
        "archived_by": sprint.archived_by,
    }


@router.patch("/sprints/{sprint_id}")
async def update_sprint(
    sprint_id: int,
    req: SprintUpdate,
    session: Session = Depends(get_session),  # noqa: B008 - FastAPI dependency marker.
    user: User | None = Depends(current_user_if_required),  # noqa: B008 - FastAPI dependency marker.
):
    """Edit the sprint's name, goal or duration (Manager); returns what changed."""
    sprint = _sprint(session, sprint_id, user, "Manager")
    changes = req.model_dump(exclude_unset=True)
    if changes.get("sprint_name", "") is None:
        raise HTTPException(status_code=422, detail="sprint_name cannot be empty")
    if "goal" in changes and not changes["goal"]:
        changes["goal"] = None  # an empty goal clears it
    try:
        changed = sprint_archive.update(session, sprint, changes, _actor(user))
    except sprint_archive.SprintAdminError as exc:
        raise HTTPException(status_code=exc.status, detail=exc.detail) from exc
    session.refresh(sprint)
    return {**_serialize(sprint), "changed": changed}


@router.delete("/sprints/{sprint_id}")
async def delete_sprint(
    sprint_id: int,
    req: SprintRemoval | None = Body(default=None),  # noqa: B008 - FastAPI body marker.
    session: Session = Depends(get_session),  # noqa: B008 - FastAPI dependency marker.
    user: User | None = Depends(current_user_if_required),  # noqa: B008 - FastAPI dependency marker.
):
    """Delete a halted sprint with no evidence, else archive it (Owner).

    ``{"deleted": true, ...}`` or ``{"archived": true, "archived_at": ...,
    "evidence": [...]}``, the evidence naming what was recorded.
    """
    sprint = _sprint(session, sprint_id, user, "Owner")
    try:
        return sprint_archive.remove(
            session, sprint, _actor(user), req.reason if req else None
        )
    except sprint_archive.SprintAdminError as exc:
        raise HTTPException(status_code=exc.status, detail=exc.detail) from exc


@router.post("/sprints/{sprint_id}/unarchive")
async def unarchive_sprint(
    sprint_id: int,
    session: Session = Depends(get_session),  # noqa: B008 - FastAPI dependency marker.
    user: User | None = Depends(current_user_if_required),  # noqa: B008 - FastAPI dependency marker.
):
    """Bring an archived sprint back to the lists and the inbox (Owner)."""
    sprint = _sprint(session, sprint_id, user, "Owner")
    try:
        return sprint_archive.unarchive(session, sprint, _actor(user))
    except sprint_archive.SprintAdminError as exc:
        raise HTTPException(status_code=exc.status, detail=exc.detail) from exc
