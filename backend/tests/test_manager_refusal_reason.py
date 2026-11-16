"""Why the Manager agent did not decide alone (review of PR #596, option 2).

A refused row records the platform's sentence in ``refusal_reason``, a field
of its own: the rule that did not hold, a flagged rule, a policy that names
no rule for the item, a suggestion that is not the rule's decision, a stale
situation, a probe that left a fact unknown when the row still ends refused.
The model's claim (``suggested_*``) stays what the model said; the API, the
inbox and the brief show the reason; the track record never reads it.
"""

from __future__ import annotations

from datetime import UTC, datetime

import pytest
from config import settings
from manager_agent_helpers import _company, _cycle, _hold, _policy, _rows, answer
from models import AgentRun
from models.agent_run import Verification
from models.manager import ManagerDecision
from services.management import manager_agent, manager_observe, manager_record
from services.management.manager_actions import MANAGER_AGENT_NAME
from services.management.manager_rules import WAITING_FOR_INTEGRATION_REVIEW
from services.tasks.board import SEND_BACK_MARKER
from services.workspace.workspace_daemon import DaemonUnavailable
from sqlmodel import select
from test_manager_rules import _delivered

REASON = "The run is verified."


def _now() -> datetime:
    return datetime.now(UTC).replace(tzinfo=None)


def _only(session, company) -> ManagerDecision:
    (row,) = _rows(session, company)
    return row


def _assert_claim_kept(row: ManagerDecision, decision: str) -> None:
    # The platform's sentence never lands in the model's fields.
    assert (row.suggested_decision, row.suggested_reason) == (decision, REASON)
    assert row.suggested_by == MANAGER_AGENT_NAME
    assert row.decided_by is None and row.rule is None and row.outcome is None


def test_a_rule_that_does_not_hold_records_its_own_sentence(sqlite_session):
    company = _company(sqlite_session)
    item = _hold(sqlite_session, company)
    item.comments = [
        *item.comments,
        {"author": "user", "text": "Freeze after the outage: leave it to me."},
    ]
    sqlite_session.add(item)
    sqlite_session.commit()
    _policy(sqlite_session, company, "accept_delivery")
    _cycle(sqlite_session, company, answer("accept", "accept_delivery"))
    row = _only(sqlite_session, company)
    assert row.action_state == manager_agent.NOT_ALLOWED
    assert row.refusal_reason == "accept_delivery: an operator froze the task"
    _assert_claim_kept(row, "accept")


def test_a_flagged_rule_records_that_it_is_flagged(sqlite_session):
    company = _company(sqlite_session)
    _policy(sqlite_session, company, "accept_delivery")
    # Two overrides among the rule's last decisions since the policy was saved.
    for n in range(2):
        sqlite_session.add(
            ManagerDecision(
                company_id=company.id,
                item_kind="task",
                item_id=900000 + n,
                item_state=f"old:{n}",
                category="accept_delivery",
                suggested_decision="accept",
                decided_by=MANAGER_AGENT_NAME,
                rule="accept_delivery",
                outcome="accepted",
                decided_at=_now(),
                overridden_by="ana",
                overridden_at=_now(),
            )
        )
    sqlite_session.commit()
    assert manager_record.track_record(sqlite_session, company.id)["flagged_rules"] == [
        "accept_delivery"
    ]
    item = _hold(sqlite_session, company)
    _cycle(sqlite_session, company, answer("accept", "accept_delivery"))
    (row,) = [r for r in _rows(sqlite_session, company) if r.item_id == item.id]
    assert row.action_state == manager_agent.NOT_ALLOWED
    assert row.refusal_reason == (
        "accept_delivery: the rule is flagged: its override rate rose"
    )
    _assert_claim_kept(row, "accept")


def test_a_policy_that_names_no_rule_for_the_item_says_so(sqlite_session):
    company = _company(sqlite_session)
    _hold(sqlite_session, company)
    _cycle(sqlite_session, company, answer("accept", "accept_delivery"))
    row = _only(sqlite_session, company)
    assert row.action_state == manager_agent.NOT_ALLOWED
    assert row.refusal_reason == "no rule of the policy applies"
    _assert_claim_kept(row, "accept")


def test_a_suggestion_that_is_not_the_rule_s_decision_says_so(sqlite_session):
    company = _company(sqlite_session)
    _hold(sqlite_session, company)
    _policy(sqlite_session, company, "accept_delivery")
    _cycle(sqlite_session, company, answer("escalate", "accept_delivery"))
    row = _only(sqlite_session, company)
    assert row.refusal_reason == "accept_delivery: the suggestion is not accept"
    _assert_claim_kept(row, "escalate")


def test_an_item_that_left_the_inbox_during_the_call_is_stale_with_a_reason(
    sqlite_session,
):
    company = _company(sqlite_session)
    item = _hold(sqlite_session, company)
    _policy(sqlite_session, company, "accept_delivery")

    def person_acts_meanwhile(prompt):
        item.status = "todo"
        sqlite_session.add(item)
        sqlite_session.commit()
        return answer("accept", "accept_delivery")

    _cycle(sqlite_session, company, person_acts_meanwhile)
    row = _only(sqlite_session, company)
    assert row.action_state == manager_agent.STALE
    assert row.refusal_reason == manager_agent.LEFT_THE_INBOX
    _assert_claim_kept(row, "accept")


