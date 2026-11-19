"""Whether a company's git may contact a forge (issue #727,
``services/platform/forge_policy_setting.py``).

- ``GET /v1/companies/{id}/forge_policy``  the stored value, the one in force and the ceiling (Viewer)
- ``PUT /v1/companies/{id}/forge_policy``  set it (Owner; audited ``forge_policy_changed``)

As ``/v1/companies/{id}/integration_review``: any member reads it, only an
Owner writes it (403 otherwise), and the audit row names the account
(``user_id``, and ``token_id`` when an Owner's API token made the change).
"""

from __future__ import annotations

from auth_policy import current_user_if_required, require_company_role
from database import get_session
from fastapi import APIRouter, Depends, HTTPException
from models import User
from schemas import ForgePolicySetting
from services.platform import forge_policy_setting as setting
from services.platform import sod
from sqlmodel import Session

router = APIRouter(prefix="/v1", tags=["Company"])


@router.get("/companies/{company_id}/forge_policy")
def get_forge_policy(
    company_id: int,
    session: Session = Depends(get_session),  # noqa: B008 - FastAPI dependency marker.
    user: User | None = Depends(current_user_if_required),  # noqa: B008 - FastAPI dependency marker.
):
    """The company's forge policy, the one in force and the deployment's ceiling."""
    require_company_role(session, user, company_id, "Viewer")
    return setting.state(session, company_id)


@router.put("/companies/{company_id}/forge_policy")
def put_forge_policy(
    company_id: int,
    payload: ForgePolicySetting,
    session: Session = Depends(get_session),  # noqa: B008 - FastAPI dependency marker.
    user: User | None = Depends(current_user_if_required),  # noqa: B008 - FastAPI dependency marker.
):
    """Set the company's forge policy (Owner; audited)."""
    require_company_role(session, user, company_id, "Owner")
    actor = sod.actor_of(session, user)
    by = (actor.label if actor else "") or "A person"
    try:
        return setting.set_policy(
            session,
            company_id,
            payload.forge_policy,
            by=by,
            user_id=actor.user_id if actor else None,
            token_id=actor.token_id if actor else None,
        )
    except setting.ForgePolicyRefused as exc:
        raise HTTPException(status_code=exc.status, detail=exc.detail) from exc
