"""A company's acknowledgements of a weaker guarantee (issue #766).

- ``GET /v1/companies/{id}/acknowledgements`` — what the company's
  configuration needs, what is in force, and the history (Viewer).
- ``POST /v1/companies/{id}/acknowledgements`` — record one: ``kind``
  (``tests_off``, ``host_sandbox``), a ``reason`` and ``days`` (1 to 90,
  30 by default) (Owner; audited ``acknowledgement_recorded``).
- ``DELETE /v1/companies/{id}/acknowledgements/{ack_id}`` — withdraw it;
  the row stays (Owner; audited ``acknowledgement_withdrawn``).

``services/platform/acknowledgements.py`` holds the rules;
``services/loop/preconditions.py`` refuses to start work without them.
"""

from __future__ import annotations

from auth_policy import current_user_if_required, require_company_role
from database import get_session
from fastapi import APIRouter, Depends, HTTPException
from models import Company, User
from pydantic import Field
from schemas import RequestModel
from services.loop import preconditions
from services.platform import acknowledgements, sod
from sqlmodel import Session

router = APIRouter(prefix="/v1", tags=["Core"])


class AcknowledgementRequest(RequestModel):
    kind: str
    reason: str = Field(max_length=2000)
    days: int = acknowledgements.DEFAULT_DAYS


def _company(session: Session, company_id: int) -> Company:
    company = session.get(Company, company_id)
    if company is None:
        raise HTTPException(status_code=404, detail="Company not found")
    return company


def _state(session: Session, company: Company) -> dict:
    needed = preconditions.required(company)
    in_force = acknowledgements.active(session, company.id)
    return {
        "company_id": company.id,
        "required": needed,
        "missing": acknowledgements.missing(session, company.id, needed),
        "in_force": [acknowledgements.payload(row) for row in in_force.values()],
        "history": [
            acknowledgements.payload(row)
            for row in acknowledgements.history(session, company.id)
        ],
    }


def _actor(session: Session, user: User | None) -> tuple[str, int | None]:
    actor = sod.actor_of(session, user)
    return (actor.label if actor else "") or "A person", (
        actor.user_id if actor else None
    )


@router.get("/companies/{company_id}/acknowledgements")
def get_acknowledgements(
    company_id: int,
    session: Session = Depends(get_session),  # noqa: B008 - FastAPI dependency marker.
    user: User | None = Depends(current_user_if_required),  # noqa: B008 - FastAPI dependency marker.
):
    """What the company's configuration needs and what is recorded (Viewer)."""
    require_company_role(session, user, company_id, "Viewer")
    return _state(session, _company(session, company_id))


@router.post("/companies/{company_id}/acknowledgements")
def post_acknowledgement(
    company_id: int,
    req: AcknowledgementRequest,
    session: Session = Depends(get_session),  # noqa: B008 - FastAPI dependency marker.
    user: User | None = Depends(current_user_if_required),  # noqa: B008 - FastAPI dependency marker.
):
    """Record an acknowledgement (Owner; audited)."""
    require_company_role(session, user, company_id, "Owner")
    company = _company(session, company_id)
    by, user_id = _actor(session, user)
    try:
        acknowledgements.record(
            session,
            company_id,
            req.kind,
            req.reason,
            by=by,
            user_id=user_id,
            days=req.days,
        )
    except acknowledgements.Refused as exc:
        raise HTTPException(status_code=exc.status, detail=exc.detail) from exc
    return _state(session, company)


@router.delete("/companies/{company_id}/acknowledgements/{acknowledgement_id}")
def delete_acknowledgement(
    company_id: int,
    acknowledgement_id: int,
    session: Session = Depends(get_session),  # noqa: B008 - FastAPI dependency marker.
    user: User | None = Depends(current_user_if_required),  # noqa: B008 - FastAPI dependency marker.
):
    """Withdraw an acknowledgement; the record stays (Owner; audited)."""
    require_company_role(session, user, company_id, "Owner")
    company = _company(session, company_id)
    by, user_id = _actor(session, user)
    try:
        acknowledgements.withdraw(
            session, company_id, acknowledgement_id, by=by, user_id=user_id
        )
    except acknowledgements.Refused as exc:
        raise HTTPException(status_code=exc.status, detail=exc.detail) from exc
    return _state(session, company)
