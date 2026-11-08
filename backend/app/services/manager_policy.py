"""What a company lets the Manager agent decide alone (issue #331).

A Manager writes the company's ``ManagerPolicy``: the names of the rules
below and the size limits they read. Each rule is deterministic: it reads the
facts the platform observed about an inbox item (the Verifier's verdict, the
risk tier, the files, the criteria a task covers, the autonomy level, the
procedures' numbers, the budget), never the model's judgement. The agent may
act alone only when a rule the policy names holds *and* its own suggestion is
the decision that rule allows; anything else stays with the client.

``ALWAYS_ESCALATE`` is the confirmation set (OpenHands' confirmation mode):
decisions no policy can hand to the agent. A policy naming one is refused.
The agent never edits a policy; a rule whose override rate rises is flagged
(``services/manager_record.py``) and the agent stops applying it until a
Manager looks, which narrows the policy and never widens it.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import UTC, datetime
from typing import Any

from models.manager import ManagerPolicy
from sqlmodel import Session, select

# The rules a policy may name, with what each needs to hold.
RULES: dict[str, str] = {
    "accept_delivery": (
        "Accept a delivery QA approved that waits for a person only because "
        "of autonomy level 1, when its latest run is verified, its risk tier "
        "is low or medium, and it is within the size limits."
    ),
    "adopt_procedure": (
        "Adopt a procedure the ScrumMaster proposed, only at autonomy level 3 "
        "and when the board reverted no earlier procedure for the same role."
    ),
    "approve_run": (
        "Approve an outside producer's run whose latest verification is "
        "verified, with a risk tier of low or medium, within the file limit."
    ),
}
# The decision each rule lets the agent take, and the category it applies to.
RULE_DECISIONS = {
    "accept_delivery": ("accept_delivery", "accept"),
    "adopt_procedure": ("adopt_procedure", "adopt"),
    "approve_run": ("approve_run", "approve"),
}
# Decisions that always go to the client, whatever a policy says.
ALWAYS_ESCALATE = frozenset(
    {
        "approve_specification",
        "approve_architecture",
        "accept_high_risk",
        "cancel_sprint",
        "change_autonomy",
        "spend_above_budget",
    }
)
LOW_TIERS = ("low", "medium")
MAX_LIMIT = 1000


class PolicyError(ValueError):
    """A policy a Manager cannot write, with the reason."""


@dataclass
class Policy:
    rules: list[str] = field(default_factory=list)
    max_files: int = 10
    max_criteria: int = 5


def _now() -> datetime:
    return datetime.now(UTC).replace(tzinfo=None)


def _limit(value: Any, name: str, default: int) -> int:
    if value is None:
        return default
    if isinstance(value, bool) or not isinstance(value, int):
        raise PolicyError(f"{name} must be a whole number")
    if not 0 <= value <= MAX_LIMIT:
        raise PolicyError(f"{name} must be between 0 and {MAX_LIMIT}")
    return value


def validate(payload: dict[str, Any]) -> Policy:
    """The policy *payload* describes, or ``PolicyError`` with the reason."""
    rules = payload.get("rules") or []
    if not isinstance(rules, list) or not all(isinstance(r, str) for r in rules):
        raise PolicyError("rules must be a list of rule names")
    clean: list[str] = []
    for rule in rules:
        name = rule.strip()
        if name in ALWAYS_ESCALATE:
            raise PolicyError(
                f"{name} is always decided by the client; no policy can hand it "
                "to the Manager agent"
            )
        if name not in RULES:
            raise PolicyError(f"unknown rule: {name}")
        if name not in clean:
            clean.append(name)
    return Policy(
        rules=clean,
        max_files=_limit(payload.get("max_files"), "max_files", 10),
        max_criteria=_limit(payload.get("max_criteria"), "max_criteria", 5),
    )


def row_for(session: Session, company_id: int) -> ManagerPolicy | None:
    return session.exec(
        select(ManagerPolicy).where(ManagerPolicy.company_id == company_id)
    ).first()


def load(session: Session, company_id: int) -> Policy:
    """The company's policy; without one, the agent only suggests."""
    row = row_for(session, company_id)
    if row is None:
        return Policy()
    # A rule that left the catalogue, or one of the confirmation set written
    # behind the API's back, is never applied.
    rules = [
        r
        for r in (row.rules or [])
        if isinstance(r, str) and r in RULES and r not in ALWAYS_ESCALATE
    ]
    return Policy(rules=rules, max_files=row.max_files, max_criteria=row.max_criteria)


