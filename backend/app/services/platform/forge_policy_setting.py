"""A company's forge policy: read, capped, stamped on the daemon's requests,
and changed by an Owner, audited (issue #727; the values are
``services/contracts/forge_policy.py``).

``companies.forge_policy`` is ``none`` for a new company and ``publish`` for
every company that existed before migration 0054. What is in force is that
value capped by the deployment's ``FORGE_POLICY_MAX``, so an air-gapped
install holds every company at ``none`` whatever is stored.

The backend sends the policy in force with every ``workspace.git.*`` request
and with the ``git`` part of a delivery (``stamp``); the workspace daemon
refuses any remote operation it does not allow and applies its own ceiling
(``vscode-daemon/forge_gate.py``), so a backend that sends a wider value, or
none at all, cannot widen contact.

Only an Owner changes it (``PUT /v1/companies/{id}/forge_policy``), never a
Manager and never the Manager agent; each change is audited as
``forge_policy_changed`` with both values and the account (``user_id``, and
``token_id`` for an API token). A value above the ceiling is refused.
"""

from __future__ import annotations

from typing import Any

from config import settings
from models.company import Company
from sqlmodel import Session

from services.contracts import forge_policy
from services.platform.agent_service import AgentService

AUDIT_ACTION = "forge_policy_changed"


class ForgePolicyRefused(ValueError):
    """A change or an action the policy refuses; ``detail`` is the HTTP body's."""

    def __init__(self, status: int, detail: Any):
        super().__init__(detail if isinstance(detail, str) else detail.get("message"))
        self.status = status
        self.detail = detail


def ceiling() -> str:
    return forge_policy.parse(settings.FORGE_POLICY_MAX)


def stored(session: Session, company_id: int | None) -> str:
    """The company's own value; an unknown company is ``none``."""
    company = session.get(Company, company_id) if company_id is not None else None
    return forge_policy.parse(company.forge_policy if company else None)


def effective(session: Session, company_id: int | None) -> str:
    """The policy in force: the company's, capped by the deployment's ceiling."""
    return forge_policy.cap(stored(session, company_id), ceiling())


def stamp(
    session: Session, company_id: int | None, spec: dict[str, Any]
) -> dict[str, Any]:
    """*spec* with the policy in force, for the workspace daemon."""
    return {**spec, "forge_policy": effective(session, company_id)}


def require_publish(
    session: Session, company_id: int | None, action: str = "publish to a remote"
) -> str:
    """The policy in force when it allows publishing; else a 409 ``forge_disabled``."""
    policy = effective(session, company_id)
    if not forge_policy.allows_publish(policy):
        raise ForgePolicyRefused(409, forge_policy.refusal(policy, action))
    return policy


def state(session: Session, company_id: int) -> dict[str, Any]:
    """What ``GET /v1/companies/{id}/forge_policy`` shows."""
    return {
        "company_id": company_id,
        "forge_policy": stored(session, company_id),
        "effective": effective(session, company_id),
        "ceiling": ceiling(),
        "policies": list(forge_policy.POLICIES),
    }


def set_policy(
    session: Session,
    company_id: int,
    value: str,
    *,
    by: str,
    user_id: int | None,
    token_id: int | None,
) -> dict[str, Any]:
    """Set the company's policy (an Owner's call, checked by the router); audited."""
    company = session.get(Company, company_id)
    if company is None:
        raise ForgePolicyRefused(404, "Company not found")
    if value not in forge_policy.POLICIES:
        raise ForgePolicyRefused(
            422, f"forge_policy is one of {', '.join(forge_policy.POLICIES)}"
        )
    limit = ceiling()
    if forge_policy.rank(value) > forge_policy.rank(limit):
        raise ForgePolicyRefused(
            409,
            f"This deployment allows at most {limit!r} (FORGE_POLICY_MAX): "
            f"{value!r} cannot be set",
        )
    before = forge_policy.parse(company.forge_policy)
    if before != value:
        company.forge_policy = value
        session.add(company)
        AgentService().audit_interaction(
            session=session,
            company_id=company_id,
            interaction_id=f"company:{company_id}:forge_policy",
            agent_id=f"human:{by}",
            action=AUDIT_ACTION,
            details={
                "company_id": str(company_id),
                "user_id": user_id,
                "token_id": token_id,
            },
            result={"from": before, "to": value},
            decision_signature=None,
            cost_tokens=0,
        )
        session.commit()
    return state(session, company_id)
