"""The Manager agent's answer and its policy's rules.

The answer is read only in its fixed form; the confirmation set cannot be
unlocked; each rule holds only on its facts and only for its own decision.

The Manager agent (issue #331): suggestions in a fixed format, a policy that
lets it decide alone only by deterministic rules, decisions audited and
overridable, its record against the client's decisions, and the brief. The
shared rows and fakes are in ``manager_agent_helpers``.
"""

from __future__ import annotations

import pytest

# test_manager_rules.py imports the helpers from here too.
from manager_agent_helpers import _company, _cycle, _hold, _policy, _rows, answer  # noqa: F401
from services.management import manager_agent, manager_policy


def test_the_answer_is_read_only_in_its_fixed_form():
    parsed = manager_agent.parse_suggestion(
        answer("accept", "accept_delivery"), "accept_delivery"
    )
    assert parsed == {"decision": "accept", "reason": "The run is verified."}
    crlf = answer("send_back", "blocked_task").replace("\n", "\r\n") + "."
    assert manager_agent.parse_suggestion(crlf, "blocked_task")["decision"] == (
        "send_back"
    )


@pytest.mark.parametrize(
    "text",
    [
        None,
        "accept it",
        # Another category than the item's: the category is the platform's.
        answer("accept", "accept_high_risk"),
        # A decision the category does not allow.
        answer("approve", "accept_delivery"),
        answer("accept", "accept_delivery", reason=""),
        "DECISION: accept\nCATEGORY: accept_delivery\nREASON:\nno end",
    ],
)
def test_an_unusable_answer_is_none(text):
    assert manager_agent.parse_suggestion(text, "accept_delivery") is None


# ── The policy ───────────────────────────────────────────────────────────────


@pytest.mark.parametrize("name", sorted(manager_policy.ALWAYS_ESCALATE))
def test_the_confirmation_set_cannot_be_unlocked(name):
    with pytest.raises(
        manager_policy.PolicyError, match="always decided by the client"
    ):
        manager_policy.validate({"rules": [name]})
    assert not manager_policy.evaluate(
        name, "accept", {}, manager_policy.Policy(rules=list(manager_policy.RULES))
    )["allowed"]


def test_a_policy_is_validated():
    with pytest.raises(manager_policy.PolicyError, match="unknown rule"):
        manager_policy.validate({"rules": ["merge_everything"]})
    with pytest.raises(manager_policy.PolicyError):
        manager_policy.validate({"rules": "accept_delivery"})
    with pytest.raises(manager_policy.PolicyError):
        manager_policy.validate({"max_files": -1})
    policy = manager_policy.validate(
        {"rules": ["accept_delivery", "accept_delivery"], "max_files": 3}
    )
    assert (policy.rules, policy.max_files, policy.max_criteria) == (
        ["accept_delivery"],
        3,
        5,
    )


def test_a_rule_written_behind_the_api_is_never_applied(sqlite_session):
    company = _company(sqlite_session)
    _policy(sqlite_session, company, "cancel_sprint", "accept_delivery", "x")
    assert manager_policy.load(sqlite_session, company.id).rules == ["accept_delivery"]


GOOD_DELIVERY = {
    "human_led_hold": True,
    "verdict": "verified",
    "risk_tier": "medium",
    "files": 3,
    "criteria": 2,
}


@pytest.mark.parametrize(
    "change, why",
    [
        ({"human_led_hold": False}, "not only the autonomy level"),
        ({"verdict": "refuted"}, "refuted"),
        ({"verdict": None}, "not verified"),
        ({"risk_tier": "high"}, "high"),
        ({"risk_tier": None}, "unknown"),
        ({"files": 11}, "over the limit"),
        ({"files": None}, "unknown files"),
        ({"criteria": 6}, "criteria"),
        ({"over_budget": True}, "spend_above_budget"),
        ({"overridden_before": True}, "overrode"),
    ],
)
def test_accept_delivery_holds_only_on_its_facts(change, why):
    policy = manager_policy.Policy(rules=["accept_delivery"])
    ok = manager_policy.evaluate("accept_delivery", "accept", GOOD_DELIVERY, policy)
    assert ok == {
        "allowed": True,
        "rule": "accept_delivery",
        "why": manager_policy.RULES["accept_delivery"],
    }
    refused = manager_policy.evaluate(
        "accept_delivery", "accept", {**GOOD_DELIVERY, **change}, policy
    )
    assert not refused["allowed"] and why in refused["why"]


def test_the_rule_allows_only_its_own_decision_and_not_when_flagged():
    policy = manager_policy.Policy(rules=["accept_delivery"])
    sent_back = manager_policy.evaluate(
        "accept_delivery", "send_back", GOOD_DELIVERY, policy
    )
    assert not sent_back["allowed"]
    flagged = manager_policy.evaluate(
        "accept_delivery", "accept", GOOD_DELIVERY, policy, {"accept_delivery"}
    )
    assert not flagged["allowed"] and "flagged" in flagged["why"]
    no_rule = manager_policy.evaluate(
        "accept_delivery", "accept", GOOD_DELIVERY, manager_policy.Policy()
    )
    assert not no_rule["allowed"]


def test_adopt_procedure_only_at_level_3_without_a_reverted_one():
    policy = manager_policy.Policy(rules=["adopt_procedure"])

    def allowed(**facts):
        return manager_policy.evaluate("adopt_procedure", "adopt", facts, policy)[
            "allowed"
        ]

    assert allowed(autonomy_level=3, reverted_for_role=False)
    assert not allowed(autonomy_level=2, reverted_for_role=False)
    assert not allowed(autonomy_level=3, reverted_for_role=True)


def test_approve_run_needs_a_verified_low_risk_small_run():
    policy = manager_policy.Policy(rules=["approve_run"], max_files=2)

    def allowed(**facts):
        return manager_policy.evaluate("approve_run", "approve", facts, policy)[
            "allowed"
        ]

    assert allowed(verdict="verified", risk_tier="low", files=2)
    assert not allowed(verdict="unverifiable", risk_tier="low", files=2)
    assert not allowed(verdict="verified", risk_tier="high", files=2)
    assert not allowed(verdict="verified", risk_tier="low", files=3)
