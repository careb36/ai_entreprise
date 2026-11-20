"""When the sprint planner's model did not answer usably, the record says so.

A plan made without the model (``sprint_fallbacks``, or one task per
requirement in ``spec_planning``) is generic; a person must see that before
the sprint runs on it, and be able to plan again. The planner returns
``planner_fallback`` (``{"reason", "detail"}``, as the backend observed it:
the proxy's error, the parse failure, too few tasks, no model). This module
records it twice, in the audit log, by the backend and never from a request
body:

- ``record_plan``: when ``POST /v1/plan_sprint`` (or the self-improvement
  planner) answers with a fallback, one ``planner_fallback`` row under
  ``plan:<company>:<plan_id>``; the response carries ``plan_id``.
- ``carry_to_sprint``: when ``POST /v1/start_sprint`` names that ``plan_id``,
  the backend reads its own row back and writes one under
  ``sprint:<id>:planning`` with the reason copied from it. A ``plan_id`` the
  backend did not record (or another company's) records nothing.
- ``record_sprint``: a sprint the platform opens itself (the follow-up at
  close) records its fallback directly.

``of_sprint`` reads it for the board. The row records no work, so it never
keeps an empty sprint from being deleted (``services/loop/sprint_archive.py``).
"""

from __future__ import annotations

from typing import Any
from uuid import uuid4

from models import AuditLog, Sprint
from sqlmodel import Session, select

from services.platform.agent_service import AgentService

ACTION = "planner_fallback"
AGENT = "system:planner"


def plan_ref(company_id: int, plan_id: str) -> str:
    return f"plan:{company_id}:{plan_id}"


def sprint_ref(sprint_id: int) -> str:
    return f"sprint:{sprint_id}:planning"


def _observed(fallback: dict[str, Any]) -> dict[str, str]:
    """The reason and its detail, as strings; an unknown reason stays visible."""
    reason = str(fallback.get("reason") or "unexpected_error")
    return {"reason": reason, "detail": str(fallback.get("detail") or "")}


def _write(
    session: Session,
    company_id: int,
    ref: str,
    details: dict[str, Any],
    result: dict[str, Any],
    commit: bool,
) -> AuditLog:
    return AgentService().audit_interaction(
        session=session,
        company_id=company_id,
        interaction_id=ref,
        agent_id=AGENT,
        action=ACTION,
        details=details,
        result=result,
        decision_signature=None,
        cost_tokens=0,
        commit=commit,
    )


def record_plan(
    session: Session, company_id: int, kind: str, fallback: dict[str, Any] | None
) -> str | None:
    """Record a fallback plan; its ``plan_id``, or None when the model answered."""
    if not fallback:
        return None
    plan_id = uuid4().hex
    _write(
        session,
        company_id,
        plan_ref(company_id, plan_id),
        {"company_id": str(company_id), "plan_kind": kind},
        _observed(fallback),
        commit=True,
    )
    return plan_id


def carry_to_sprint(
    session: Session, sprint: Sprint, plan_id: str | None
) -> dict[str, str] | None:
    """The sprint's row from the plan's, read back from the backend's own
    record; the caller commits. None when *plan_id* names no fallback row."""
    if not plan_id:
        return None
    row = session.exec(
        select(AuditLog).where(
            AuditLog.company_id == sprint.company_id,
            AuditLog.interaction_id == plan_ref(sprint.company_id, plan_id),
            AuditLog.action == ACTION,
        )
    ).first()
    if row is None or not isinstance(row.result, dict):
        return None
    observed = _observed(row.result)
    _write(
        session,
        sprint.company_id,
        sprint_ref(sprint.id),
        {"sprint_id": sprint.id, "plan_id": plan_id, "plan_audit_id": row.id},
        observed,
        commit=False,
    )
    return observed


def record_sprint(
    session: Session, sprint: Sprint, fallback: dict[str, Any] | None
) -> dict[str, str] | None:
    """Record the fallback of a sprint the platform planned; the caller commits."""
    if not fallback:
        return None
    observed = _observed(fallback)
    _write(
        session,
        sprint.company_id,
        sprint_ref(sprint.id),
        {"sprint_id": sprint.id},
        observed,
        commit=False,
    )
    return observed


def of_sprint(session: Session, sprint: Sprint) -> dict[str, Any] | None:
    """The sprint's recorded fallback for the board, or None."""
    row = session.exec(
        select(AuditLog)
        .where(
            AuditLog.company_id == sprint.company_id,
            AuditLog.interaction_id == sprint_ref(sprint.id),
            AuditLog.action == ACTION,
        )
        .order_by(AuditLog.id.desc())
    ).first()
    if row is None or not isinstance(row.result, dict):
        return None
    return {
        **_observed(row.result),
        "recorded_at": row.created_at.isoformat() if row.created_at else None,
    }