def save(
    session: Session, company_id: int, policy: Policy, actor: str
) -> ManagerPolicy:
    row = row_for(session, company_id) or ManagerPolicy(company_id=company_id)
    row.rules = list(policy.rules)
    row.max_files = policy.max_files
    row.max_criteria = policy.max_criteria
    row.updated_by = actor
    row.updated_at = _now()
    session.add(row)
    return row


def serialize(policy: Policy, row: ManagerPolicy | None = None) -> dict[str, Any]:
    return {
        "rules": list(policy.rules),
        "max_files": policy.max_files,
        "max_criteria": policy.max_criteria,
        "updated_by": row.updated_by if row else None,
        "updated_at": row.updated_at.isoformat() if row and row.updated_at else None,
        "catalogue": RULES,
        "always_escalate": sorted(ALWAYS_ESCALATE),
    }


def _rule_holds(rule: str, facts: dict[str, Any], policy: Policy) -> str | None:
    """Why *rule* does not hold for *facts*, or ``None`` when it does."""
    tier = facts.get("risk_tier")
    files = facts.get("files")
    if rule == "accept_delivery":
        if not facts.get("human_led_hold"):
            return "the hold is not only the autonomy level's"
        if facts.get("verdict") != "verified":
            return f"the latest run is {facts.get('verdict') or 'not verified'}"
        if tier not in LOW_TIERS:
            return f"the risk tier is {tier or 'unknown'}"
        if files is None or files > policy.max_files:
            return f"{files if files is not None else 'unknown'} files, over the limit"
        if (facts.get("criteria") or 0) > policy.max_criteria:
            return f"{facts.get('criteria')} criteria, over the limit"
        return None
    if rule == "adopt_procedure":
        if facts.get("autonomy_level") != 3:
            return f"autonomy level {facts.get('autonomy_level')}, not 3"
        if facts.get("reverted_for_role"):
            return "the board reverted an earlier procedure for this role"
        return None
    if rule == "approve_run":
        if facts.get("verdict") != "verified":
            return f"the latest verification is {facts.get('verdict') or 'missing'}"
        if tier not in LOW_TIERS:
            return f"the risk tier is {tier or 'unknown'}"
        if files is None or files > policy.max_files:
            return f"{files if files is not None else 'unknown'} files, over the limit"
        return None
    return "unknown rule"


def evaluate(
    category: str,
    suggestion: str | None,
    facts: dict[str, Any],
    policy: Policy,
    flagged: set[str] | frozenset[str] = frozenset(),
) -> dict[str, Any]:
    """May the agent take *suggestion* alone on an item of *category*?

    ``{"allowed": bool, "rule": name or None, "why": text}``. Never allowed
    for the confirmation set, over budget, after the client overrode the
    agent on the item, or under a flagged rule.
    """
    if category in ALWAYS_ESCALATE:
        return {"allowed": False, "rule": None, "why": f"{category}: always the client"}
    if facts.get("over_budget"):
        return {
            "allowed": False,
            "rule": None,
            "why": "spend_above_budget: the company is over its token budget",
        }
    if facts.get("overridden_before"):
        return {
            "allowed": False,
            "rule": None,
            "why": "the client overrode the agent on this item before",
        }
    for rule in policy.rules:
        rule_category, decision = RULE_DECISIONS[rule]
        if rule_category != category:
            continue
        if suggestion != decision:
            return {
                "allowed": False,
                "rule": rule,
                "why": f"the suggestion is not {decision}",
            }
        if rule in flagged:
            return {
                "allowed": False,
                "rule": rule,
                "why": "the rule is flagged: its override rate rose",
            }
        why_not = _rule_holds(rule, facts, policy)
        if why_not is None:
            return {"allowed": True, "rule": rule, "why": RULES[rule]}
        return {"allowed": False, "rule": rule, "why": why_not}
    return {"allowed": False, "rule": None, "why": "no rule of the policy applies"}
