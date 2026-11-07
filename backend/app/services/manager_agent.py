"""Manager agent: suggests a decision for an inbox item, in a fixed format.

The agent reads the evidence of an item and writes a suggested decision
(``suggested_decision``, ``suggested_category``, ``suggested_reason``) that a
parser checks. A decision policy (deterministic, never the model's judgement)
says which decisions the agent may take alone and which escalate to the
client. Here we provide the data shapes and the parser; the LLM call and the
policy enforcement are wired by the caller.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import Any

# Fixed answer format the parser checks.
ANSWER_RE = re.compile(
    r"DECISION:\s*(?P<decision>\w+)\s*\n"
    r"CATEGORY:\s*(?P<category>[\w-]+)\s*\n"
    r"REASON:\s*(?P<reason>.+)",
    re.IGNORECASE | re.DOTALL,
)


@dataclass
class ManagerPolicy:
    """Which decisions the agent may take alone vs. escalate.

    Deterministic criteria only: risk tier, verdict, size, cost, etc.
    """

    # Categories the agent may decide alone (e.g. "accept_low_risk").
    autonomous: set[str] = field(default_factory=set)
    # Categories that always escalate to the client. Mandatory: a caller may
    # add to this set but never remove the entries below.
    escalate: set[str] = field(default_factory=set)

    _MANDATORY_ESCALATE = frozenset(
        {
            "approve_specification",
            "approve_architecture",
            "accept_high_risk",
            "cancel_sprint",
            "change_autonomy",
        }
    )

    def __post_init__(self) -> None:
        self.escalate = set(self.escalate) | set(self._MANDATORY_ESCALATE)

    def may_decide_alone(self, category: str) -> bool:
        try:
            if category in self.escalate:
                return False
            return category in self.autonomous
        except TypeError:
            return False


def parse_suggestion(text: str) -> dict[str, Any] | None:
    """Parse the agent's fixed-format answer; None if unusable."""
    if not isinstance(text, str):
        return None
    m = ANSWER_RE.search(text)
    if not m:
        return None
    reason = m.group("reason").strip()
    if not reason:
        return None
    return {
        "suggested_decision": m.group("decision").strip().lower(),
        "suggested_category": m.group("category").strip().lower(),
        "suggested_reason": reason,
    }


def suggest_for_blocked(
    item: Any, triage: dict[str, Any] | None = None
) -> dict[str, Any] | None:
    """A deterministic suggestion for a blocked task."""
    risk_hold = False
    try:
        from services.board import awaits_risk_acceptance

        risk_hold = bool(awaits_risk_acceptance(getattr(item, "comments", None)))
    except Exception:  # noqa: BLE001
        risk_hold = False
    if risk_hold:
        return {
            "suggested_decision": "escalate",
            "suggested_category": "accept_high_risk",
            "suggested_reason": "high-risk delivery waiting for a person",
        }
    if triage and triage.get("decision") == "person":
        return {
            "suggested_decision": "escalate",
            "suggested_category": "needs_person",
            "suggested_reason": "scrum master left it for a person",
        }
    if triage and triage.get("decision") == "retry":
        return {
            "suggested_decision": "carry_over",
            "suggested_category": "carry_over",
            "suggested_reason": "scrum master suggested another attempt",
        }
    status = getattr(item, "status", None)
    if status == "blocked":
        try:
            from services.board import blocked_item_next_step

            if blocked_item_next_step(getattr(item, "comments", None), getattr(item, "assignee", None)) == "frozen":
                return None
        except Exception:  # noqa: BLE001
            pass
    return {
        "suggested_decision": "accept",
        "suggested_category": "accept_low_risk",
        "suggested_reason": "verified delivery, low risk",
    }


def suggest_for_proposal(procedure: Any) -> dict[str, Any] | None:
    """A deterministic suggestion for a proposed procedure."""
    return {
        "suggested_decision": "adopt",
        "suggested_category": "adopt_procedure",
        "suggested_reason": "procedure proposed for review",
    }


def suggest_for_run(run: Any) -> dict[str, Any] | None:
    """A deterministic suggestion for an external producer's run."""
    return {
        "suggested_decision": "approve",
        "suggested_category": "approve_run",
        "suggested_reason": "external run awaiting decision",
    }


def record_suggestion(item: Any, suggestion: dict[str, Any] | None) -> None:
    """Store the suggestion apart from the person's decision."""
    if not isinstance(item, dict):
        return
    if not isinstance(suggestion, dict):
        item["suggested_decision"] = None
        item["suggested_category"] = None
        item["suggested_reason"] = None
        return
    item["suggested_decision"] = suggestion.get("suggested_decision")
    item["suggested_category"] = suggestion.get("suggested_category")
    item["suggested_reason"] = suggestion.get("suggested_reason")
