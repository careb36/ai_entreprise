"""What a company lets the Manager agent decide alone (issue #331).

A Manager writes the company's ``ManagerPolicy``: the names of the rules
below and the size limits they read. Each rule is deterministic: it reads the
facts the platform observed about an inbox item (the Verifier's verdict, the
risk tier, the files, the criteria a task covers, the autonomy level, the
procedures' numbers, the budget), never the model's judgement. The agent may
act alone only when a rule the policy names holds *and* its own suggestion is
the decision that rule allows; anything else stays with the client.

``ALWAYS_ESCALATE`` is the confirmation set (OpenHands' confirmation mode):
decisions no policy can hand to the agent (cancel a sprint, change the
autonomy level, spend above budget). A policy naming one is refused. Every
other action a Manager takes in the product is a rule a policy may name
(issue #394), off until named; the strict ones (approve a specification or
an architecture, accept a high-risk delivery) read more facts than the rest.
The agent never edits a policy; a rule whose override rate rises is flagged
(``services/management/manager_record.py``) and the agent stops applying it until a
Manager looks, which narrows the policy and never widens it.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import UTC, datetime
from typing import Any

from models.manager import ManagerPolicy
from sqlmodel import Session, select

# The rules a policy may name, with what each needs to hold. Every one is
# off until a Manager names it; ``services/management/manager_rules.py`` holds the checks.
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
    "merge_local": (
        "Merge a done task's branch into the base branch locally when QA "
        "approved it, its latest run is verified, its risk tier is low or "
        "medium and it is within the file limit; or a sprint's integration "
        "whose suite passed and, when the company requires a review of its "
        "integration, that a person approved at its commit."
    ),
    "undo_delivery": (
        "Undo a delivery from the daemon's snapshot when its latest "
        "verification is refuted, it was not made on a Git branch, and no "
        "person has worked on the task since."
    ),
    "send_back_blocked": (
        "Send back to work a task blocked for a person when the ScrumMaster's "
        "triage chose another attempt with a brief and its sprint is open."
    ),
    "carry_over": (
        "Mark a task blocked for a person for another attempt in the next "
        "sprint, when it was not carried before, is not itself a second "
        "attempt, and the ScrumMaster's triage did not leave it for a person."
    ),
    "answer_question": (
        "Answer the PO's or the Architect's open question on a draft, only "
        "with the answer an approved specification or architecture already "
        "gives to the same question or decision, word for word."
    ),
    "publish": (
        "Publish a done task's branch as a pull or merge request when the "
        "client turned publishing on and the repository has a remote, under "
        "the same conditions as merge_local."
    ),
    "resume_after_credit": (
        "Resume a sprint paused for lack of credit once a short call to the "
        "provider through the proxy passes."
    ),
    "approve_specification": (
        "Approve a draft specification the check does not block, with no open "
        "question and acceptance criteria on every requirement."
    ),
    "approve_architecture": (
        "Approve a draft architecture the check does not block, with no open "
        "question, that covers every requirement of its approved specification."
    ),
    "accept_high_risk": (
        "Accept a high-risk delivery QA approved when its latest run is "
        "verified, every criterion test the task covers passed, the standards "
        "check found no secret, it is within the high-risk file limit, and it "
        "touches no protected path of a company that hosts the factory."
    ),
}
# The decision each rule lets the agent take, and the category it applies to.
RULE_DECISIONS = {
    "accept_delivery": ("accept_delivery", "accept"),
    "adopt_procedure": ("adopt_procedure", "adopt"),
    "approve_run": ("approve_run", "approve"),
    "merge_local": ("merge_local", "merge"),
    "undo_delivery": ("undo_delivery", "undo"),
    "send_back_blocked": ("blocked_task", "send_back"),
    "carry_over": ("blocked_task", "carry_over"),
    "answer_question": ("answer_question", "answer"),
    "publish": ("publish", "publish"),
    "resume_after_credit": ("resume_after_credit", "resume"),
    "approve_specification": ("approve_specification", "approve"),
    "approve_architecture": ("approve_architecture", "approve"),
    "accept_high_risk": ("accept_high_risk", "accept"),
}
# Decisions that always go to the client, whatever a policy says.
# ``change_protected_paths``: turning a company's protected paths on or off
# (``services/platform/protected_paths_setting.py``) is a signed-in Owner's.
ALWAYS_ESCALATE = frozenset(
    {
        "cancel_sprint",
        "delete_sprint",
        "change_autonomy",
        "spend_above_budget",
        "change_protected_paths",
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
    max_files_high_risk: int = 3
    publish_enabled: bool = False


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


def _flag(value: Any, name: str) -> bool:
    if value is None:
        return False
    if not isinstance(value, bool):
        raise PolicyError(f"{name} must be true or false")
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
        max_files_high_risk=_limit(
            payload.get("max_files_high_risk"), "max_files_high_risk", 3
        ),
        publish_enabled=_flag(payload.get("publish_enabled"), "publish_enabled"),
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
    return Policy(
        rules=rules,
        max_files=row.max_files,
        max_criteria=row.max_criteria,
        max_files_high_risk=row.max_files_high_risk,
        publish_enabled=bool(row.publish_enabled),
    )


def save(
    session: Session, company_id: int, policy: Policy, actor: str
) -> ManagerPolicy:
    row = row_for(session, company_id) or ManagerPolicy(company_id=company_id)
    row.rules = list(policy.rules)
    row.max_files = policy.max_files
    row.max_criteria = policy.max_criteria
    row.max_files_high_risk = policy.max_files_high_risk
    row.publish_enabled = policy.publish_enabled
    row.updated_by = actor
    row.updated_at = _now()
    session.add(row)
    return row


def serialize(policy: Policy, row: ManagerPolicy | None = None) -> dict[str, Any]:
    return {
        "rules": list(policy.rules),
        "max_files": policy.max_files,
        "max_criteria": policy.max_criteria,
        "max_files_high_risk": policy.max_files_high_risk,
        "publish_enabled": policy.publish_enabled,
        "updated_by": row.updated_by if row else None,
        "updated_at": row.updated_at.isoformat() if row and row.updated_at else None,
        "catalogue": RULES,
        "always_escalate": sorted(ALWAYS_ESCALATE),
    }


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
    agent on the item, or under a flagged rule. A category may have several
    rules (a blocked task: send back, carry over); the one whose decision is
    the suggestion decides.
    """
    from services.management.manager_rules import rule_holds

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
    named = [r for r in policy.rules if RULE_DECISIONS[r][0] == category]
    if not named:
        return {"allowed": False, "rule": None, "why": "no rule of the policy applies"}
    rule = next((r for r in named if RULE_DECISIONS[r][1] == suggestion), None)
    if rule is None:
        decisions = " or ".join(RULE_DECISIONS[r][1] for r in named)
        return {
            "allowed": False,
            "rule": named[0],
            "why": f"the suggestion is not {decisions}",
        }
    if rule in flagged:
        return {
            "allowed": False,
            "rule": rule,
            "why": "the rule is flagged: its override rate rose",
        }
    why_not = rule_holds(rule, facts, policy)
    if why_not is None:
        return {"allowed": True, "rule": rule, "why": RULES[rule]}
    return {"allowed": False, "rule": rule, "why": why_not}


def _rule_holds(rule: str, facts: dict[str, Any], policy: Policy) -> str | None:
    """Why *rule* does not hold for *facts* (``services/management/manager_rules.py``)."""
    from services.management.manager_rules import rule_holds

    return rule_holds(rule, facts, policy)
