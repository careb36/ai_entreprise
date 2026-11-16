"""The Manager agent's brief: what got done, decided, waits and cost, dated by
each piece's own moment and scoped to the company.

The Manager agent (issue #331): suggestions in a fixed format, a policy that
lets it decide alone only by deterministic rules, decisions audited and
overridable, its record against the client's decisions, and the brief. The
shared rows and fakes are in ``manager_agent_helpers``.
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

import manager_agent_helpers
from manager_agent_helpers import _company, _cycle, _hold, _member, _policy, answer
from models import AuditLog
from models.manager import ManagerDecision
from services.management.manager_actions import MANAGER_AGENT_NAME

# The shared fixtures.
auth_on = manager_agent_helpers.auth_on


def test_the_brief_says_what_got_done_decided_waits_and_cost(
    sqlite_session, test_client
):
    company = _company(sqlite_session)
    accepted = _hold(sqlite_session, company)
    _hold(sqlite_session, company, human_led=False)
    _policy(sqlite_session, company, "accept_delivery")
    replies = iter(
        [answer("accept", "accept_delivery"), answer("escalate", "accept_high_risk")]
    )
    _cycle(sqlite_session, company, lambda prompt: next(replies))
    sqlite_session.add(
        AuditLog(company_id=company.id, action="generate_code", cost_tokens=1234)
    )
    sqlite_session.commit()

    brief = test_client.get(f"/v1/companies/{company.id}/brief").json()
    assert brief["done"]["count"] == 1
    assert brief["done"]["tasks"][0]["id"] == accepted.id
    assert brief["agent"]["taken_alone"] == 1
    (decision,) = brief["agent"]["decisions"]
    assert decision["rule"] == "accept_delivery" and decision["reason"]
    assert brief["waiting"]["risk_holds"] == 1
    assert brief["cost"]["tokens_total"] >= 1234
    assert brief["record"]["total"]["taken_alone"] == 1
    assert test_client.get("/v1/companies/999999/brief").status_code == 404


def test_the_brief_is_company_scoped(sqlite_session, test_client, auth_on):
    company = _company(sqlite_session)
    other = _member(sqlite_session, _company(sqlite_session).id, "Manager")
    viewer = _member(sqlite_session, company.id, "Viewer")
    url = f"/v1/companies/{company.id}/brief"
    assert test_client.get(url, headers=other).status_code == 403
    assert test_client.get(url, headers=viewer).status_code == 200


def test_the_brief_dates_work_and_decisions_by_their_own_moments(
    sqlite_session, test_client
):
    company = _company(sqlite_session)
    old = _hold(sqlite_session, company)
    old.status = "done"
    old.comments = [
        *old.comments,
        {"author": "user", "text": "Accepted.", "created_at": "2026-01-01T09:00:00"},
    ]
    sqlite_session.add(old)
    sqlite_session.commit()
    # Commented on today, done long ago: not done in the window.
    old.priority = "low"
    old.updated_at = datetime.now(UTC).replace(tzinfo=None)
    sqlite_session.add(old)
    sqlite_session.commit()
    now = datetime.now(UTC).replace(tzinfo=None)
    sqlite_session.add(
        ManagerDecision(
            company_id=company.id,
            item_kind="task",
            item_id=old.id,
            item_state="done:old",
            category="accept_delivery",
            suggested_decision="accept",
            suggested_at=now - timedelta(days=10),
            decided_by=MANAGER_AGENT_NAME,
            decided_at=now - timedelta(days=10),
            overridden_by="ana",
            overridden_at=now,
        )
    )
    sqlite_session.commit()
    brief = test_client.get(f"/v1/companies/{company.id}/brief").json()
    assert brief["done"]["count"] == 0
    assert brief["agent"]["overridden"] == 1
    assert brief["agent"]["suggested"] == 0 and brief["agent"]["taken_alone"] == 0
