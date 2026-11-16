"""The client's brief (issue #331): what got done, what the Manager agent
decided and why, what it was not allowed to decide alone and why, what waits
for the client, and the cost so far.

Deterministic: every number is read from the board, the agent's decision
rows and the audit log; no model writes it. ``GET /v1/companies/{id}/brief``
serves it (Viewer) for the window that starts at ``since`` (the last day by
default).
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from typing import Any

from models import (
    AgentRun,
    AgentRunApproval,
    Architecture,
    AuditLog,
    BacklogItem,
    Company,
    Procedure,
    Specification,
    Sprint,
)
from models.agent_run import IN_HOUSE_PRODUCERS
from models.company import BusinessUnit
from models.manager import ManagerDecision
from sqlalchemy import func, or_
from sqlmodel import Session, select

from services.management import manager_agent, manager_record
from services.management.manager_actions import ACCEPTED_PREFIX, MANAGER_AGENT_NAME
from services.release import integration
from services.tasks import task_projection
from services.tasks.board import comment_list

LIST_LIMIT = 20
DEFAULT_WINDOW = timedelta(days=1)


def _naive(moment: datetime) -> datetime:
    return moment.astimezone(UTC).replace(tzinfo=None) if moment.tzinfo else moment


def _iso(moment: datetime | None) -> str | None:
    return moment.isoformat() if moment else None


def _completed_at(item: BacklogItem) -> datetime | None:
    """When the task got done: its last closing comment (an ``APPROVED:``
    verdict, a person's note or the Manager agent's acceptance), the same
    transition ``history_check`` reads; ``updated_at`` moves on any edit."""
    closing = None
    for comment in comment_list(item.comments):
        author = str(comment.get("author") or "").strip().lower()
        text = str(comment.get("text") or "").strip()
        if (
            author == "user"
            or text.upper().startswith("APPROVED:")
            or (
                author == MANAGER_AGENT_NAME.lower()
                and text.startswith(ACCEPTED_PREFIX)
            )
        ):
            closing = comment
    if closing is None:
        return None
    try:
        return _naive(datetime.fromisoformat(str(closing.get("created_at") or "")))
    except ValueError:
        # A closing comment written without its time: the last edit is all we have.
        return _naive(item.updated_at) if item.updated_at else None


def _done(session: Session, company_id: int, since: datetime) -> dict[str, Any]:
    # A task done in the window was last edited in it too; the comments say when.
    candidates = session.exec(
        select(BacklogItem)
        .join(Sprint, Sprint.id == BacklogItem.sprint_id)
        .where(
            Sprint.company_id == company_id,
            BacklogItem.status == "done",
            BacklogItem.updated_at >= since,
        )
    ).all()
    done = sorted(
        (
            (moment, item)
            for item in candidates
            if (moment := _completed_at(item)) is not None and moment >= since
        ),
        key=lambda pair: (pair[0], pair[1].id),
        reverse=True,
    )
    return {
        "count": len(done),
        "tasks": [
            {
                "id": i.id,
                "title": i.title,
                "sprint_id": i.sprint_id,
                "completed_at": _iso(moment),
            }
            for moment, i in done[:LIST_LIMIT]
        ],
    }


def _decisions(session: Session, company_id: int, since: datetime) -> dict[str, Any]:
    """Suggestions, decisions and overrides, each counted by its own moment."""
    rows = session.exec(
        select(ManagerDecision)
        .where(
            ManagerDecision.company_id == company_id,
            or_(
                ManagerDecision.suggested_at >= since,
                ManagerDecision.decided_at >= since,
                ManagerDecision.overridden_at >= since,
            ),
        )
        .order_by(ManagerDecision.id.desc())
    ).all()
    suggested = [r for r in rows if r.suggested_at and r.suggested_at >= since]
    alone = [r for r in rows if r.decided_by and r.decided_at and r.decided_at >= since]
    overridden = [r for r in rows if r.overridden_at and r.overridden_at >= since]
    # Suggestions the policy did not let the agent take alone, with the
    # platform's sentence; the model's own reason stays beside it as a claim.
    refused = [r for r in suggested if r.refusal_reason]
    return {
        "suggested": sum(1 for r in suggested if r.suggested_decision),
        "unusable": sum(1 for r in suggested if r.suggested_decision is None),
        "taken_alone": len(alone),
        "overridden": len(overridden),
        "refused": len(refused),
        "refusals": [
            {
                "id": r.id,
                "item_kind": r.item_kind,
                "item_id": r.item_id,
                "category": r.category,
                "decision": r.suggested_decision,
                "reason": r.suggested_reason,
                "action_state": r.action_state,
                "refusal_reason": r.refusal_reason,
            }
            for r in refused[:LIST_LIMIT]
        ],
        "decisions": [
            {
                "id": r.id,
                "item_kind": r.item_kind,
                "item_id": r.item_id,
                "category": r.category,
                "decision": r.suggested_decision,
                "rule": r.rule,
                "reason": r.suggested_reason,
                "outcome": r.outcome,
                "decided_at": _iso(r.decided_at),
                "overridden_by": r.overridden_by,
            }
            for r in alone[:LIST_LIMIT]
        ],
    }


def _waiting(session: Session, company_id: int) -> dict[str, int]:
    tasks = manager_agent.waiting_tasks(session, company_id)
    records = task_projection.records_for(session, tasks)
    holds = sum(1 for t in tasks if records[t.id].awaits_risk_acceptance())
    decided = select(AgentRunApproval.agent_run_id)

    def count(statement) -> int:
        return int(session.exec(statement).one() or 0)

    waiting = {
        "blocked_tasks": len(tasks) - holds,
        "risk_holds": holds,
        "proposals": count(
            select(func.count(Procedure.id)).where(
                Procedure.company_id == company_id, Procedure.status == "proposed"
            )
        ),
        "pending_runs": count(
            select(func.count(AgentRun.id)).where(
                AgentRun.company_id == company_id,
                AgentRun.producer.not_in(IN_HOUSE_PRODUCERS),
                AgentRun.id.not_in(decided),
            )
        ),
        "integrations": len(integration.waiting_for_a_person(session, company_id)),
        # Approving a specification or an architecture is always the client's.
        "specifications": count(
            select(func.count(Specification.id)).where(
                Specification.company_id == company_id,
                Specification.status == "draft",
            )
        ),
        "architectures": count(
            select(func.count(Architecture.id)).where(
                Architecture.company_id == company_id,
                Architecture.status == "draft",
            )
        ),
    }
    waiting["total"] = sum(waiting.values())
    return waiting


def _cost(session: Session, company_id: int, since: datetime) -> dict[str, Any]:
    def tokens(*where) -> int:
        return int(
            session.exec(
                select(func.coalesce(func.sum(AuditLog.cost_tokens), 0)).where(
                    AuditLog.company_id == company_id, *where
                )
            ).one()
            or 0
        )

    units = session.exec(
        select(BusinessUnit).where(BusinessUnit.company_id == company_id)
    ).all()
    limits = [u for u in units if u.token_quota_limit > 0]
    return {
        "tokens_total": tokens(),
        "tokens_since": tokens(AuditLog.created_at >= since),
        "quota_limit": sum(u.token_quota_limit for u in limits) or None,
        "quota_used": sum(u.token_quota_used for u in units),
        "over_budget": manager_agent.over_budget(session, company_id),
    }


def brief(
    session: Session, company: Company, since: datetime | None = None
) -> dict[str, Any]:
    now = datetime.now(UTC).replace(tzinfo=None)
    start = _naive(since) if since else now - DEFAULT_WINDOW
    record = manager_record.track_record(session, company.id)
    return {
        "company_id": company.id,
        "since": start.isoformat(),
        "until": now.isoformat(),
        "autonomy_level": company.autonomy_level,
        "done": _done(session, company.id, start),
        "agent": _decisions(session, company.id, start),
        "waiting": _waiting(session, company.id),
        "cost": _cost(session, company.id, start),
        "record": {
            "total": record["total"],
            "flagged_rules": record["flagged_rules"],
        },
    }
