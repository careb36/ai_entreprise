"""The Verifier's guard on protected paths (``docs/design/rsi-loop.md``,
section 3.3 layers 1 and 2, PR 2 of section 8; REQ-3, REQ-10, REQ-11).

For a company an Owner marked as hosting the factory
(``companies.protected_paths_enabled``), a delivery whose observed paths match
``services/contracts/protected_paths.py`` gets a ``protected_paths`` check
naming them and a ``high`` risk tier with the area ``grader``, so it waits for
a person; the Manager agent's ``accept_high_risk`` refuses it; and only a
signed-in Owner changes the flag, audited. The refusals come first, then what
is allowed.
"""

from __future__ import annotations

import secrets

import pytest
from manager_agent_helpers import _company, _cycle, _hold, _policy, answer
from models import AgentRun, AuditLog
from models.agent_run import Verification
from services.evidence import protected_check, risk
from services.evidence.verification import verify_agent_run
from services.management import manager_policy, manager_rules
from services.platform import api_tokens, protected_paths_setting
from sod_helpers import _person, auth_on  # noqa: F401 - an autouse fixture
from sqlmodel import select

GRADER = "bench/specs/01-cli-wordcount/acceptance/test_x.py"
PATTERNS = protected_check.patterns_in_force()
STRICT_FACTS = {
    "verdict": "verified",
    "criteria": 2,
    "all_criterion_tests_pass": True,
    "standards_secret": False,
    "files": 3,
}


@pytest.fixture(autouse=True)
def _audit_path(monkeypatch, tmp_path):
    monkeypatch.setenv("AUDIT_LOG_PATH", str(tmp_path / "audit"))


def _host(session, company, on=True):
    company.protected_paths_enabled = on
    session.add(company)
    session.commit()
    return company


def _writes(*paths, status="written"):
    return {"workspace_writes": [{"path": p, "status": status} for p in paths]}


def _verify(session, company, evidence, files=None, claims=None):
    run = AgentRun(
        company_id=company.id,
        external_ref=f"task-{secrets.token_hex(3)}",
        producer="test",
        claims=claims if claims is not None else {"files": files or []},
        artifact_text="x",
        artifact_digest="0" * 64,
        artifact_size=1,
    )
    session.add(run)
    session.commit()
    verification = verify_agent_run(session, run, evidence)
    return {c["name"]: c for c in verification.checks}, verification


# -- The refusals -------------------------------------------------------------


def test_a_delivery_writing_a_grader_is_high_with_the_area_grader(sqlite_session):
    company = _host(sqlite_session, _company(sqlite_session))
    checks, _ = _verify(sqlite_session, company, _writes(GRADER), [GRADER])
    assert checks["risk_tier"]["tier"] == "high"
    assert f"grader: {GRADER}" in checks["risk_tier"]["reasons"]
    guard = checks["protected_paths"]
    assert guard["outcome"] == "pass"  # a reason for a person, not a fault
    assert guard["matches"] == [
        {"path": GRADER, "pattern": "bench/**", "observed": True}
    ]
    assert GRADER in guard["observation"] and "a person decides" in guard["observation"]


def test_a_declared_path_never_removes_an_observed_match(sqlite_session):
    """The daemon wrote the Verifier; the producer declared only a harmless file."""
    company = _host(sqlite_session, _company(sqlite_session))
    target = "backend/app/services/evidence/verification.py"
    claims = {"files": ["src/a.py"], "risk": {"tier": "low", "reason": "tiny"}}
    checks, _ = _verify(
        sqlite_session, company, _writes(target, "src/a.py"), claims=claims
    )
    assert [m["path"] for m in checks["protected_paths"]["matches"]] == [target]
    assert checks["risk_tier"]["tier"] == "high"


