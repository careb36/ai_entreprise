"""A company's name, and deleting the company
(``services/platform/company_removal.py``).

- ``PATCH  /v1/companies/{id}``           rename it (Owner; audited ``company_renamed``)
- ``GET    /v1/companies/{id}/deletion``  what deleting it would remove, and what holds it back (signed-in Owner)
- ``DELETE /v1/companies/{id}``           delete it with every row it holds (signed-in Owner, the name typed back; audited ``company_deleted``)

Deleting needs a signed-in person who is Owner, never an API token, as an
owner action does: it cannot be undone. The body repeats the company's name,
so a stale tab or a wrong id deletes nothing. A sprint or an owner action
running holds it back (409).
"""

from __future__ import annotations

from auth_policy import current_user_if_required, require_company_role
from database import get_session
from fastapi import APIRouter, Depends, HTTPException
from models import User
from schemas import RequestModel
from services.platform import company_removal
from sqlmodel import Session

from routers.owner_actions import require_human_owner

router = APIRouter(prefix="/v1", tags=["Company"])


class RenameRequest(RequestModel):
    company_name: str


class DeleteRequest(RequestModel):
    confirm_name: str


def _refused(exc: company_removal.CompanyRemovalRefused) -> HTTPException:
    return HTTPException(status_code=exc.status, detail=exc.detail)


@router.patch("/companies/{company_id}")
def rename_company(
    company_id: int,
    payload: RenameRequest,
    session: Session = Depends(get_session),  # noqa: B008 - FastAPI dependency marker.
    user: User | None = Depends(current_user_if_required),  # noqa: B008 - FastAPI dependency marker.
):
    """Rename the company (Owner)."""
    require_company_role(session, user, company_id, "Owner")
    try:
        company = company_removal.rename(
            session, company_id, payload.company_name, user
        )
    except company_removal.CompanyRemovalRefused as exc:
        raise _refused(exc) from exc
    return {"id": company.id, "company_name": company.company_name}


@router.get("/companies/{company_id}/deletion")
def deletion_preview(
    company_id: int,
    session: Session = Depends(get_session),  # noqa: B008 - FastAPI dependency marker.
    user: User | None = Depends(current_user_if_required),  # noqa: B008 - FastAPI dependency marker.
):
    """The rows deleting the company would remove, and what holds it back."""
    require_human_owner(session, user, company_id)
    return {
        "rows": company_removal.counts(session, company_id),
        "blockers": company_removal.blockers(session, company_id),
    }


@router.delete("/companies/{company_id}")
def delete_company(
    company_id: int,
    payload: DeleteRequest,
    session: Session = Depends(get_session),  # noqa: B008 - FastAPI dependency marker.
    user: User | None = Depends(current_user_if_required),  # noqa: B008 - FastAPI dependency marker.
):
    """Delete the company with every row it holds (signed-in Owner)."""
    require_human_owner(session, user, company_id)
    try:
        return company_removal.delete_company(
            session, company_id, payload.confirm_name, user
        )
    except company_removal.CompanyRemovalRefused as exc:
        session.rollback()
        raise _refused(exc) from exc
