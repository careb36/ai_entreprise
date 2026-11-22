"""A company's configuration as one file, without its secrets (issue #774).

- ``GET /v1/companies/{company_id}/export`` — Owner only: the file holds every
  agent's prompt and the company's settings. Audited
  (``services/management/company_export.py``).
- ``POST /v1/companies/import`` — any signed-in person: a new company from such
  a file, which they own (``services/management/company_import.py``).
"""

from typing import Any

from auth_policy import (
    current_user_if_required,
    require_authenticated,
    require_company_role,
)
from database import get_session
from fastapi import APIRouter, Depends, HTTPException
from models import User
from pydantic import Field
from schemas import RequestModel
from services.loop import run_window_gate as gate
from services.management import company_export, company_import
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


class CompanyImportRequest(RequestModel):
    """An export file, and the new company's name when it should differ."""

    file: dict[str, Any]
    company_name: str | None = Field(default=None, max_length=200)


@router.post("/companies/import", status_code=201)
async def import_company(
    req: CompanyImportRequest,
    session: Session = Depends(get_session),  # noqa: B008
    user: User | None = Depends(current_user_if_required),  # noqa: B008
):
    """A new company from an export file; the caller owns it. What a file may
    not set, and what failed its check, is listed instead of applied."""
    require_authenticated(user)
    try:
        return company_import.import_company(
            session,
            req.file,
            user.id if user is not None else None,
            gate.actor_fields(sod.actor_of(session, user)),
            req.company_name,
        )
    except company_import.ImportRefused as exc:
        raise HTTPException(status_code=exc.status, detail=exc.detail) from exc
