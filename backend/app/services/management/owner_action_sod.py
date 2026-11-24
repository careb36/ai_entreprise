"""Segregation of duties as ``self_host_container_run`` meets it (issue #701,
``docs/design/owner-actions.md``).

The run drives the harness as the Owner who started it, so the Owner drafts
the specification and the architectures and then approves them. The rule
(``services/platform/sod.py``) refuses that unless the company's mode leaves
the action alone or the single-person exception is in force. This module
observes, before the run and again right after the run has its company,
what the rule would say of each approval the harness performs; it never
grants, changes or bypasses the policy (granting the exception is the
Owner's explicit choice, in the company's duties page).

- ``previous_state``: the company a run with the same ``checkout_name``
  created, which the next run reuses (the CLI's ``<checkout>.self_host.json``).
- ``situation``: the facts the preview shows and its digest covers.
- ``blockers``: ``sod_refused`` when a reused company would refuse an
  approval; a new company is not a blocker (the exception can be granted
  only once it exists), the run stops ``blocked_by_sod`` after creating it.
"""

from __future__ import annotations

from typing import Any

from models import Company, OwnerActionRun
from sqlmodel import Session, select

from services.platform import sod

# The approvals the harness's steps ``records`` and ``issue`` perform as the
# Owner, of records the Owner drafted (``scripts/self_host/self_host.py``:
# ``_approved_spec``, ``_approved_architecture``, ``_revised_version``). The
# run does not run ``review``, so it never accepts a held delivery nor
# releases an integration.
APPROVALS: tuple[tuple[str, str], ...] = (
    (sod.APPROVE_SPEC, "drafted the specification"),
    (sod.APPROVE_ARCH, "drafted the architecture"),
)
SELF_HOST = "self_host_container_run"  # owner_actions.SELF_HOST
BLOCKER = "sod_refused"
BLOCKED = "blocked_by_sod"


def previous_state(session: Session, name: str, checkout_name: str) -> dict[str, Any]:
    """The harness state of the last run on the same snapshot: the company is
    found again, not remade."""
    rows = session.exec(
        select(OwnerActionRun)
        .where(OwnerActionRun.action == name)
        .order_by(OwnerActionRun.id.desc())  # type: ignore[union-attr]
    ).all()
    for row in rows:
        if (row.params or {}).get("checkout_name") == checkout_name:
            state = (row.observations or {}).get("state")
            if isinstance(state, dict) and state.get("company_id"):
                return {"company_id": state["company_id"]}
    return {}


def situation(session: Session, company_id: int | None, user_id: int) -> dict[str, Any]:
    """What the rule says of the Owner approving what the Owner drafted, under
    *company_id*'s actual mode and exception (``None``: a company the run will
    create, with the default policy and no exception)."""
    if company_id is not None and session.get(Company, company_id) is None:
        company_id = None  # the harness creates a new one (``_company_exists``)
    new = company_id is None
    if new:
        mode, granted, eligible = sod.DEFAULT_MODE, False, None
    else:
        policy = sod.policy_of(session, company_id)
        mode, granted = policy.mode, bool(policy.solo_exception)
        eligible = sod.solo_eligible(session, company_id)
    in_force = granted and bool(eligible)
    owner = sod.person_of_user_id(session, user_id)
    refused = []
    for action, why in APPROVALS:
        decision = sod.decide(
            mode, action, owner, [sod.Requester(owner, why)], in_force
        )
        if decision.outcome == "refused":
            refused.append(action)
    return {
        "company_id": company_id,
        "new_company": new,
        "mode": mode,
        "solo_exception": granted,
        "solo_eligible": eligible,
        "solo_in_force": in_force,
        "approvals": [a for a, _ in APPROVALS],
        "refused": refused,
    }


def facts(session: Session, user_id: int, params: Any) -> dict[str, Any]:
    """The preview's facts for *params*: the SoD situation of the company the
    run will use (reused by ``checkout_name``, else a new one)."""
    state = previous_state(session, SELF_HOST, params.checkout_name)
    return {"sod": situation(session, state.get("company_id"), user_id)}


def blockers(params: Any, f: dict[str, Any]) -> list[str]:
    seen = f.get("sod") or {}
    return [BLOCKER] if seen.get("refused") and not seen.get("new_company") else []


class Blocked(Exception):
    """The run's company would refuse an approval the harness performs."""

    def __init__(self, seen: dict[str, Any]):
        super().__init__(BLOCKED)
        self.seen = seen
