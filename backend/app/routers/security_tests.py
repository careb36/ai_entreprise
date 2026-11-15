"""Security tests router — a project's OWASP WSTG plan, checklist and people's reviews (issue #539).

Endpoints:
- ``GET  /v1/companies/{id}/security/wstg``          — the plan and each test's status (Viewer).
- ``GET  /v1/companies/{id}/security/wstg/reviews``  — the recorded reviews, newest first (Viewer).
- ``POST /v1/companies/{id}/security/wstg/reviews``  — record a person's review of one test (Manager; audited).

The plan is the rule table of ``services/planning/wstg_plan.py`` over the
company's records; a status comes only from a Verifier's observation or a
review recorded here (``project_docs.wstg_status``). A review names the
person who reviewed, the date and the artifact it rests on; the backend
computes the record's digest, never taking one from the request. The
documentation pack's checklist and security report show the same rows.
"""

from datetime import UTC, date, datetime
from typing import Any, Literal

from auth_policy import current_user_if_required, require_company_role
from database import get_session
from fastapi import APIRouter, Depends, HTTPException
from models import Company, CompanyMember, User, WstgReview
from pydantic import Field, field_validator
from schemas import RequestModel
from services.planning import wstg_plan
from services.planning.project_docs import collect, wstg_status
from services.planning.project_docs.refs import digest
from services.platform import wstg_catalogue
from services.platform.agent_service import AgentService
from sqlmodel import Session, select

router = APIRouter(tags=["Core"])


class ReviewRequest(RequestModel):
    wstg_id: str = Field(min_length=5, max_length=40)
    result: Literal["pass", "fail", "not_applicable"]
    reviewer_name: str = Field(min_length=1, max_length=120)
    reviewed_on: date
    artifact: str = Field(min_length=1, max_length=500)
    note: str | None = Field(default=None, max_length=1000)

    @field_validator("reviewer_name", "artifact", "note")
    @classmethod
    def _one_line(cls, value: str | None) -> str | None:
        if value is None:
            return None
        text = " ".join(value.split())
        return text or None


def _actor(user: User | None) -> str:
    return user.email if user is not None else "local demo"


def _company(
    session: Session, user: User | None, company_id: int, role: str
) -> Company:
    company = session.get(Company, company_id)
    if company is None:
        raise HTTPException(status_code=404, detail="Company not found")
    require_company_role(session, user, company.id, role)
    return company


def _recorder_role(session: Session, user: User | None, company_id: int) -> str:
    if user is None:
        return "Owner"
    member = session.exec(
        select(CompanyMember).where(
            CompanyMember.company_id == company_id, CompanyMember.user_id == user.id
        )
    ).first()
    return str(member.human_role) if member and member.human_role else "Manager"


def _review(r: WstgReview) -> dict[str, Any]:
    return {
        "id": r.id,
        "company_id": r.company_id,
        "wstg_id": r.wstg_id,
        "result": r.result,
        "reviewer_name": r.reviewer_name,
        "reviewer_role": r.reviewer_role,
        "reviewed_on": r.reviewed_on,
        "artifact": r.artifact,
        "note": r.note,
        "record_sha256": r.record_sha256,
        "created_at": r.created_at.isoformat() if r.created_at else None,
    }


@router.get("/v1/companies/{company_id}/security/wstg")
async def get_plan(
    company_id: int,
    session: Session = Depends(get_session),  # noqa: B008 - FastAPI dependency marker.
    user: User | None = Depends(current_user_if_required),  # noqa: B008 - FastAPI dependency marker.
):
    """The plan (signals and rows) with each row's status and what set it."""
    company = _company(session, user, company_id, "Viewer")
    snapshot = collect.snapshot(session, company)
    plan = wstg_plan.plan(snapshot)
    rows = wstg_status.rows(snapshot, plan)
    return {
        "company_id": company.id,
        "catalogue": {
            **plan["catalogue"],
            "attribution": wstg_catalogue.attribution(),
        },
        "signals": plan["signals"],
        "counts": wstg_status.counts(rows),
        "rows": rows,
    }


@router.get("/v1/companies/{company_id}/security/wstg/reviews")
async def list_reviews(
    company_id: int,
    session: Session = Depends(get_session),  # noqa: B008 - FastAPI dependency marker.
    user: User | None = Depends(current_user_if_required),  # noqa: B008 - FastAPI dependency marker.
):
    company = _company(session, user, company_id, "Viewer")
    rows = session.exec(
        select(WstgReview)
        .where(WstgReview.company_id == company.id)
        .order_by(WstgReview.id.desc())
    ).all()
    return {"reviews": [_review(r) for r in rows]}


@router.post("/v1/companies/{company_id}/security/wstg/reviews", status_code=201)
async def record_review(
    company_id: int,
    req: ReviewRequest,
    session: Session = Depends(get_session),  # noqa: B008 - FastAPI dependency marker.
    user: User | None = Depends(current_user_if_required),  # noqa: B008 - FastAPI dependency marker.
):
    """A Manager records a person's review of one WSTG test: who, when, on what artifact, the result.

    422 for a test the catalogue does not hold, a date in the future, or
    ``not_applicable`` without a note saying why.
    """
    company = _company(session, user, company_id, "Manager")
    wstg_id = wstg_catalogue.versioned(req.wstg_id)
    if wstg_id is None:
        raise HTTPException(
            status_code=422, detail=f"No WSTG test {req.wstg_id!r} in the catalogue"
        )
    if req.reviewed_on > datetime.now(UTC).date():
        raise HTTPException(status_code=422, detail="The review date is in the future")
    if req.result == "not_applicable" and not req.note:
        raise HTTPException(
            status_code=422,
            detail="A test marked not applicable needs a note saying why",
        )
    review = WstgReview(
        company_id=company.id,
        wstg_id=wstg_id,
        result=req.result,
        reviewer_name=req.reviewer_name,
        reviewer_role=_recorder_role(session, user, company.id),
        reviewed_on=req.reviewed_on.isoformat(),
        artifact=req.artifact,
        note=req.note,
        recorded_by=_actor(user),
    )
    review.record_sha256 = digest(
        {
            "company_id": review.company_id,
            "wstg_id": review.wstg_id,
            "result": review.result,
            "reviewer_name": review.reviewer_name,
            "reviewed_on": review.reviewed_on,
            "artifact": review.artifact,
            "note": review.note,
            "created_at": review.created_at.isoformat(),
        }
    )
    session.add(review)
    session.flush()
    AgentService().audit_interaction(
        session=session,
        company_id=company.id,
        interaction_id=f"wstg-review:{review.id}",
        agent_id=f"human:{_actor(user)}",
        action="wstg_review_recorded",
        details={"wstg_id": wstg_id, "reviewed_on": review.reviewed_on},
        result={"result": review.result, "record_sha256": review.record_sha256},
        decision_signature=None,
        cost_tokens=0,
    )
    session.commit()
    session.refresh(review)
    return _review(review)
