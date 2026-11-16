"""The Manager agent's routes: the policy, the override and the record.

A Manager writes the policy and a Viewer reads it; the client overrides a
decision (once, and never one a later decision replaced), which is counted
and undoes what it did; the record counts agreement with the client and goes
into the prompt, and a rule whose overrides rise is flagged and not applied.

The Manager agent (issue #331): suggestions in a fixed format, a policy that
lets it decide alone only by deterministic rules, decisions audited and
overridable, its record against the client's decisions, and the brief. The
shared rows and fakes are in ``manager_agent_helpers``.
"""

from __future__ import annotations

import manager_agent_helpers
import pytest
from config import settings
from manager_agent_helpers import (
    _approvals,
    _company,
    _cycle,
    _hold,
    _member,
    _outside_run,
    _policy,
    _rows,
    answer,
)
from models import AuditLog, Procedure
from services.loop import run_review
from services.management import manager_actions, manager_policy, manager_record
from services.management.manager_actions import MANAGER_AGENT_NAME
from services.platform import locks
from services.tasks.board import SEND_BACK_MARKER, awaits_risk_acceptance
from sqlmodel import select

# The shared fixtures.
auth_on = manager_agent_helpers.auth_on


def test_a_manager_writes_the_policy_and_a_viewer_reads_it(
    sqlite_session, test_client, auth_on
):
    company = _company(sqlite_session)
    manager = _member(sqlite_session, company.id, "Manager")
    viewer = _member(sqlite_session, company.id, "Viewer")
    outsider = _member(sqlite_session, None, "Manager")
    url = f"/v1/companies/{company.id}/manager_policy"

    empty = test_client.get(url, headers=viewer).json()
    assert empty["rules"] == [] and "cancel_sprint" in empty["always_escalate"]
    assert "accept_high_risk" in empty["catalogue"]
    assert test_client.put(url, json={"rules": []}, headers=viewer).status_code == 403
    assert test_client.get(url, headers=outsider).status_code == 403
    refused = test_client.put(url, json={"rules": ["change_autonomy"]}, headers=manager)
    assert refused.status_code == 422 and "always decided" in refused.json()["detail"]
    saved = test_client.put(
        url, json={"rules": ["accept_delivery"], "max_files": 4}, headers=manager
    ).json()
    assert saved["rules"] == ["accept_delivery"] and saved["max_files"] == 4
    assert saved["updated_by"].startswith("manager-")
    assert test_client.get(url, headers=viewer).json()["rules"] == ["accept_delivery"]
    assert sqlite_session.exec(
        select(AuditLog).where(
            AuditLog.company_id == company.id,
            AuditLog.action == "manager_policy_updated",
        )
    ).first()


def test_the_client_overrides_a_decision_and_it_is_counted(
    sqlite_session, test_client, auth_on, monkeypatch
):
    monkeypatch.setattr(settings, "MANAGER_AGENT_ENABLED", True)
    company = _company(sqlite_session)
    item = _hold(sqlite_session, company)
    _policy(sqlite_session, company, "accept_delivery")
    _cycle(sqlite_session, company, answer("accept", "accept_delivery"))
    (row,) = _rows(sqlite_session, company)
    manager = _member(sqlite_session, company.id, "Manager")
    viewer = _member(sqlite_session, company.id, "Viewer")
    other = _member(sqlite_session, _company(sqlite_session).id, "Manager")

    inbox = test_client.get(f"/v1/inbox?company_id={company.id}", headers=viewer)
    assert [d["id"] for d in inbox.json()["agent_decisions"]] == [row.id]

    url = f"/v1/manager_decisions/{row.id}/override"
    assert test_client.post(url, headers=viewer).status_code == 403
    assert test_client.post(url, headers=other).status_code == 403
    done = test_client.post(url, headers=manager)
    assert done.status_code == 200 and done.json()["outcome"] == "waits_for_a_person"
    assert test_client.post(url, headers=manager).status_code == 409

    sqlite_session.refresh(item)
    assert item.status == "blocked" and awaits_risk_acceptance(item.comments)
    inbox = test_client.get(f"/v1/inbox?company_id={company.id}", headers=viewer)
    (task,) = inbox.json()["blocked_tasks"]
    assert task["id"] == item.id and task["risk_hold"]
    assert task["suggestion"]["overridden_by"].startswith("manager-")

    record = test_client.get(
        f"/v1/manager_record?company_id={company.id}", headers=viewer
    ).json()
    assert record["categories"]["accept_delivery"]["taken_alone"] == 1
    assert record["categories"]["accept_delivery"]["overridden"] == 1
    assert record["total"]["override_rate"] == 1.0
    assert (
        test_client.get(
            f"/v1/manager_record?company_id={company.id}", headers=other
        ).status_code
        == 403
    )
    # Overridden once, the agent never takes that item alone again.
    sqlite_session.refresh(item)
    item.comments = [*item.comments, {"author": "System", "text": "Re-checked."}]
    sqlite_session.add(item)
    sqlite_session.commit()
    _cycle(sqlite_session, company, answer("accept", "accept_delivery"))
    sqlite_session.refresh(item)
    assert item.status == "blocked"


def test_overriding_an_adopted_procedure_brings_back_the_one_it_replaced(
    sqlite_session, test_client
):
    company = _company(sqlite_session, autonomy=3)
    old = Procedure(
        company_id=company.id,
        role="QA",
        title="Review",
        body="v1",
        proposed_by="ScrumMaster",
        status="active",
    )
    new = Procedure(
        company_id=company.id,
        role="QA",
        title="Review",
        body="v2",
        proposed_by="ScrumMaster",
    )
    sqlite_session.add_all([old, new])
    sqlite_session.commit()
    _policy(sqlite_session, company, "adopt_procedure")
    _cycle(sqlite_session, company, answer("adopt", "adopt_procedure"))
    sqlite_session.refresh(old)
    assert old.status == "retired"
    (row,) = _rows(sqlite_session, company)
    result = test_client.post(f"/v1/manager_decisions/{row.id}/override").json()
    assert result["outcome"] == "retired, 1 restored"
    sqlite_session.refresh(old)
    sqlite_session.refresh(new)
    assert (old.status, new.status) == ("active", "retired")