def test_a_declared_protected_path_adds_to_the_match(sqlite_session):
    company = _host(sqlite_session, _company(sqlite_session))
    checks, _ = _verify(
        sqlite_session,
        company,
        _writes("src/a.py"),
        ["src/a.py", "docs/POSITIONING.md"],
    )
    assert checks["protected_paths"]["matches"] == [
        {
            "path": "docs/POSITIONING.md",
            "pattern": "docs/POSITIONING.md",
            "observed": False,
        }
    ]
    assert checks["risk_tier"]["tier"] == "high"


def test_a_console_producer_s_observed_change_is_matched(sqlite_session):
    company = _host(sqlite_session, _company(sqlite_session))
    evidence = {
        "console_run": {
            "outcome": "changed",
            "files": [{"path": "scripts/bench/compare.py", "delivered": True}],
        }
    }
    checks, _ = _verify(sqlite_session, company, evidence)
    assert checks["protected_paths"]["matches"][0]["path"] == "scripts/bench/compare.py"
    assert checks["risk_tier"]["tier"] == "high"


def test_spelling_does_not_hide_a_protected_path():
    for spelled in (
        "./bench/x.py",
        "BENCH/x.py",
        "bench/../bench/x.py",
        "\\bench\\x.py",
    ):
        result = risk.classify([spelled], protected=PATTERNS)
        assert result["tier"] == "high", spelled
        assert result["reasons"][0].startswith("grader: ")


def test_accept_high_risk_refuses_a_protected_match():
    why = manager_rules.rule_holds(
        "accept_high_risk",
        {**STRICT_FACTS, "protected_paths": [GRADER]},
        manager_policy.Policy(),
    )
    assert why is not None and GRADER in why and "a person reviews it" in why


LOW_FACTS = {
    "human_led_hold": True,
    "verdict": "verified",
    "risk_tier": "low",
    "files": 1,
    "criteria": 0,
}


def test_accept_delivery_refuses_a_protected_match_whatever_the_tier():
    """A run verified before the flag kept its low tier: still no acceptance."""
    policy = manager_policy.Policy()
    assert manager_rules.rule_holds("accept_delivery", LOW_FACTS, policy) is None
    why = manager_rules.rule_holds(
        "accept_delivery", {**LOW_FACTS, "protected_paths": [GRADER]}, policy
    )
    assert why is not None and GRADER in why


def _protected_hold(session, company, recorded: bool):
    item = _hold(
        session, company, human_led=False, tier="high", files=2, covers=["REQ-1.1"]
    )
    run = session.exec(
        select(AgentRun).where(AgentRun.external_ref == f"task-{item.id}")
    ).one()
    run.claims = {"files": ["src/a.py", GRADER]}
    verification = session.exec(
        select(Verification).where(Verification.agent_run_id == run.id)
    ).one()
    guard = {
        "name": "protected_paths",
        "outcome": "pass",
        "matches": [{"path": GRADER}],
    }
    verification.checks = [
        *verification.checks,
        {"name": "criteria_tests", "outcome": "pass", "results": {"REQ-1.1": "pass"}},
        {"name": "standards", "outcome": "pass", "findings": []},
        *([guard] if recorded else []),
    ]
    session.add_all([run, verification])
    session.commit()
    return item


@pytest.mark.parametrize("recorded", [True, False])
def test_the_manager_agent_does_not_accept_a_protected_delivery(
    sqlite_session, recorded
):
    """Recorded by the Verifier, or verified before the Owner turned the flag on."""
    company = _host(sqlite_session, _company(sqlite_session, autonomy=2))
    item = _protected_hold(sqlite_session, company, recorded)
    _policy(sqlite_session, company, "accept_high_risk")
    _cycle(sqlite_session, company, answer("accept", "accept_high_risk"))
    sqlite_session.refresh(item)
    assert item.status == "blocked"
    assert f"Protected paths touched (a person reviews them): {GRADER}" in (
        "\n".join(_cycle.prompts)
    )


def test_no_policy_hands_the_flag_to_the_manager_agent():
    assert "change_protected_paths" in manager_policy.ALWAYS_ESCALATE
    with pytest.raises(manager_policy.PolicyError):
        manager_policy.validate({"rules": ["change_protected_paths"]})


