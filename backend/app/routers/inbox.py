"""
Inbox router — what the company needs a person for.

``GET /v1/inbox`` lists, for one company, the decisions only a person can
take: tasks blocked until someone acts (with the ScrumMaster's triage of each,
when it answered the escalation; ``risk_hold`` marks an approved high-risk
delivery waiting for a person to accept it), procedure proposals waiting for a Manager,
the agent runs of other producers nobody has approved or rejected (the
company's own task worker and kickoff runs are decided by the review loop),
and the sprints whose integration stopped at a merge conflict or a failing
suite, missed a task's branch or could not start (``integrations``, listed up
to the limit and counted in full; ``services/release/integration.py``). An archived
sprint's tasks and integrations are left out (``services/loop/sprint_archive.py``).

With ``MANAGER_AGENT_ENABLED`` each item carries the Manager agent's latest
``suggestion`` (a claim, ``services/manager_agent.py``), and
``agent_decisions`` lists what it decided alone under the company's policy,
each with its rule, for the client to override in one click.
The agents do the rest on their own, so this is the whole of a person's
queue.
"""

from auth_policy import current_user_if_required, require_company_role
from config import settings
from database import get_session
from fastapi import APIRouter, Depends, Query
from models import AgentRun, AgentRunApproval, BacklogItem, Procedure, Sprint, User
from models.agent_run import IN_HOUSE_PRODUCERS
from models.manager import ManagerDecision
from services import integration, sprint_archive
from services.carry_over import carried_over, carry_mark
from services.learning import scrum_master
from services.tasks import task_events, task_projection
from sqlalchemy import func
from sqlmodel import Session, select

from routers.manager import serialize_decision

router = APIRouter(prefix="/v1", tags=["Company"])

REASON_LIMIT = 300
AGENT_DECISIONS_LIMIT = 20


def _suggestions(session: Session, company_id: int) -> dict[tuple[str, int], dict]:
    """The Manager agent's latest row per inbox item, serialized."""
    if not settings.MANAGER_AGENT_ENABLED:
        return {}
    latest: dict[tuple[str, int], dict] = {}
    for row in session.exec(
        select(ManagerDecision)
        .where(ManagerDecision.company_id == company_id)
        .order_by(ManagerDecision.id.desc())
    ).all():
        latest.setdefault((row.item_kind, row.item_id), serialize_decision(row))
    return latest


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
        .where(
            Sprint.company_id == company_id,
            BacklogItem.status == "blocked",
            sprint_archive.listed(),
        )
        .order_by(BacklogItem.updated_at.desc(), BacklogItem.id.desc())
    ).all()
    blocked_tasks = []
    blocked_total = 0
    # Every candidate's events, one query, shared by the projections below.
    histories = task_events.for_tasks(session, [item.id for item, _ in rows])
    records = {
        item.id: task_projection.record_of(session, item, histories) for item, _ in rows
    }
    # The ScrumMaster's answers to the escalations, one query for the page.
    triages = scrum_master.triages_for(session, [item for item, _ in rows], records)
    suggestions = _suggestions(session, company_id)
    for item, sprint in rows:
        # From the task's events when they render its whole thread, else
        # from its comments (services/task_projection.py).
        next_step = records[item.id].next_step()
        if next_step not in (None, "frozen"):
            continue  # the worker still retries or re-reviews it on its own
        if carried_over(item, records[item.id]):
            continue  # it got another attempt in a later sprint
        blocked_total += 1
        if len(blocked_tasks) == limit:
            continue  # listed up to the limit, counted in full
        blocked_tasks.append(
            {
                "id": item.id,
                "title": item.title,
                "assignee": item.assignee,
                "sprint_id": sprint.id,
                "sprint_name": sprint.sprint_name,
                "sprint_status": sprint.status,
                "reason": records[item.id].block_reason()[:REASON_LIMIT],
                # The ScrumMaster's answer to the escalation, a claim for the
                # person to weigh: another attempt (with a brief) or a person.
                "triage": triages.get(item.id),
                "frozen": next_step == "frozen",
                # Approved by QA, but high risk (services/risk.py): a person
                # accepts it (marks it done) or sends it back to work.
                "risk_hold": records[item.id].awaits_risk_acceptance(),
                "suggestion": suggestions.get(("task", item.id)),
                # The Manager agent marked it for the next sprint (issue #394):
                # the "Plan a sprint" form ticks it; a person may untick it.
                "marked_for_carry_over": carry_mark(item, records[item.id]) is True,
                "updated_at": item.updated_at.isoformat() if item.updated_at else None,
            }
        )

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
            AgentRun.company_id == company_id,
            AgentRun.id.not_in(decided),
            # The company's own runs are decided by the review loop.
            AgentRun.producer.not_in(IN_HOUSE_PRODUCERS),
        )
    ).one()

    pending_run_rows = []
    if settings.MANAGER_AGENT_ENABLED:
        pending_run_rows = session.exec(
            select(AgentRun)
            .where(
                AgentRun.company_id == company_id,
                AgentRun.id.not_in(decided),
                AgentRun.producer.not_in(IN_HOUSE_PRODUCERS),
            )
            .order_by(AgentRun.id.desc())
            .limit(20)
        ).all()

    integrations = integration.waiting_for_a_person(session, company_id)
    agent_decisions = []
    if settings.MANAGER_AGENT_ENABLED:
        agent_decisions = [
            serialize_decision(row)
            for row in session.exec(
                select(ManagerDecision)
                .where(
                    ManagerDecision.company_id == company_id,
                    ManagerDecision.decided_by.is_not(None),
                )
                .order_by(ManagerDecision.id.desc())
                .limit(AGENT_DECISIONS_LIMIT)
            ).all()
        ]

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
                "suggestion": suggestions.get(("procedure", p.id)),
            }
            for p in proposals
        ],
        "pending_runs": int(pending_runs or 0),
        "run_suggestions": [
            {
                "id": r.id,
                "producer": r.producer,
                "suggestion": suggestions.get(("run", r.id)),
            }
            for r in pending_run_rows
        ],
        "integrations": integrations[:limit],
        "integrations_total": len(integrations),
        "agent_decisions": agent_decisions,
        "blocked_total": blocked_total,
        "total": blocked_total
        + len(proposals)
        + int(pending_runs or 0)
        + len(integrations),
    }
