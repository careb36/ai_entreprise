"""SSO-managed teams: the break-glass exclusion and the conversion preview
(issue #703; ``docs/design/SSO_GROUP_MAPPING.md``, R19, R27, R28).

A signed-in human Owner only; an API token is refused whatever its role.

- ``POST   /v1/companies/{id}/teams/{team_id}/exclusions``                  take a person out, pinned
- ``DELETE /v1/companies/{id}/teams/{team_id}/exclusions/{exclusion_id}``   lift it, with a reason
- ``POST   /v1/companies/{id}/teams/{team_id}/membership-source-preview``   who converting to SSO keeps

The switch itself is ``PATCH /v1/companies/{id}/teams/{team_id}`` with
``membership_source``, ``preview_id`` and ``confirm`` (``routers/teams.py``).
"""

from __future__ import annotations

from auth_policy import current_user_if_required
from database import get_session
from fastapi import APIRouter, Depends, HTTPException
from models import User
from pydantic import Field
from schemas import RequestModel
from services.platform import sso_team_control as control
from sqlmodel import Session

from routers.teams import _owner, _team

router = APIRouter(prefix="/v1", tags=["Company"])


class ExclusionIn(RequestModel):
    user_id: int
    reason: str = Field(min_length=1, max_length=2000)


class LiftIn(RequestModel):
    reason: str = Field(min_length=1, max_length=2000)


def _refused(session: Session, exc: control.ControlRefused) -> HTTPException:
    session.rollback()
    return HTTPException(status_code=exc.status, detail=exc.detail)


def _exclusion(row) -> dict:
    return {
        "id": row.id,
        "team_id": row.team_id,
        "user_id": row.user_id,
        "created_by": row.created_by,
        "created_at": row.created_at.isoformat() if row.created_at else None,
        "reason": row.reason,
        "lifted_by": row.lifted_by,
        "lifted_at": row.lifted_at.isoformat() if row.lifted_at else None,
        "lift_reason": row.lift_reason,
    }


@router.post("/companies/{company_id}/teams/{team_id}/exclusions", status_code=201)
async def create_exclusion(
    company_id: int,
    team_id: int,
    body: ExclusionIn,
    session: Session = Depends(get_session),  # noqa: B008 - FastAPI dependency marker.
    user: User | None = Depends(current_user_if_required),  # noqa: B008 - FastAPI dependency marker.
):
    actor = _owner(session, user, company_id)
    team = _team(session, company_id, team_id)
    try:
        row = control.create_exclusion(session, team, actor, body.user_id, body.reason)
    except control.ControlRefused as exc:
        raise _refused(session, exc) from exc
    session.commit()
    session.refresh(row)
    return _exclusion(row)


@router.delete("/companies/{company_id}/teams/{team_id}/exclusions/{exclusion_id}")
async def lift_exclusion(
    company_id: int,
    team_id: int,
    exclusion_id: int,
    body: LiftIn,
    session: Session = Depends(get_session),  # noqa: B008 - FastAPI dependency marker.
    user: User | None = Depends(current_user_if_required),  # noqa: B008 - FastAPI dependency marker.
):
    actor = _owner(session, user, company_id)
    team = _team(session, company_id, team_id)
    try:
        row = control.lift_exclusion(session, team, actor, exclusion_id, body.reason)
    except control.ControlRefused as exc:
        raise _refused(session, exc) from exc
    session.commit()
    session.refresh(row)
    return _exclusion(row)


@router.post("/companies/{company_id}/teams/{team_id}/membership-source-preview")
async def membership_source_preview(
    company_id: int,
    team_id: int,
    session: Session = Depends(get_session),  # noqa: B008 - FastAPI dependency marker.
    user: User | None = Depends(current_user_if_required),  # noqa: B008 - FastAPI dependency marker.
):
    actor = _owner(session, user, company_id)
    team = _team(session, company_id, team_id)
    try:
        row = control.create_preview(session, team, actor)
    except control.ControlRefused as exc:
        raise _refused(session, exc) from exc
    return {
        "id": row.id,
        "team_id": team.id,
        "members": list(row.members or []),
        "expires_at": row.expires_at.isoformat(),
        "kept_as": "manual_exception",
    }
