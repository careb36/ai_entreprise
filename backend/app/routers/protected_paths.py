"""Whether a company hosts the factory, so its protected paths hold
(``services/platform/protected_paths_setting.py``; ``docs/design/rsi-loop.md``,
section 3.2).

- ``GET /v1/companies/{id}/protected_paths``  the flag and the list in force (Viewer)
- ``PUT /v1/companies/{id}/protected_paths``  turn it on or off (a signed-in Owner; audited)

The list is the running backend's (``services/contracts/protected_paths.py``,
with the evidence's tests when the backend's checkout has the test map); it
is shown, never written here. Only a signed-in human Owner changes the flag:
an API token is refused like a Manager, and the Manager agent never can
(``change_protected_paths`` is in ``ALWAYS_ESCALATE``).
"""

from __future__ import annotations

from auth_policy import current_user_if_required, require_company_role
from database import get_session
from fastapi import APIRouter, Depends, HTTPException
from models import User
from schemas import ProtectedPathsSetting
from services.contracts import protected_paths
from services.evidence import protected_check
from services.platform import protected_paths_setting as setting
from sqlmodel import Session

from routers.sod import owner_person

router = APIRouter(prefix="/v1", tags=["Company"])

_WHY = dict(protected_paths.PROTECTED)
_TESTS_WHY = "a test that holds the evidence to its behaviour"


def _state(session: Session, company_id: int) -> dict:
    return {
        "company_id": company_id,
        "enabled": setting.enabled(session, company_id),
        "patterns": [
            {"pattern": p, "why": _WHY.get(p, _TESTS_WHY)}
            for p in protected_check.patterns_in_force()
        ],
    }


@router.get("/companies/{company_id}/protected_paths")
def get_protected_paths(
    company_id: int,
    session: Session = Depends(get_session),  # noqa: B008 - FastAPI dependency marker.
    user: User | None = Depends(current_user_if_required),  # noqa: B008 - FastAPI dependency marker.
):
    """Whether the company's protected paths hold, and the list in force."""
    require_company_role(session, user, company_id, "Viewer")
    return _state(session, company_id)


@router.put("/companies/{company_id}/protected_paths")
def put_protected_paths(
    company_id: int,
    payload: ProtectedPathsSetting,
    session: Session = Depends(get_session),  # noqa: B008 - FastAPI dependency marker.
    user: User | None = Depends(current_user_if_required),  # noqa: B008 - FastAPI dependency marker.
):
    """Turn the protected paths on or off (a signed-in Owner, never a token; audited)."""
    actor = owner_person(session, user, company_id)
    try:
        setting.set_enabled(
            session, company_id, payload.enabled, by=actor.email, user_id=actor.id
        )
    except setting.SettingRefused as exc:
        raise HTTPException(status_code=exc.status, detail=exc.detail) from exc
    return _state(session, company_id)
