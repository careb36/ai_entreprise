"""A company's limits on its agents' work (issue #766).

``companies.limits`` holds the Developer's tool rounds, tool-result size and
answer cap and the task's rework budget, each within the code's floors and
ceilings (``services/contracts/company_limits.py``). Any member reads them
beside the deployment's defaults; only an Owner changes them, audited on an
intact chain. The model's profile applies the company's values above the
operator's per-model override, and the rework budget of QA and both gates
is the company's when it set one, else the deployment's ``MAX_REWORKS``.
"""

from __future__ import annotations

import secrets

import audit_signing
import pytest
from audit_chain import verify_chain
from config import Settings, settings
from models import AuditLog, Company
from services.contracts import company_limits
from services.loop.worker import qa_review
from services.models import model_catalog, model_profile
from services.platform import company_limits_setting
from sqlmodel import select
from test_git_workflow import _member, enforcement  # noqa: F401


@pytest.fixture(autouse=True)
def _audit_and_defaults(monkeypatch, tmp_path):
    monkeypatch.setenv("AUDIT_LOG_PATH", str(tmp_path / "audit"))
    key = secrets.token_hex(16)
    monkeypatch.setattr(audit_signing, "_configured_key", lambda: key)
    monkeypatch.setattr(model_catalog, "_catalog", model_catalog._Catalog())
    monkeypatch.setattr(settings, "MODELS_CATALOG_REFRESH_HOURS", 0)
    monkeypatch.setattr(settings, "MODEL_PROFILES", {})
    monkeypatch.setattr(settings, "DEVELOPER_TOOL_ROUNDS", 8)
    monkeypatch.setattr(settings, "MCP_RESULT_MAX_CHARS", 4000)
    monkeypatch.setattr(settings, "DEVELOPER_MAX_OUTPUT_TOKENS", 0)


def _company(session, limits=None):
    company = Company(company_name=f"Limits {secrets.token_hex(3)}", limits=limits)
    session.add(company)
    session.commit()
    session.refresh(company)
    return company


# --- the limits as data ------------------------------------------------------------


def test_limits_within_the_range_are_kept_and_null_clears_them():
    assert company_limits.validate(None) is None
    assert company_limits.validate({}) is None
    assert company_limits.validate({"tool_rounds": 12, "max_reworks": 0}) == {
        "tool_rounds": 12,
        "max_reworks": 0,
    }


@pytest.mark.parametrize(
    ("raw", "says"),
    [
        ({"tool_rounds": 51}, "between 1 and 50"),
        ({"max_reworks": 11}, "between 0 and 10"),
        ({"result_max_chars": 10}, "between 500"),
        ({"tool_rounds": "12"}, "whole number"),
        ({"tool_rounds": True}, "whole number"),
        ({"temperature": 1}, "unknown limit"),
        ([1, 2], "an object or null"),
    ],
)
def test_a_limit_outside_the_range_is_refused_with_the_reason(raw, says):
    with pytest.raises(company_limits.InvalidLimits, match=says):
        company_limits.validate(raw)


def test_a_stored_value_that_no_longer_validates_is_never_applied():
    stored = {"tool_rounds": 9999, "max_reworks": 2, "nonsense": 1}
    assert company_limits.parse(stored) == {"max_reworks": 2}
    assert company_limits.parse("not an object") == {}


def test_the_deployment_rework_budget_and_its_old_name():
    assert company_limits.deployment_rework_limit(None) == 3
    assert company_limits.deployment_rework_limit(None, 5) == 5
    assert company_limits.deployment_rework_limit(2, 5) == 2
    assert company_limits.deployment_rework_limit(-4) == 0
    for given in ("lots", ""):
        assert Settings(MAX_REWORKS=given).MAX_REWORKS is None
    assert Settings(MAX_REWORKS="2").MAX_REWORKS == 2


# --- who sets them ---------------------------------------------------------------


