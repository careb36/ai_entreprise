"""Manager agent suggestion parsing (services.manager_agent)."""

from services import manager_agent


def test_parse_suggestion():
    text = "DECISION: accept\nCATEGORY: accept_low_risk\nREASON: verified run"
    s = manager_agent.parse_suggestion(text)
    assert s["suggested_decision"] == "accept"
    assert s["suggested_category"] == "accept_low_risk"


def test_parse_unusable_returns_none():
    assert manager_agent.parse_suggestion("no format here") is None
    assert manager_agent.parse_suggestion(None) is None


def test_policy_autonomous_vs_escalate():
    p = manager_agent.ManagerPolicy(autonomous={"accept_low_risk"})
    assert p.may_decide_alone("accept_low_risk") is True
    assert p.may_decide_alone("approve_specification") is False
