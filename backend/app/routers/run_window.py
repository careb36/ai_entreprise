"""When a company's work that can wait may start (issue #736,
``services/platform/run_window_setting.py``, ``services/loop/run_window_gate.py``).

- ``GET /v1/companies/{id}/run_window``          the window, whether it is open, the next 7 days (Viewer)
- ``PUT /v1/companies/{id}/run_window``          set it, or ``null`` to turn it off (Owner; audited ``run_window_changed``)
- ``POST /v1/companies/{id}/run_window/preview`` a window not saved yet, checked and previewed (Viewer)
- ``PUT /v1/sprints/{id}/urgent``                bypass the window for a sprint (Manager; audited ``sprint_urgent_changed``)

As ``/v1/companies/{id}/forge_policy``: any member reads the window, only an
Owner writes it (403 otherwise), and the audit row names the account
(``user_id``, and ``token_id`` when an Owner's API token made the change).
"""

from __future__ import annotations

from typing import Any

from auth_policy import current_user_if_required, require_company_role
from database import get_session
from fastapi import APIRouter, Depends, HTTPException
from models import Sprint, User
from schemas import RequestModel
from services.loop import run_window_gate as gate
from services.platform import run_window_setting as setting
from services.platform import sod
from sqlmodel import Session

router = APIRouter(prefix="/v1", tags=["Company"])


class RunWindowRequest(RequestModel):
    """``{"run_window": {...}}``, or ``{"run_window": null}`` to turn it off."""

    run_window: dict[str, Any] | None = None


class UrgentRequest(RequestModel):
    urgent: bool


@router.get("/companies/{company_id}/run_window")
def get_run_window(
    company_id: int,
    session: Session = Depends(get_session),  # noqa: B008 - FastAPI dependency marker.
    user: User | None = Depends(current_user_if_required),  # noqa: B008 - FastAPI dependency marker.
):
    """The company's run window, whether it is open now, and its next 7 days."""
    require_company_role(session, user, company_id, "Viewer")
    return setting.state(session, company_id, gate.utc_now())


@router.post("/companies/{company_id}/run_window/preview")
def preview_run_window(
    company_id: int,
    payload: RunWindowRequest,
    session: Session = Depends(get_session),  # noqa: B008 - FastAPI dependency marker.
    user: User | None = Depends(current_user_if_required),  # noqa: B008 - FastAPI dependency marker.
):
    """A window as the settings form holds it, checked and previewed; nothing is saved."""
    require_company_role(session, user, company_id, "Viewer")
    try:
        return setting.preview(payload.run_window, gate.utc_now())
    except setting.RunWindowRefused as exc:
        raise HTTPException(status_code=exc.status, detail=exc.detail) from exc


@router.put("/companies/{company_id}/run_window")
def put_run_window(
    company_id: int,
    payload: RunWindowRequest,
    session: Session = Depends(get_session),  # noqa: B008 - FastAPI dependency marker.
    user: User | None = Depends(current_user_if_required),  # noqa: B008 - FastAPI dependency marker.
):
    """Set or clear the company's run window (Owner; audited)."""
    require_company_role(session, user, company_id, "Owner")
    try:
        return setting.set_window(
            session,
            company_id,
            payload.run_window,
            now=gate.utc_now(),
            **gate.actor_fields(sod.actor_of(session, user)),
        )
    except setting.RunWindowRefused as exc:
        raise HTTPException(status_code=exc.status, detail=exc.detail) from exc


@router.put("/sprints/{sprint_id}/urgent")
def put_sprint_urgent(
    sprint_id: int,
    payload: UrgentRequest,
    session: Session = Depends(get_session),  # noqa: B008 - FastAPI dependency marker.
    user: User | None = Depends(current_user_if_required),  # noqa: B008 - FastAPI dependency marker.
):
    """Mark a sprint urgent, or not (Manager; audited). Urgent work ignores the
    run window, and a sprint waiting for it starts at once."""
    sprint = session.get(Sprint, sprint_id)
    if sprint is None:
        raise HTTPException(status_code=404, detail="Sprint not found")
    require_company_role(session, user, sprint.company_id, "Manager")
    if sprint.archived_at is not None:
        raise HTTPException(status_code=409, detail="Sprint is archived")
    result = gate.set_urgent(
        session,
        sprint,
        payload.urgent,
        now=gate.utc_now(),
        **gate.actor_fields(sod.actor_of(session, user)),
    )
    session.commit()
    return result
