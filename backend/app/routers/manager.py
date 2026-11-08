"""
The Manager agent's routes (issue #331).

- ``GET  /v1/companies/{id}/manager_policy`` — the policy and its rule catalogue (Viewer).
- ``PUT  /v1/companies/{id}/manager_policy`` — replace it (Manager); a rule of
  the confirmation set is refused (422). The agent never writes it.
- ``GET  /v1/manager_decisions?company_id=`` — what the agent suggested and
  decided, newest first (Viewer).
- ``POST /v1/manager_decisions/{id}/override`` — take back a decision the
  agent took alone (Manager); counted in the record.
- ``GET  /v1/manager_record?company_id=`` — its suggestions against the
  client's decisions and its override rate, by category (Viewer).
- ``GET  /v1/companies/{id}/brief`` — the client's brief (Viewer).
"""

from datetime import datetime

from auth_policy import current_user_if_required, require_company_role
from database import get_session
from fastapi import APIRouter, Body, Depends, HTTPException, Query
from models import Company, User
from models.manager import ManagerDecision
from services import manager_actions, manager_brief, manager_policy, manager_record
from services.agent_service import AgentService
from sqlmodel import Session, select

router = APIRouter(prefix="/v1", tags=["Company"])


def _actor(user: User | None) -> str:
    return user.email if user is not None else "local demo"


def serialize_decision(row: ManagerDecision) -> dict:
    return {
        "id": row.id,
        "item_kind": row.item_kind,
        "item_id": row.item_id,
        "category": row.category,
        "suggested_decision": row.suggested_decision,
        "suggested_reason": row.suggested_reason,
        "suggested_by": row.suggested_by,
        "suggested_at": row.suggested_at.isoformat() if row.suggested_at else None,
        "decided_by": row.decided_by,
        "rule": row.rule,
        "outcome": row.outcome,
        "decided_at": row.decided_at.isoformat() if row.decided_at else None,
        "overridden_by": row.overridden_by,
        "overridden_at": row.overridden_at.isoformat() if row.overridden_at else None,
        "action_state": row.action_state,
        "audit_log_id": row.audit_log_id,
        "override_audit_log_id": row.override_audit_log_id,
    }


@router.get("/companies/{company_id}/manager_policy")
async def get_manager_policy(
    company_id: int,
    session: Session = Depends(get_session),  # noqa: B008 - FastAPI dependency marker.
    user: User | None = Depends(current_user_if_required),  # noqa: B008 - FastAPI dependency marker.
):
    require_company_role(session, user, company_id, "Viewer")
    return {
        "company_id": company_id,
        **manager_policy.serialize(
            manager_policy.load(session, company_id),
            manager_policy.row_for(session, company_id),
        ),
    }


@router.put("/companies/{company_id}/manager_policy")
async def put_manager_policy(
    company_id: int,
    payload: dict = Body(...),  # noqa: B008 - FastAPI dependency marker.
    session: Session = Depends(get_session),  # noqa: B008 - FastAPI dependency marker.
    user: User | None = Depends(current_user_if_required),  # noqa: B008 - FastAPI dependency marker.
):
    """Replace the company's policy (Manager). Audited."""
    require_company_role(session, user, company_id, "Manager")
    if session.get(Company, company_id) is None:
        raise HTTPException(status_code=404, detail="Company not found")
    try:
        policy = manager_policy.validate(payload)
    except manager_policy.PolicyError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    actor = _actor(user)
    row = manager_policy.save(session, company_id, policy, actor)
    session.flush()
    AgentService().audit_interaction(
        session=session,
        company_id=company_id,
        interaction_id=f"manager-policy:{company_id}",
        agent_id=f"human:{actor}",
        action="manager_policy_updated",
        details={"company_id": str(company_id)},
        result={
            "rules": policy.rules,
            "max_files": policy.max_files,
            "max_criteria": policy.max_criteria,
        },
        decision_signature=None,
        cost_tokens=0,
    )
    session.refresh(row)
    return {"company_id": company_id, **manager_policy.serialize(policy, row)}


@router.get("/manager_decisions")
async def list_manager_decisions(
    company_id: int = Query(...),
    limit: int = Query(50, ge=1, le=500),
    session: Session = Depends(get_session),  # noqa: B008 - FastAPI dependency marker.
    user: User | None = Depends(current_user_if_required),  # noqa: B008 - FastAPI dependency marker.
):
    require_company_role(session, user, company_id, "Viewer")
    rows = session.exec(
        select(ManagerDecision)
        .where(ManagerDecision.company_id == company_id)
        .order_by(ManagerDecision.id.desc())
        .limit(limit)
    ).all()
    return {"decisions": [serialize_decision(r) for r in rows]}


@router.post("/manager_decisions/{decision_id}/override")
async def override_manager_decision(
    decision_id: int,
    session: Session = Depends(get_session),  # noqa: B008 - FastAPI dependency marker.
    user: User | None = Depends(current_user_if_required),  # noqa: B008 - FastAPI dependency marker.
):
    """Take back a decision the Manager agent took alone (Manager)."""
    row = session.get(ManagerDecision, decision_id)
    if row is None:
        raise HTTPException(status_code=404, detail="Decision not found")
    require_company_role(session, user, row.company_id, "Manager")
    try:
        return manager_actions.override(session, row, _actor(user))
    except manager_actions.OverrideRefused as exc:
        session.rollback()
        raise HTTPException(status_code=exc.status, detail=exc.detail) from exc


@router.get("/manager_record")
async def get_manager_record(
    company_id: int = Query(...),
    session: Session = Depends(get_session),  # noqa: B008 - FastAPI dependency marker.
    user: User | None = Depends(current_user_if_required),  # noqa: B008 - FastAPI dependency marker.
):
    """How the Manager agent's suggestions and decisions fared with the client."""
    require_company_role(session, user, company_id, "Viewer")
    return manager_record.track_record(session, company_id)


@router.get("/companies/{company_id}/brief")
async def get_brief(
    company_id: int,
    since: datetime | None = Query(None),  # noqa: B008 - FastAPI dependency marker.
    session: Session = Depends(get_session),  # noqa: B008 - FastAPI dependency marker.
    user: User | None = Depends(current_user_if_required),  # noqa: B008 - FastAPI dependency marker.
):
    """What got done, what the agent decided, what waits for the client, the cost."""
    require_company_role(session, user, company_id, "Viewer")
    company = session.get(Company, company_id)
    if company is None:
        raise HTTPException(status_code=404, detail="Company not found")
    return manager_brief.brief(session, company, since)
