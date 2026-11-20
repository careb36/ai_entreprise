"""The periodic access review and the service accounts inventory (issue #543;
``docs/design/SEGREGATION_OF_DUTIES.md``).

Only a signed-in human Owner reads or completes them; an API token is refused.

- ``GET  /v1/companies/{id}/access-review``     the schedule, what to review, the history
- ``POST /v1/companies/{id}/access-review``     complete it: each item confirmed or removed
- ``GET  /v1/companies/{id}/service-accounts``  the credentials that act without a person
"""

from __future__ import annotations

from auth_policy import current_user_if_required
from database import get_session
from fastapi import APIRouter, Depends, HTTPException
from fastapi.responses import JSONResponse
from models import User
from pydantic import Field
from schemas import RequestModel
from services.platform import access_review, service_accounts
from sqlmodel import Session

from routers.sod import owner_person

router = APIRouter(prefix="/v1", tags=["Company"])

NO_STORE = {"Cache-Control": "no-store"}


class Decision(RequestModel):
    kind: str = Field(
        pattern="^(member|token|exclusion|manual_exception|manager_pair|standard_exception|standards_single_owner)$"
    )
    id: int
    decision: str = Field(pattern="^(confirm|remove)$")


class ReviewCompletion(RequestModel):
    decisions: list[Decision] = Field(max_length=2000)
    note: str | None = Field(default=None, max_length=access_review.NOTE_MAX)


@router.get("/companies/{company_id}/access-review")
async def get_access_review(
    company_id: int,
    session: Session = Depends(get_session),  # noqa: B008 - FastAPI dependency marker.
    user: User | None = Depends(current_user_if_required),  # noqa: B008 - FastAPI dependency marker.
):
    owner_person(session, user, company_id)
    return JSONResponse(
        {
            "company_id": company_id,
            "schedule": access_review.schedule_of(session, company_id),
            **access_review.listing(session, company_id),
            "history": access_review.history(session, company_id),
        },
        headers=NO_STORE,
    )


@router.post("/companies/{company_id}/access-review", status_code=201)
async def complete_access_review(
    company_id: int,
    req: ReviewCompletion,
    session: Session = Depends(get_session),  # noqa: B008 - FastAPI dependency marker.
    user: User | None = Depends(current_user_if_required),  # noqa: B008 - FastAPI dependency marker.
):
    owner = owner_person(session, user, company_id)
    try:
        review = access_review.complete(
            session,
            company_id,
            owner,
            [d.model_dump() for d in req.decisions],
            req.note,
        )
    except access_review.ReviewRefused as exc:
        session.rollback()
        raise HTTPException(status_code=exc.status, detail=exc.detail) from exc
    return {
        "id": review.id,
        "completed_at": review.completed_at.isoformat(),
        "on_time": review.on_time,
        "summary": review.summary,
        "schedule": access_review.schedule_of(session, company_id),
    }


@router.get("/companies/{company_id}/service-accounts")
async def get_service_accounts(
    company_id: int,
    session: Session = Depends(get_session),  # noqa: B008 - FastAPI dependency marker.
    user: User | None = Depends(current_user_if_required),  # noqa: B008 - FastAPI dependency marker.
):
    owner_person(session, user, company_id)
    return JSONResponse(
        service_accounts.inventory(session, company_id), headers=NO_STORE
    )
