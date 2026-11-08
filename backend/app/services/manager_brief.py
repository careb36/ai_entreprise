"""The client's brief (issue #331): what got done, what the Manager agent
decided and why, what waits for the client, and the cost so far.

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
from sqlalchemy import func
from sqlmodel import Session, select

from services import integration, manager_agent, manager_record
from services.board import awaits_risk_acceptance

LIST_LIMIT = 20
DEFAULT_WINDOW = timedelta(days=1)


def _naive(moment: datetime) -> datetime:
    return moment.astimezone(UTC).replace(tzinfo=None) if moment.tzinfo else moment


def _iso(moment: datetime | None) -> str | None:
    return moment.isoformat() if moment else None


def _done(session: Session, company_id: int, since: datetime) -> dict[str, Any]:
    rows = session.exec(
        select(BacklogItem)
        .join(Sprint, Sprint.id == BacklogItem.sprint_id)
        .where(
            Sprint.company_id == company_id,
            BacklogItem.status == "done",
            BacklogItem.updated_at >= since,
        )
        .order_by(BacklogItem.updated_at.desc(), BacklogItem.id.desc())
    ).all()
    return {
        "count": len(rows),
        "tasks": [
            {"id": i.id, "title": i.title, "sprint_id": i.sprint_id}
            for i in rows[:LIST_LIMIT]
        ],
    }


def _decisions(session: Session, company_id: int, since: datetime) -> dict[str, Any]:
    rows = session.exec(
        select(ManagerDecision)
        .where(
            ManagerDecision.company_id == company_id,
            ManagerDecision.suggested_at >= since,
        )
        .order_by(ManagerDecision.id.desc())
    ).all()
    alone = [r for r in rows if r.decided_by]
    return {
        "suggested": sum(1 for r in rows if r.suggested_decision),
        "unusable": sum(1 for r in rows if r.suggested_decision is None),
        "taken_alone": len(alone),
        "overridden": sum(1 for r in alone if r.overridden_at),
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
    holds = sum(1 for t in tasks if awaits_risk_acceptance(t.comments))
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