def test_only_a_signed_in_owner_changes_the_flag(sqlite_session, test_client):
    company = _company(sqlite_session)
    owner, _ = _person(sqlite_session, company.id, "Owner")
    _, manager = _person(sqlite_session, company.id, "Manager")
    _, raw = api_tokens.create_token(
        sqlite_session, company.id, "ci", owner.email, [], role="Manager"
    )
    url = f"/v1/companies/{company.id}/protected_paths"
    for headers in (manager, {"Authorization": f"Bearer {raw}"}):
        refused = test_client.put(url, json={"enabled": False}, headers=headers)
        assert refused.status_code == 403, refused.text
    assert test_client.put(url, json={"enabled": False}).status_code == 401
    assert not protected_paths_setting.enabled(sqlite_session, company.id)
    assert not _audits(sqlite_session, company.id)


# -- What is allowed ----------------------------------------------------------


def test_without_the_flag_nothing_changes(sqlite_session):
    company = _company(sqlite_session)
    checks, _ = _verify(sqlite_session, company, _writes(GRADER), [GRADER])
    assert "protected_paths" not in checks
    assert checks["risk_tier"]["tier"] == "low"
    assert risk.classify([GRADER]) == {"tier": "low", "reasons": []}


def test_a_delivery_outside_the_list_passes_with_its_own_tier(sqlite_session):
    company = _host(sqlite_session, _company(sqlite_session))
    checks, _ = _verify(sqlite_session, company, _writes("src/a.py"), ["src/a.py"])
    guard = checks["protected_paths"]
    assert guard["matches"] == [] and guard["observed"] is True
    assert guard["patterns"] == len(PATTERNS)
    assert "no protected path among 1 observed" in guard["observation"]
    assert checks["risk_tier"]["tier"] == "low"
    # A write the daemon did not complete was not observed as a change.
    checks, _ = _verify(sqlite_session, company, _writes(GRADER, status="failed"))
    assert checks["protected_paths"]["matches"] == []


def test_accept_high_risk_holds_without_a_match():
    assert (
        manager_rules.rule_holds(
            "accept_high_risk",
            {**STRICT_FACTS, "protected_paths": []},
            manager_policy.Policy(),
        )
        is None
    )


def test_without_the_flag_the_strict_rule_still_accepts(sqlite_session):
    company = _company(sqlite_session, autonomy=2)
    item = _protected_hold(sqlite_session, company, recorded=False)
    _policy(sqlite_session, company, "accept_high_risk")
    _cycle(sqlite_session, company, answer("accept", "accept_high_risk"))
    sqlite_session.refresh(item)
    assert item.status == "done"


def _audits(session, company_id):
    return session.exec(
        select(AuditLog).where(
            AuditLog.company_id == company_id,
            AuditLog.action == protected_paths_setting.AUDIT_ACTION,
        )
    ).all()


def test_an_owner_turns_the_flag_on_and_off_audited(sqlite_session, test_client):
    company = _company(sqlite_session)
    owner, owner_h = _person(sqlite_session, company.id, "Owner")
    _, viewer = _person(sqlite_session, company.id, "Viewer")
    url = f"/v1/companies/{company.id}/protected_paths"
    shown = test_client.get(url, headers=viewer).json()
    assert shown["enabled"] is False
    assert {"pattern": "bench/**", "why": shown["patterns"][0]["why"]} in shown[
        "patterns"
    ]
    assert len(shown["patterns"]) == len(PATTERNS)

    on = test_client.put(url, json={"enabled": True}, headers=owner_h)
    assert on.status_code == 200 and on.json()["enabled"] is True
    test_client.put(url, json={"enabled": True}, headers=owner_h)  # no change
    test_client.put(url, json={"enabled": False}, headers=owner_h)
    rows = _audits(sqlite_session, company.id)
    assert [r.result for r in rows] == [
        {"from": False, "to": True},
        {"from": True, "to": False},
    ]
    assert rows[0].agent_id == f"human:{owner.email}"
    assert rows[0].details["user_id"] == owner.id