def test_new_evidence_during_the_call_is_stale_with_a_reason(sqlite_session):
    company = _company(sqlite_session)
    item = _hold(sqlite_session, company)
    _policy(sqlite_session, company, "accept_delivery")
    run = sqlite_session.exec(
        select(AgentRun).where(AgentRun.external_ref == f"task-{item.id}")
    ).one()

    def verified_again_meanwhile(prompt):
        sqlite_session.add(
            Verification(
                company_id=company.id,
                agent_run_id=run.id,
                verdict="verified",
                checks=[{"name": "risk_tier", "outcome": "pass", "tier": "low"}],
            )
        )
        sqlite_session.commit()
        return answer("accept", "accept_delivery")

    _cycle(sqlite_session, company, verified_again_meanwhile)
    (row,) = [r for r in _rows(sqlite_session, company) if r.action_state == "stale"]
    assert row.refusal_reason == manager_agent.SITUATION_CHANGED
    _assert_claim_kept(row, "accept")


@pytest.fixture()
def no_daemon():
    """A workspace daemon nobody can reach: the remote stays unknown."""

    def request(subject, payload, timeout):
        raise DaemonUnavailable("no daemon")

    manager_observe.set_requester(request)
    yield
    manager_observe.set_requester(None)


def test_a_probe_that_left_a_fact_unknown_records_the_rule_that_failed(
    sqlite_session, no_daemon
):
    company = _company(sqlite_session)
    _delivered(sqlite_session, company, verdict="refuted")
    _policy(sqlite_session, company, "publish", publish_enabled=True)
    _cycle(sqlite_session, company, answer("publish", "publish"))
    row = _only(sqlite_session, company)
    # The remote could not be read, but the run is refuted: no wait, a refusal.
    assert row.action_state == manager_agent.NOT_ALLOWED
    assert row.refusal_reason == "publish: the latest run is refuted"
    _assert_claim_kept(row, "publish")


def test_a_probe_that_only_waits_records_no_reason_yet(sqlite_session, no_daemon):
    company = _company(sqlite_session)
    _delivered(sqlite_session, company)
    _policy(sqlite_session, company, "publish", publish_enabled=True)
    _cycle(sqlite_session, company, answer("publish", "publish"))
    row = _only(sqlite_session, company)
    assert row.action_state == manager_agent.PENDING
    assert row.refusal_reason is None


@pytest.mark.parametrize(
    "verdict, sentence",
    [
        (
            {
                "allowed": False,
                "rule": None,
                "why": "the client overrode the agent on this item before",
            },
            "the client overrode the agent on this item before",
        ),
        (
            {
                "allowed": False,
                "rule": "merge_local",
                "why": WAITING_FOR_INTEGRATION_REVIEW,
            },
            f"merge_local: {WAITING_FOR_INTEGRATION_REVIEW}",
        ),
        (
            {
                "allowed": False,
                "rule": None,
                "why": "spend_above_budget: the company is over its token budget",
            },
            "spend_above_budget: the company is over its token budget",
        ),
    ],
)
def test_the_reason_is_the_verdict_s_sentence_after_its_rule(verdict, sentence):
    assert manager_agent.refusal_reason(verdict) == sentence


def test_a_decision_taken_alone_records_no_refusal(sqlite_session):
    company = _company(sqlite_session)
    _hold(sqlite_session, company)
    _policy(sqlite_session, company, "accept_delivery")
    _cycle(sqlite_session, company, answer("accept", "accept_delivery"))
    row = _only(sqlite_session, company)
    assert row.action_state == manager_agent.DONE and row.refusal_reason is None


def test_the_api_the_inbox_and_the_brief_show_the_reason(
    sqlite_session, test_client, monkeypatch
):
    monkeypatch.setattr(settings, "MANAGER_AGENT_ENABLED", True)
    company = _company(sqlite_session)
    item = _hold(sqlite_session, company)
    _cycle(sqlite_session, company, answer("accept", "accept_delivery"))
    (shown,) = test_client.get(f"/v1/manager_decisions?company_id={company.id}").json()[
        "decisions"
    ]
    assert shown["refusal_reason"] == "no rule of the policy applies"
    assert (shown["suggested_decision"], shown["suggested_reason"]) == (
        "accept",
        REASON,
    )
    assert shown["action_state"] == "not_allowed"
    inbox = test_client.get(f"/v1/inbox?company_id={company.id}").json()
    (task,) = [t for t in inbox["blocked_tasks"] if t["id"] == item.id]
    assert task["suggestion"]["refusal_reason"] == "no rule of the policy applies"
    agent = test_client.get(f"/v1/companies/{company.id}/brief").json()["agent"]
    assert agent["refused"] == 1 and agent["taken_alone"] == 0
    (refusal,) = agent["refusals"]
    assert refusal == {
        "id": shown["id"],
        "item_kind": "task",
        "item_id": item.id,
        "category": "accept_delivery",
        "decision": "accept",
        "reason": REASON,
        "action_state": "not_allowed",
        "refusal_reason": "no rule of the policy applies",
    }
    assert agent["decisions"] == []


def test_the_track_record_does_not_read_the_reason(sqlite_session):
    company = _company(sqlite_session)
    item = _hold(sqlite_session, company)
    _cycle(sqlite_session, company, answer("escalate", "accept_delivery"))
    row = _only(sqlite_session, company)
    assert row.refusal_reason
    # The client then sends the task back: the agent escalated, so a miss.
    item.comments = [
        *item.comments,
        {
            "author": "user",
            "text": f"{SEND_BACK_MARKER}: not yet.",
            "created_at": _now().isoformat(),
        },
    ]
    sqlite_session.add(item)
    sqlite_session.commit()
    with_reason = manager_record.track_record(sqlite_session, company.id)
    row.refusal_reason = None
    sqlite_session.add(row)
    sqlite_session.commit()
    without = manager_record.track_record(sqlite_session, company.id)
    assert with_reason == without
    counts = with_reason["categories"]["accept_delivery"]
    assert (counts["decided"], counts["agreed"], counts["taken_alone"]) == (1, 0, 0)
