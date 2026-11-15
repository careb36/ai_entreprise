"""Segregation of duties: the company's policy, its single-person exception and
the report (issue #543; ``docs/design/SEGREGATION_OF_DUTIES.md``).

The rule itself is applied at each approval point (``services/platform/sod.py``).
The policy is written only by a signed-in human Owner (an API token is refused,
since the policy decides who may approve); every change is audited.

- ``GET    /v1/companies/{id}/sod/policy``     the policy (Owner)
- ``PUT    /v1/companies/{id}/sod/policy``     change mode, review interval, inactivity
- ``POST   /v1/companies/{id}/sod/exception``  grant the single-person exception, with a reason
- ``DELETE /v1/companies/{id}/sod/exception``  withdraw it
- ``GET    /v1/companies/{id}/sod/report``     the matrix, the approvals, the refusals (Manager)
"""

from __future__ import annotations

from auth_policy import current_user_if_required, require_company_role
from database import get_session
from fastapi import APIRouter, Depends, HTTPException
from fastapi.responses import JSONResponse
from models import User
from pydantic import BaseModel, Field
from services.management import sod_report
from services.platform import sod
from services.platform.agent_service import AgentService
from sqlmodel import Session

router = APIRouter(prefix="/v1", tags=["Company"])


class PolicyUpdate(BaseModel):
    mode: str | None = Field(default=None, pattern="^(off|high_risk|all)$")
    review_interval_days: int | None = Field(
        default=None, ge=sod.INTERVAL_RANGE[0], le=sod.INTERVAL_RANGE[1]
    )
    inactive_days: int | None = Field(
        default=None, ge=sod.INACTIVE_RANGE[0], le=sod.INACTIVE_RANGE[1]
    )


class ExceptionGrant(BaseModel):
    reason: str = Field(min_length=10, max_length=sod.REASON_MAX)


def owner_person(session: Session, user: User | None, company_id: int) -> User:
    """A signed-in human Owner of *company_id*; a token is refused."""
    if user is None:
        raise HTTPException(status_code=401, detail="Not authenticated")
    if getattr(user, "_api_token_company_id", None) is not None:
        raise HTTPException(
            status_code=403, detail="This is decided by a person, not a token"
        )
    require_company_role(session, user, company_id, "Owner")
    return user


def _audit(session: Session, company_id: int, actor: User, action: str, **result):
    AgentService().audit_interaction(
        session=session,
        company_id=company_id,
        interaction_id=f"sod:{company_id}",
        agent_id=f"human:{actor.email}",
        action=action,
        details={"company_id": company_id},
        result=result,
        decision_signature=None,
        cost_tokens=0,
    )


def _refused(exc: sod.PolicyRefused) -> HTTPException:
    return HTTPException(status_code=exc.status, detail=exc.detail)


@router.get("/companies/{company_id}/sod/policy")
async def get_policy(
    company_id: int,
    session: Session = Depends(get_session),  # noqa: B008 - FastAPI dependency marker.
    user: User | None = Depends(current_user_if_required),  # noqa: B008 - FastAPI dependency marker.
):
    owner_person(session, user, company_id)
    return sod.serialize(session, sod.policy_of(session, company_id))


@router.put("/companies/{company_id}/sod/policy")
async def update_policy(
    company_id: int,
    req: PolicyUpdate,
    session: Session = Depends(get_session),  # noqa: B008 - FastAPI dependency marker.
    user: User | None = Depends(current_user_if_required),  # noqa: B008 - FastAPI dependency marker.
):
    owner = owner_person(session, user, company_id)
    try:
        changed = sod.update_policy(
            session, company_id, owner.email, req.model_dump(exclude_none=True)
        )
    except sod.PolicyRefused as exc:
        raise _refused(exc) from exc
    if changed:
        _audit(session, company_id, owner, "sod_policy_updated", changed=changed)
    session.commit()
    return sod.serialize(session, sod.policy_of(session, company_id))


@router.post("/companies/{company_id}/sod/exception")
async def grant_exception(
    company_id: int,
    req: ExceptionGrant,
    session: Session = Depends(get_session),  # noqa: B008 - FastAPI dependency marker.
    user: User | None = Depends(current_user_if_required),  # noqa: B008 - FastAPI dependency marker.
):
    """The single-person exception: only while one person can approve."""
    owner = owner_person(session, user, company_id)
    try:
        policy = sod.grant_solo(session, company_id, owner.email, req.reason)
    except sod.PolicyRefused as exc:
        raise _refused(exc) from exc
    _audit(
        session,
        company_id,
        owner,
        "sod_exception_granted",
        reason=policy.solo_reason,
        approvers=len(sod.approvers(session, company_id)),
    )
    session.commit()
    return sod.serialize(session, sod.policy_of(session, company_id))


@router.delete("/companies/{company_id}/sod/exception")
async def withdraw_exception(
    company_id: int,
    session: Session = Depends(get_session),  # noqa: B008 - FastAPI dependency marker.
    user: User | None = Depends(current_user_if_required),  # noqa: B008 - FastAPI dependency marker.
):
    owner = owner_person(session, user, company_id)
    sod.revoke_solo(session, company_id, owner.email)
    _audit(session, company_id, owner, "sod_exception_withdrawn")
    session.commit()
    return sod.serialize(session, sod.policy_of(session, company_id))


@router.get("/companies/{company_id}/sod/report")
async def get_report(
    company_id: int,
    session: Session = Depends(get_session),  # noqa: B008 - FastAPI dependency marker.
    user: User | None = Depends(current_user_if_required),  # noqa: B008 - FastAPI dependency marker.
):
    """Every approval with its requester and status, the refusals, the matrix."""
    require_company_role(session, user, company_id, "Manager")
    return JSONResponse(
        sod_report.report(session, company_id),
        headers={"Cache-Control": "no-store"},
    )