def test_only_an_owner_changes_the_limits_audited_on_an_intact_chain(
    sqlite_session,
    test_client,
    enforcement,  # noqa: F811
):
    company = _company(sqlite_session)
    viewer = _member(sqlite_session, company.id, "Viewer")
    manager = _member(sqlite_session, company.id, "Manager")
    owner = _member(sqlite_session, company.id, "Owner")
    url = f"/v1/companies/{company.id}/limits"

    read = test_client.get(url, headers=viewer)
    assert read.status_code == 200, read.text
    body = read.json()
    assert body["limits"] is None
    assert body["defaults"]["tool_rounds"] == 8
    assert body["ceilings"]["max_reworks"] == 10
    for who in (viewer, manager):
        refused = test_client.put(
            url, json={"limits": {"tool_rounds": 12}}, headers=who
        )
        assert refused.status_code == 403
    bad = test_client.put(url, json={"limits": {"tool_rounds": 99}}, headers=owner)
    assert bad.status_code == 422 and "between 1 and 50" in bad.json()["detail"]

    changed = test_client.put(
        url, json={"limits": {"tool_rounds": 12, "max_reworks": 1}}, headers=owner
    )
    assert changed.status_code == 200, changed.text
    assert changed.json()["limits"] == {"tool_rounds": 12, "max_reworks": 1}
    same = test_client.put(
        url, json={"limits": {"tool_rounds": 12, "max_reworks": 1}}, headers=owner
    )
    assert same.status_code == 200
    cleared = test_client.put(url, json={"limits": None}, headers=owner)
    assert cleared.json()["limits"] is None

    rows = sqlite_session.exec(
        select(AuditLog)
        .where(
            AuditLog.company_id == company.id,
            AuditLog.action == company_limits_setting.AUDIT_ACTION,
        )
        .order_by(AuditLog.id)
    ).all()
    assert [r.result["to"] for r in rows] == [
        {"tool_rounds": 12, "max_reworks": 1},
        None,
    ]
    assert rows[0].result["from"] is None
    assert rows[0].details["user_id"] is not None
    assert verify_chain(sqlite_session, company.id)["status"] == "intact"


# --- what they change ------------------------------------------------------------


def test_the_company_s_limit_wins_over_the_model_override_within_the_ceiling(
    monkeypatch,
):
    monkeypatch.setattr(
        settings, "MODEL_PROFILES", {"deepseek-v4-pro": {"tool_rounds": 16}}
    )
    p = model_profile.profile("deepseek-v4-pro", {"tool_rounds": 12})
    assert p["tool_rounds"] == 12 and p["sources"]["tool_rounds"] == "company"
    p = model_profile.profile("deepseek-v4-pro", None)
    assert p["tool_rounds"] == 16 and p["sources"]["tool_rounds"] == "override"
    # A stored value past the ceiling is dropped, never applied.
    p = model_profile.profile("deepseek-v4-pro", {"tool_rounds": 9999})
    assert p["tool_rounds"] == 16
    assert (
        model_profile.CEILINGS["tool_rounds"] == company_limits.CEILINGS["tool_rounds"]
    )


def test_the_rework_budget_is_the_company_s_when_it_set_one(
    sqlite_session, monkeypatch
):
    monkeypatch.setattr(qa_review, "engine", sqlite_session.get_bind())
    monkeypatch.setattr(qa_review, "MAX_REWORKS", 3)
    own = _company(sqlite_session, {"max_reworks": 1})
    plain = _company(sqlite_session)
    assert qa_review.rework_limit(sqlite_session, own.id) == 1
    assert qa_review.rework_limit(sqlite_session, plain.id) == 3
    assert qa_review.rework_limit(sqlite_session, None) == 3
    assert qa_review.rework_limit_of(own.id) == 1

    record = type("Record", (), {"reworks": lambda self: 1})()
    reviewer = {"name": "QA", "role": "QA"}
    assert qa_review._rework_budget(record, reviewer, True, False, 1)[1] is False
    assert qa_review._rework_budget(record, reviewer, True, False, 3)[1] is True
