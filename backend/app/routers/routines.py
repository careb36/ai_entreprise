"""A company's routines: a task on a daily or weekly schedule (issue #774).

- ``GET    /v1/companies/{company_id}/routines`` — its routines (Viewer).
- ``POST   /v1/companies/{company_id}/routines`` — write one (Manager).
- ``PATCH  /v1/routines/{routine_id}``           — change one (Manager).
- ``DELETE /v1/routines/{routine_id}``           — delete one (Manager).
- ``POST   /v1/routines/{routine_id}/run``       — run it now (Manager).

A routine decides what agents do, so changing or running one takes a Manager,
like a sprint. Every change and run is audited (``services/loop/routines.py``).
"""

from typing import Any

from auth_policy import current_user_if_required, require_company_role
from database import get_session
from fastapi import APIRouter, Depends, HTTPException
from models import Routine, User
from pydantic import Field
from schemas import RequestModel
from services.loop import routines
from services.loop import run_window_gate as gate
from services.platform import sod
from sqlmodel import Session, select

router = APIRouter(prefix="/v1", tags=["Routines"])


class RoutineSchedule(RequestModel):
    """When it runs: ``every`` day or week, ``at`` HH:MM in ``timezone``."""

    every: str
    at: str
    timezone: str = "UTC"
    weekday: int | None = None


class RoutineCreateRequest(RequestModel):
    """A new routine."""

    title: str = Field(max_length=routines.TITLE_MAX)
    description: str = Field(default="", max_length=routines.DESCRIPTION_MAX)
    assignee: str = Field(max_length=100)
    schedule: RoutineSchedule
    enabled: bool = True


class RoutineUpdateRequest(RequestModel):
    """The fields of a routine to change."""

    title: str | None = Field(default=None, max_length=routines.TITLE_MAX)
    description: str | None = Field(default=None, max_length=routines.DESCRIPTION_MAX)
    assignee: str | None = Field(default=None, max_length=100)
    schedule: RoutineSchedule | None = None
    enabled: bool | None = None


def _payload(req: RequestModel) -> dict[str, Any]:
    data = req.model_dump(exclude_unset=True)
    if isinstance(data.get("schedule"), dict):
        data["schedule"] = {k: v for k, v in data["schedule"].items() if v is not None}
    return data


def _actor(session: Session, user: User | None) -> dict[str, Any]:
    return gate.actor_fields(sod.actor_of(session, user))


def _routine(
    session: Session, user: User | None, routine_id: int, role: str
) -> Routine:
    routine = session.get(Routine, routine_id)
    if routine is None:
        raise HTTPException(status_code=404, detail=f"Routine {routine_id} not found")
    require_company_role(session, user, routine.company_id, role)
    return routine


def _refused(exc: routines.RoutineRefused) -> HTTPException:
    return HTTPException(status_code=exc.status, detail=exc.detail)


@router.get("/companies/{company_id}/routines")
async def list_routines(
    company_id: int,
    session: Session = Depends(get_session),  # noqa: B008
    user: User | None = Depends(current_user_if_required),  # noqa: B008
):
    """The company's routines (Viewer)."""
    require_company_role(session, user, company_id, "Viewer")
    rows = session.exec(
        select(Routine).where(Routine.company_id == company_id).order_by(Routine.id)
    ).all()
    return {"company_id": company_id, "routines": [routines.view(r) for r in rows]}


@router.post("/companies/{company_id}/routines", status_code=201)
async def create_routine(
    company_id: int,
    req: RoutineCreateRequest,
    session: Session = Depends(get_session),  # noqa: B008
    user: User | None = Depends(current_user_if_required),  # noqa: B008
):
    """Write a routine (Manager)."""
    require_company_role(session, user, company_id, "Manager")
    try:
        routine = routines.create(
            session, company_id, _payload(req), _actor(session, user)
        )
    except routines.RoutineRefused as exc:
        raise _refused(exc) from exc
    return routines.view(routine)


@router.patch("/routines/{routine_id}")
async def update_routine(
    routine_id: int,
    req: RoutineUpdateRequest,
    session: Session = Depends(get_session),  # noqa: B008
    user: User | None = Depends(current_user_if_required),  # noqa: B008
):
    """Change a routine (Manager)."""
    routine = _routine(session, user, routine_id, "Manager")
    try:
        routine = routines.update_routine(
            session, routine, _payload(req), _actor(session, user)
        )
    except routines.RoutineRefused as exc:
        raise _refused(exc) from exc
    return routines.view(routine)


@router.delete("/routines/{routine_id}")
async def delete_routine(
    routine_id: int,
    session: Session = Depends(get_session),  # noqa: B008
    user: User | None = Depends(current_user_if_required),  # noqa: B008
):
    """Delete a routine (Manager); the sprints it opened stay."""
    routine = _routine(session, user, routine_id, "Manager")
    routines.delete(session, routine, _actor(session, user))
    return {"deleted": routine_id}


@router.post("/routines/{routine_id}/run")
async def run_routine(
    routine_id: int,
    session: Session = Depends(get_session),  # noqa: B008
    user: User | None = Depends(current_user_if_required),  # noqa: B008
):
    """Run a routine now (Manager); skipped while its previous run is open."""
    routine = _routine(session, user, routine_id, "Manager")
    return routines.run(session, routine, _actor(session, user))
