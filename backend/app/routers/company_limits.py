"""A company's limits on its agents' work (issue #766,
``services/platform/company_limits_setting.py``).

- ``GET /v1/companies/{id}/limits``  the company's limits, the deployment's defaults and the ceilings (Viewer)
- ``PUT /v1/companies/{id}/limits``  set them, or ``null`` to use the deployment's (Owner; audited ``company_limits_changed``)

As ``/v1/companies/{id}/run_window``: any member reads them, only an Owner
writes them (403 otherwise), since each changes what a task may spend, and
the audit row names the account (``user_id``, and ``token_id`` when an
Owner's API token made the change).
"""

from __future__ import annotations

from typing import Any

from auth_policy import current_user_if_required, require_company_role
from database import get_session
from fastapi import APIRouter, Depends, HTTPException
from models import User
from schemas import RequestModel
from services.loop import run_window_gate as gate
from services.platform import company_limits_setting as setting
from services.platform import sod
from sqlmodel import Session

router = APIRouter(prefix="/v1", tags=["Company"])


class LimitsRequest(RequestModel):
    """``{"limits": {"tool_rounds": 12, ...}}``, or ``{"limits": null}``."""

    limits: dict[str, Any] | None = None


@router.get("/companies/{company_id}/limits")
def get_limits(
    company_id: int,
    session: Session = Depends(get_session),  # noqa: B008 - FastAPI dependency marker.
    user: User | None = Depends(current_user_if_required),  # noqa: B008 - FastAPI dependency marker.
):
    """The company's limits beside the deployment's defaults and the ceilings."""
    require_company_role(session, user, company_id, "Viewer")
    return setting.state(session, company_id)


@router.put("/companies/{company_id}/limits")
def put_limits(
    company_id: int,
    payload: LimitsRequest,
    session: Session = Depends(get_session),  # noqa: B008 - FastAPI dependency marker.
    user: User | None = Depends(current_user_if_required),  # noqa: B008 - FastAPI dependency marker.
):
    """Set or clear the company's limits (Owner; audited)."""
    require_company_role(session, user, company_id, "Owner")
    try:
        return setting.set_limits(
            session,
            company_id,
            payload.limits,
            **gate.actor_fields(sod.actor_of(session, user)),
        )
    except setting.LimitsRefused as exc:
        raise HTTPException(status_code=exc.status, detail=exc.detail) from exc
