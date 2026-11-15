"""The intake queue as read: an item as the API shows it, its history from
the audit log, the company's list and the queue's numbers (issue #532).

The moves are ``services/planning/intake_queue.py``; this module only reads.
The suggestion (the Support agent's claim) and the decision (a person's) are
shown side by side, never merged.
"""

from __future__ import annotations

from datetime import datetime
from typing import Any

from models import AuditLog, IntakeItem
from sqlalchemy import func
from sqlmodel import Session, select

from services.contracts import intake_rules as intake


def _iso(value: datetime | None) -> str | None:
    return value.isoformat() if value else None


def interaction_of(item: IntakeItem) -> str:
    return f"intake:{item.company_id}:{item.id}"


def serialize(item: IntakeItem) -> dict[str, Any]:
    return {
        "id": item.id,
        "company_id": item.company_id,
        "source": item.source,
        "source_ref": item.source_ref,
        "submitted_by": item.submitted_by,
        "title": item.title,
        "body": item.body,
        "status": item.status,
        "snoozed_until": _iso(item.snoozed_until),
        "duplicate_of": intake.link("intake", item.duplicate_of_item_id)
        or intake.link("task", item.duplicate_of_task_id),
        "outcome": intake.link("task", item.outcome_task_id)
        or intake.link("specification", item.outcome_specification_id),
        # The Support agent's suggestion: a claim, shown beside the decision.
        "suggestion": {
            "status": item.suggested_status,
            "category": item.suggested_category,
            "duplicate_of": item.suggested_duplicate_of,
            "reason": item.suggested_reason,
            "by": item.triaged_by,
            "at": _iso(item.triaged_at),
        }
        if item.triaged_at
        else None,
        "decision": {
            "status": item.decided_status,
            "category": item.decided_category,
            "reason": item.decision_reason,
            "by": item.decided_by,
            "at": _iso(item.decided_at),
        }
        if item.decided_at
        else None,
        "created_at": _iso(item.created_at),
        "updated_at": _iso(item.updated_at),
    }


def history(session: Session, item: IntakeItem) -> list[dict[str, Any]]:
    """The item's moves, from the audit log, oldest first."""
    rows = session.exec(
        select(AuditLog)
        .where(
            AuditLog.company_id == item.company_id,
            AuditLog.interaction_id == interaction_of(item),
        )
        .order_by(AuditLog.id)
    ).all()
    return [
        {
            "action": row.action,
            "actor": row.agent_id,
            "from": (row.details or {}).get("from"),
            "to": (row.details or {}).get("to"),
            "reason": (row.details or {}).get("reason"),
            "at": _iso(row.created_at),
        }
        for row in rows
    ]


def items_of(
    session: Session, company_id: int, status: str | None = None, limit: int = 100
) -> list[IntakeItem]:
    query = select(IntakeItem).where(IntakeItem.company_id == company_id)
    if status:
        query = query.where(IntakeItem.status == status)
    return list(session.exec(query.order_by(IntakeItem.id.desc()).limit(limit)).all())


def pending_count(session: Session, company_id: int) -> int:
    return int(
        session.exec(
            select(func.count(IntakeItem.id)).where(
                IntakeItem.company_id == company_id,
                IntakeItem.status == intake.PENDING,
            )
        ).one()
        or 0
    )


def measures(session: Session, company_id: int) -> dict[str, Any]:
    """The queue's numbers (``services/contracts/intake_rules.measures``)."""
    rows = [
        {
            **serialize(item),
            "decision_reason": item.decision_reason,
            "created_at": item.created_at,
            "decided_at": item.decided_at,
            "suggested_status": item.suggested_status,
            "suggested_category": item.suggested_category,
            "decided_status": item.decided_status,
            "decided_category": item.decided_category,
            "triaged": item.triaged_at is not None,
        }
        for item in session.exec(
            select(IntakeItem).where(IntakeItem.company_id == company_id)
        ).all()
    ]
    return intake.measures(rows)