def test_overriding_an_approved_run_records_the_client_s_rejection(
    sqlite_session, test_client
):
    company = _company(sqlite_session)
    run = _outside_run(sqlite_session, company)
    _policy(sqlite_session, company, "approve_run")
    _cycle(sqlite_session, company, answer("approve", "approve_run"))
    (row,) = _rows(sqlite_session, company)
    test_client.post(f"/v1/manager_decisions/{row.id}/override")
    latest = _approvals(sqlite_session, run)[-1]
    assert (latest.approver_name, latest.decision) == ("local demo", "rejected")


def test_the_record_counts_agreement_with_the_client_and_goes_into_the_prompt(
    sqlite_session,
):
    company = _company(sqlite_session)
    agreed = _hold(sqlite_session, company, human_led=False)
    missed = _hold(sqlite_session, company, human_led=False)
    unusable = _hold(sqlite_session, company, human_led=False)
    replies = iter(
        [
            answer("send_back", "accept_high_risk"),
            answer("accept", "accept_high_risk"),
            "no idea",
        ]
    )
    _cycle(sqlite_session, company, lambda prompt: next(replies))
    # The client sends the first two back and accepts the third.
    for item, status, text in (
        (agreed, "todo", f"{SEND_BACK_MARKER} by a person."),
        (missed, "todo", f"{SEND_BACK_MARKER} by a person."),
        (unusable, "done", "High-risk delivery accepted by a person."),
    ):
        sqlite_session.refresh(item)
        item.status = status
        item.comments = [*item.comments, {"author": "user", "text": text}]
        sqlite_session.add(item)
    sqlite_session.commit()

    record = manager_record.track_record(sqlite_session, company.id)
    counts = record["categories"]["accept_high_risk"]
    assert (counts["decided"], counts["agreed"], counts["unusable"]) == (3, 1, 1)
    context = manager_record.record_context(record)
    assert "matched the client's decision 1 of 3" in context
    assert "1 without a usable answer" in context

    _hold(sqlite_session, company, human_led=False)
    _cycle(sqlite_session, company, answer("escalate", "accept_high_risk"))
    assert "matched the client's decision 1 of 3" in _cycle.prompts[0]


def test_a_rule_whose_overrides_rise_is_flagged_and_not_applied(sqlite_session):
    company = _company(sqlite_session)
    _policy(sqlite_session, company, "accept_delivery")
    for _ in range(2):
        _hold(sqlite_session, company)
    _cycle(sqlite_session, company, answer("accept", "accept_delivery"))
    for row in _rows(sqlite_session, company):
        manager_actions.override(sqlite_session, row, "client")
    record = manager_record.track_record(sqlite_session, company.id)
    assert record["flagged_rules"] == ["accept_delivery"]
    fresh = _hold(sqlite_session, company)
    _cycle(sqlite_session, company, answer("accept", "accept_delivery"))
    sqlite_session.refresh(fresh)
    assert fresh.status == "blocked"
    # The policy is untouched: the agent narrows by not applying, never edits.
    assert manager_policy.load(sqlite_session, company.id).rules == ["accept_delivery"]


def test_an_override_already_running_is_refused(sqlite_session):
    company = _company(sqlite_session)
    _hold(sqlite_session, company)
    _policy(sqlite_session, company, "accept_delivery")
    _cycle(sqlite_session, company, answer("accept", "accept_delivery"))
    (row,) = _rows(sqlite_session, company)
    engine = sqlite_session.get_bind()
    with (
        locks.held(engine, manager_actions.OVERRIDE_LOCK, row.id),
        pytest.raises(manager_actions.OverrideRefused) as refused,
    ):
        manager_actions.override(sqlite_session, row, "ana")
    assert refused.value.status == 409
    assert manager_actions.override(sqlite_session, row, "ana")["outcome"] == (
        "waits_for_a_person"
    )


def test_an_override_never_undoes_a_later_decision(sqlite_session):
    company = _company(sqlite_session)
    item = _hold(sqlite_session, company)
    _policy(sqlite_session, company, "accept_delivery", "approve_run")
    run = _outside_run(sqlite_session, company)
    replies = {"accept_delivery": "accept", "approve_run": "approve"}
    _cycle(
        sqlite_session,
        company,
        lambda p: next(
            answer(d, c) for c, d in replies.items() if f"CATEGORY: {c}" in p
        ),
    )
    rows = {r.item_kind: r for r in _rows(sqlite_session, company)}
    task_row, run_row = rows["task"], rows["run"]
    assert task_row.decided_by == run_row.decided_by == MANAGER_AGENT_NAME
    # A person reopened and accepted the task again; a person decided the run.
    sqlite_session.refresh(item)
    item.comments = [
        *item.comments,
        {"author": "user", "text": "Reopened and accepted again by a person."},
    ]
    sqlite_session.add(item)
    run_review.record_approval(
        sqlite_session, run, approver_name="ana", decision="approved", feedback="ok"
    )
    sqlite_session.commit()
    for row in (task_row, run_row):
        with pytest.raises(manager_actions.OverrideRefused) as refused:
            manager_actions.override(sqlite_session, row, "ana")
        assert refused.value.status == 409
        sqlite_session.rollback()
        sqlite_session.refresh(row)
        assert row.overridden_at is None
