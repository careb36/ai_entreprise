"""
Inbox router — what the company needs a person for.

``GET /v1/inbox`` lists, for one company, the decisions only a person can
take: tasks blocked until someone acts, procedure proposals waiting for a
Manager, and agent runs nobody has approved or rejected. The agents do the
rest on their own, so this is the whole of a person's queue.
"""

from auth_policy import current_user_if_required, require_company_role
from database import get_session
from fastapi import APIRouter, Depends, Query
from models import AgentRun, AgentRunApproval, BacklogItem, Procedure, Sprint, User
from services.board import block_reason, blocked_item_next_step
from services.carry_over import carried_over
from sqlalchemy import func
from sqlmodel import Session, select

router = APIRouter(prefix="/v1", tags=["Company"])

REASON_LIMIT = 300


@router.get("/inbox")
async def inbox(
    company_id: int,
    limit: int = Query(100, ge=1, le=500),
    session: Session = Depends(get_session),  # noqa: B008 - FastAPI dependency marker.
    user: User | None = Depends(current_user_if_required),  # noqa: B008 - FastAPI dependency marker.
):
    """The company's open decisions for a person, newest first in each list."""
    require_company_role(session, user, company_id, "Viewer")

    rows = session.exec(
        select(BacklogItem, Sprint)
        .join(Sprint, Sprint.id == BacklogItem.sprint_id)
        .where(Sprint.company_id == company_id, BacklogItem.status == "blocked")
        .order_by(BacklogItem.updated_at.desc(), BacklogItem.id.desc())
    ).all()
    blocked_tasks = []
    for item, sprint in rows:
        next_step = blocked_item_next_step(item.comments, item.assignee)
        if next_step not in (None, "frozen"):
            continue  # the worker still retries or re-reviews it on its own
        if carried_over(item):
            continue  # it got another attempt in a later sprint
        blocked_tasks.append(
            {
                "id": item.id,
                "title": item.title,
                "assignee": item.assignee,
                "sprint_id": sprint.id,
                "sprint_name": sprint.sprint_name,
                "sprint_status": sprint.status,
                "reason": block_reason(item.comments)[:REASON_LIMIT],
                "frozen": next_step == "frozen",
                "updated_at": item.updated_at.isoformat() if item.updated_at else None,
            }
        )
        if len(blocked_tasks) == limit:
            break

    proposals = session.exec(
        select(Procedure)
        .where(Procedure.company_id == company_id, Procedure.status == "proposed")
        .order_by(Procedure.id.desc())
        .limit(limit)
    ).all()

    decided = select(AgentRunApproval.agent_run_id).where(
        AgentRunApproval.company_id == company_id
    )
    pending_runs = session.exec(
        select(func.count(AgentRun.id)).where(
            AgentRun.company_id == company_id, AgentRun.id.not_in(decided)
        )
    ).one()

    return {
        "blocked_tasks": blocked_tasks,
        "proposals": [
            {
                "id": p.id,
                "role": p.role,
                "title": p.title,
                "proposed_by": p.proposed_by,
                "source_sprint_id": p.source_sprint_id,
                "created_at": p.created_at.isoformat() if p.created_at else None,
            }
            for p in proposals
        ],
        "pending_runs": int(pending_runs or 0),
        "total": len(blocked_tasks) + len(proposals) + int(pending_runs or 0),
    }
