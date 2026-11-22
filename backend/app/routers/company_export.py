"""A company's configuration as one file, without its secrets (issue #774).

- ``GET /v1/companies/{company_id}/export`` — Owner only: the file holds every
  agent's prompt and the company's settings. Audited
  (``services/management/company_export.py``).
"""

from auth_policy import current_user_if_required, require_company_role
from database import get_session
from fastapi import APIRouter, Depends, HTTPException
from models import User
from services.loop import run_window_gate as gate
from services.management import company_export
from services.platform import sod
from sqlmodel import Session

router = APIRouter(prefix="/v1", tags=["Company"])


@router.get("/companies/{company_id}/export")
async def export_company(
    company_id: int,
    session: Session = Depends(get_session),  # noqa: B008
    user: User | None = Depends(current_user_if_required),  # noqa: B008
):
    """The company's configuration, secrets left out and listed (Owner)."""
    require_company_role(session, user, company_id, "Owner")
    try:
        return company_export.export(
            session, company_id, gate.actor_fields(sod.actor_of(session, user))
        )
    except ValueError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
