"""Manager policy rules (services.management.manager_policy)."""

from services.management.manager_policy import Policy, _rule_holds


def test_accept_delivery_holds():
    facts = {
        "frozen": False,
        "human_led_hold": True,
        "verdict": "verified",
        "risk_tier": "low",
        "files": 3,
        "criteria": 2,
    }
    assert (
        _rule_holds("accept_delivery", facts, Policy(max_files=10, max_criteria=5))
        is None
    )


def test_accept_delivery_fails_on_unverified():
    facts = {
        "frozen": False,
        "human_led_hold": True,
        "verdict": "refuted",
        "risk_tier": "low",
        "files": 1,
        "criteria": 1,
    }
    assert _rule_holds("accept_delivery", facts, Policy()) is not None


def test_new_rules_present_in_catalogue():
    from services.management.manager_policy import RULE_DECISIONS, RULES

    for name in (
        "merge_local",
        "undo_delivery",
        "send_back_blocked",
        "carry_over",
        "answer_question",
        "publish",
        "resume_after_credit",
        "approve_specification",
        "approve_architecture",
        "accept_high_risk",
    ):
        assert name in RULES
        assert name in RULE_DECISIONS
