"""A sprint stops spending at the company's budget in money.

``companies.limits`` may set ``sprint_budget_cents`` (``0``: no budget) and
``budget_warn_percent`` (``company_limits``; only an Owner sets them, audited).
The sprint's cost is what the platform priced from the usage the LLM proxy
recorded for its tasks (``services/models/run_cost.sprint_costs``): never a
figure an agent or a producer reported.

Before a task's model turn (``worker/intake._claim``), ``hold`` reads that
cost:

- at or past the budget, the sprint pauses with the reason (``sprint_control.
  pause``) and the task waits as ``todo``, so resuming the sprint, after the
  Owner raises the budget, runs it again. The pause is audited as
  ``sprint_budget_reached``, the incident, once per pause;
- when a budget is set and the cost cannot be priced (a model without a price,
  no turn with usage from the provider), the sprint pauses too: an unknown
  cost is not under the budget. Turns the provider gave no usage for, beside
  priced ones, are counted in the audit row (``turns_without_usage``);
- past ``budget_warn_percent`` of it, once per sprint, a
  ``sprint_budget_warning`` audit row and a Glassroom event say so, and work
  goes on.

A turn already being written when the budget is reached is still delivered
(it was paid for), as for a Manager's pause. The design follows Paperclip's
budget policies (MIT: ``server/src/services/budgets.ts``: a warning percent, a
hard stop that pauses the scope with ``pauseReason: "budget"`` and opens an
incident, and unpriced usage that blocks), with the cost from the platform's
own measure instead of the runner's receipts.
"""

from __future__ import annotations

from typing import Any

from models import BacklogItem, Company, Sprint
from models.audit import AuditLog
from sqlmodel import Session, select

from services.contracts import company_limits
from services.loop import sprint_control
from services.models import run_cost
from services.platform.agent_service import AgentService
from services.platform.glassroom import publish_glassroom
from services.tasks import task_events

WARNING_ACTION = "sprint_budget_warning"
REACHED_ACTION = "sprint_budget_reached"


def budget_of(session: Session, company_id: int) -> tuple[int, int]:
    """The company's sprint budget in cents (``0``: none) and its warning percent."""
    company = session.get(Company, company_id)
    limits = company_limits.parse(company.limits if company else None)
    return (
        int(limits.get("sprint_budget_cents") or 0),
        int(
            limits.get("budget_warn_percent")
            or company_limits.DEFAULT_BUDGET_WARN_PERCENT
        ),
    )


def spent(
    session: Session, company_id: int, sprint_id: int
) -> tuple[float | None, str | None, int]:
    """The sprint's priced cost in cents (``None`` with why, when it cannot be
    priced), and how many of its turns the provider gave no usage for."""
    cost = run_cost.sprint_costs(session, company_id, [sprint_id]).get(sprint_id) or {}
    unreported = int(cost.get("estimated_turns") or 0) + int(
        cost.get("unavailable_turns") or 0
    )
    if cost.get("cost_cents") is not None:
        return float(cost["cost_cents"]), None, unreported
    if cost.get("reason") in (None, run_cost.NO_TURNS):
        return 0.0, None, unreported
    return None, str(cost["reason"]), unreported


def _dollars(cents: float) -> str:
    return f"${cents / 100:,.2f}"


def reason_reached(spent_cents: float, budget: int) -> str:
    return (
        f"The sprint spent {_dollars(spent_cents)} of its {_dollars(budget)} budget. "
        "An Owner can raise the company's sprint budget, then resume the sprint."
    )


def reason_unknown(why: str, budget: int) -> str:
    return (
        f"The sprint has a {_dollars(budget)} budget but its cost cannot be priced "
        f"({why}): an unknown cost is not under the budget. Add the model's price "
        "or remove the budget, then resume the sprint."
    )


def _audited(session: Session, company_id: int, sprint_id: int, action: str) -> bool:
    return (
        session.exec(
            select(AuditLog.id).where(
                AuditLog.company_id == company_id,
                AuditLog.interaction_id == f"sprint:{sprint_id}:budget",
                AuditLog.action == action,
            )
        ).first()
        is not None
    )


def _audit(
    session: Session, sprint: Sprint, action: str, result: dict[str, Any]
) -> None:
    AgentService().audit_interaction(
        session=session,
        company_id=sprint.company_id,
        interaction_id=f"sprint:{sprint.id}:budget",
        agent_id="system:sprint-budget",
        action=action,
        details={"company_id": str(sprint.company_id), "sprint_id": sprint.id},
        result=result,
        decision_signature=None,
        cost_tokens=0,
        commit=False,
    )


def check(session: Session, sprint: Sprint) -> dict[str, Any] | None:
    """What the budget says now: ``{"pause": reason}`` when the sprint must
    stop, ``{"warn": text}`` the first time it passes the warning, else ``None``.
    Records the audit rows; the caller commits."""
    budget, warn_percent = budget_of(session, sprint.company_id)
    if budget <= 0:
        return None
    cents, why, unreported = spent(session, sprint.company_id, sprint.id)
    figures = {
        "budget_cents": budget,
        "spent_cents": cents,
        "unpriced": why,
        "turns_without_usage": unreported,
    }
    if cents is None or cents >= budget:
        reason = (
            reason_unknown(why or "unknown", budget)
            if cents is None
            else reason_reached(cents, budget)
        )
        _audit(session, sprint, REACHED_ACTION, {**figures, "reason": reason})
        return {"pause": reason}
    if cents >= budget * warn_percent / 100 and not _audited(
        session, sprint.company_id, sprint.id, WARNING_ACTION
    ):
        text = (
            f"The sprint spent {_dollars(cents)}, {warn_percent}% or more of its "
            f"{_dollars(budget)} budget."
        )
        _audit(
            session, sprint, WARNING_ACTION, {**figures, "warn_percent": warn_percent}
        )
        return {"warn": text}
    return None


def hold(engine: Any, item_id: int) -> dict[str, Any] | None:
    """Before a model turn on *item_id*: pause its sprint when the budget says
    so (the task waits as ``todo``); the budget's answer, for the Glassroom."""
    with Session(engine) as session:
        item = session.get(BacklogItem, item_id)
        sprint = session.get(Sprint, item.sprint_id) if item else None
        if item is None or sprint is None or sprint_control.is_halted(sprint.status):
            return None
        verdict = check(session, sprint)
        if verdict and "pause" in verdict:
            if sprint_control.pause(session, sprint, verdict["pause"]):
                if item.status == "todo":
                    task_events.record(
                        session,
                        item,
                        "paused",
                        task_events.SYSTEM,
                        status="todo",
                        text=f"Paused: {verdict['pause']}",
                    )
            else:
                verdict = None
        session.commit()
        if verdict:
            verdict["sprint_id"] = sprint.id
        return verdict


async def publish(nats_client: Any, verdict: dict[str, Any]) -> None:
    """The budget's warning or pause on the sprint's Glassroom."""
    paused = "pause" in verdict
    await publish_glassroom(
        nats_client,
        verdict["sprint_id"],
        {
            "type": "error" if paused else "status",
            "semantic_type": "sprint.paused_budget"
            if paused
            else "sprint.budget_warning",
            "source": "task_worker",
            "task_status": sprint_control.PAUSED if paused else None,
            "message": ("⏸ " if paused else "⚠ ")
            + verdict["pause" if paused else "warn"],
        },
    )
