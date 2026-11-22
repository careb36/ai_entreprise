"""A company's limits on its agents' work: read, and changed by an Owner,
audited (issue #766; the limits as data are
``services/contracts/company_limits.py``, the Developer's profile that
applies them ``services/models/model_profile.py``, the rework budget
``services/loop/worker/qa_review.rework_limit``).

``companies.limits`` is ``null`` (the deployment's values) or an object of
some limits. Any member reads them with ``GET /v1/companies/{id}/limits``,
beside the deployment's defaults and the ceilings; only an Owner changes
them (``PUT``), never a Manager and never the Manager agent, since each
raises or lowers what a task may spend. Each change is audited as
``company_limits_changed`` with both values and the account (``user_id``,
and ``token_id`` for an API token), as ``run_window_changed``.
"""

from __future__ import annotations

from typing import Any

from config import settings
from models.company import Company
from sqlmodel import Session

from services.contracts import company_limits
from services.platform.agent_service import AgentService

AUDIT_ACTION = "company_limits_changed"


class LimitsRefused(ValueError):
    """A change the backend refuses; ``detail`` is the HTTP body's."""

    def __init__(self, status: int, detail: str):
        super().__init__(detail)
        self.status = status
        self.detail = detail


def deployment_defaults() -> dict[str, int]:
    """What applies where a company sets nothing (``docs/CONFIGURATION.md``)."""
    return {
        "tool_rounds": int(settings.DEVELOPER_TOOL_ROUNDS),
        "result_max_chars": int(settings.MCP_RESULT_MAX_CHARS),
        "max_output_tokens": max(0, int(settings.DEVELOPER_MAX_OUTPUT_TOKENS)),
        "max_reworks": company_limits.deployment_rework_limit(
            settings.MAX_REWORKS, settings.AUTOPILOT_MAX_REWORK_CYCLES
        ),
        "sprint_budget_cents": 0,
        "budget_warn_percent": company_limits.DEFAULT_BUDGET_WARN_PERCENT,
    }


def stored(session: Session, company_id: int | None) -> dict[str, int]:
    """The company's valid limits (``{}`` for none, or an unknown company)."""
    company = session.get(Company, company_id) if company_id is not None else None
    return company_limits.parse(company.limits if company else None)


def state(session: Session, company_id: int) -> dict[str, Any]:
    """What ``GET /v1/companies/{id}/limits`` shows."""
    return {
        "company_id": company_id,
        "limits": stored(session, company_id) or None,
        "defaults": deployment_defaults(),
        "floors": dict(company_limits.FLOORS),
        "ceilings": dict(company_limits.CEILINGS),
    }


def set_limits(
    session: Session,
    company_id: int,
    raw: Any,
    *,
    by: str,
    user_id: int | None,
    token_id: int | None,
) -> dict[str, Any]:
    """Set or clear the company's limits (an Owner's call, checked by the router); audited."""
    company = session.get(Company, company_id)
    if company is None:
        raise LimitsRefused(404, "Company not found")
    try:
        limits = company_limits.validate(raw)
    except company_limits.InvalidLimits as exc:
        raise LimitsRefused(422, str(exc)) from exc
    before = company_limits.parse(company.limits) or None
    if before != limits:
        company.limits = limits
        session.add(company)
        AgentService().audit_interaction(
            session=session,
            company_id=company_id,
            interaction_id=f"company:{company_id}:limits",
            agent_id=f"human:{by}",
            action=AUDIT_ACTION,
            details={
                "company_id": str(company_id),
                "user_id": user_id,
                "token_id": token_id,
            },
            result={"from": before, "to": limits},
            decision_signature=None,
            cost_tokens=0,
        )
        session.commit()
    return state(session, company_id)
