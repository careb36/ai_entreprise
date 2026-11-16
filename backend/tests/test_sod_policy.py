"""The segregation-of-duties policy and its report (issue #543;
``docs/design/SEGREGATION_OF_DUTIES.md``): only a signed-in Owner changes the
policy, each change audited; a company with one person who can approve gets
an explicit, audited exception that stops once a second person can, never a
silent bypass; with the rule off the approval passes and the report, computed
from the records, lists the violation, a release recorded by the starter and
every integration review's approval with its status. The shared rows are in
``sod_helpers``.
"""

from __future__ import annotations

import secrets

import sod_helpers
from chat_fakes import (
    drain_chat_outbox,  # noqa: F401 - an autouse fixture
    held_task,
)
from models import (
    AuditLog,
    IntegrationReview,
    RunPublication,
    Sprint,
    SprintIntegration,
    Team,
    TeamMember,
)
from services.management import sod_report
from services.platform import api_tokens, sod
from sod_helpers import _approve, _audits, _company, _person, _started_by

# The shared fixtures.
# The shared fixtures.
auth_on = sod_helpers.auth_on


# -- The policy and the single-person exception ------------------------------


def test_only_a_signed_in_owner_changes_the_policy_and_each_change_is_audited(
    sqlite_session, test_client
):
    acme = _company(sqlite_session)
    owner, owner_h = _person(sqlite_session, acme.id, "Owner")
    _, manager_h = _person(sqlite_session, acme.id, "Manager")
    _, raw = api_tokens.create_token(
        sqlite_session, acme.id, "ci", owner.email, [], role="Manager"
    )
    url = f"/v1/companies/{acme.id}/sod/policy"
    assert test_client.get(url, headers=owner_h).json()["mode"] == "high_risk"
    assert (
        test_client.put(url, json={"mode": "off"}, headers=manager_h).status_code == 403
    )
    assert (
        test_client.put(
            url, json={"mode": "off"}, headers={"Authorization": f"Bearer {raw}"}
        ).status_code
        == 403
    )
    assert (
        test_client.put(url, json={"mode": "lax"}, headers=owner_h).status_code == 422
    )

    changed = test_client.put(
        url, json={"mode": "all", "review_interval_days": 30}, headers=owner_h
    )

    assert changed.status_code == 200 and changed.json()["mode"] == "all"
    (row,) = _audits(sqlite_session, acme.id, "sod_policy_updated")
    assert row.result["changed"]["mode"] == {"from": "high_risk", "to": "all"}
    assert row.agent_id == f"human:{owner.email}"


def test_the_single_person_exception_is_explicit_audited_and_listed(
    sqlite_session, test_client
):
    acme = _company(sqlite_session)
    owner, owner_h = _person(sqlite_session, acme.id, "Owner")
    item, run = held_task(sqlite_session, acme.id)
    _started_by(sqlite_session, item, owner.id)
    url = f"/v1/companies/{acme.id}/sod/exception"

    # Without the exception, even a one-person company is refused.
    assert _approve(test_client, acme.id, run, owner_h).status_code == 403
    assert (
        test_client.post(url, json={"reason": "short"}, headers=owner_h).status_code
        == 422
    )
    granted = test_client.post(
        url,
        json={"reason": "Only one person runs this company for now."},
        headers=owner_h,
    )
    assert granted.status_code == 200 and granted.json()["solo_in_force"] is True
    assert _audits(sqlite_session, acme.id, "sod_exception_granted")

    accepted = _approve(test_client, acme.id, run, owner_h)

    assert accepted.status_code == 201
    (used,) = _audits(sqlite_session, acme.id, "sod_exception_applied")
    assert used.result["reason"] == "Only one person runs this company for now."
    report = sod_report.report(sqlite_session, acme.id)
    (approval,) = report["approvals"]
    assert approval["status"] == "exception"
    assert report["violations"] == [approval]


def test_the_exception_stops_when_a_second_person_can_approve(
    sqlite_session, test_client
):
    acme = _company(sqlite_session)
    owner, owner_h = _person(sqlite_session, acme.id, "Owner")
    item, run = held_task(sqlite_session, acme.id)
    _started_by(sqlite_session, item, owner.id)
    test_client.post(
        f"/v1/companies/{acme.id}/sod/exception",
        json={"reason": "Only one person runs this company for now."},
        headers=owner_h,
    )
    # A Viewer made Manager by a team can approve: the company is no longer solo.
    viewer, _ = _person(sqlite_session, acme.id, "Viewer")
    team = Team(company_id=acme.id, name="Reviewers", role="Manager")
    sqlite_session.add(team)
    sqlite_session.commit()
    sqlite_session.add(
        TeamMember(company_id=acme.id, team_id=team.id, user_id=viewer.id)
    )
    sqlite_session.commit()

    refused = _approve(test_client, acme.id, run, owner_h)

    assert refused.status_code == 403
    assert "more than one person who can approve" in refused.json()["detail"]
    # And a new grant is refused while two can approve.
    again = test_client.post(
        f"/v1/companies/{acme.id}/sod/exception",
        json={"reason": "Only one person runs this company for now."},
        headers=owner_h,
    )
    assert again.status_code == 409


def test_with_the_rule_off_the_approval_passes_and_the_report_lists_a_violation(
    sqlite_session, test_client
):
    acme = _company(sqlite_session)
    _, owner_h = _person(sqlite_session, acme.id, "Owner")
    starter, starter_h = _person(sqlite_session, acme.id, "Manager")
    reviewer, reviewer_h = _person(sqlite_session, acme.id, "Manager")
    test_client.put(
        f"/v1/companies/{acme.id}/sod/policy", json={"mode": "off"}, headers=owner_h
    )
    first, run1 = held_task(sqlite_session, acme.id)
    _started_by(sqlite_session, first, starter.id)
    second, run2 = held_task(sqlite_session, acme.id)
    _started_by(sqlite_session, second, starter.id)
    assert _approve(test_client, acme.id, run1, starter_h).status_code == 201
    assert _approve(test_client, acme.id, run2, reviewer_h).status_code == 201

    report = test_client.get(f"/v1/companies/{acme.id}/sod/report", headers=reviewer_h)

    assert report.status_code == 200
    assert report.headers["cache-control"] == "no-store"
    body = report.json()
    statuses = {a["subject"]["id"]: a["status"] for a in body["approvals"]}
    assert statuses == {first.id: "violation", second.id: "separated"}
    assert [v["subject"]["id"] for v in body["violations"]] == [first.id]
    assert body["violations"][0]["conflicts"] == ["started the sprint"]
    # The matrix is counted from the records: two sprints started, one approval each.
    rows = {m["user_id"]: m for m in body["matrix"]["members"]}
    assert rows[starter.id]["request"] == 2 and rows[starter.id]["approve"] == 1
    assert rows[starter.id]["holds_request_and_approval"] is True
    assert rows[reviewer.id]["approve"] == 1 and rows[reviewer.id]["request"] == 0
    assert any(a["role"] == "QA" and a["verify"] == 2 for a in body["matrix"]["agents"])
    _, viewer_h = _person(sqlite_session, acme.id, "Viewer")
    assert (
        test_client.get(
            f"/v1/companies/{acme.id}/sod/report", headers=viewer_h
        ).status_code
        == 403
    )


def test_a_release_recorded_by_the_starter_is_a_violation_in_the_report(sqlite_session):
    acme = _company(sqlite_session)
    starter, _ = _person(sqlite_session, acme.id, "Manager")
    item, run = held_task(sqlite_session, acme.id)
    _started_by(sqlite_session, item, starter.id)
    sqlite_session.add(
        RunPublication(
            company_id=acme.id,
            agent_run_id=run.id,
            branch="fabrique/task",
            base="main",
            commit="a" * 40,
            how="local",
            published_by=starter.full_name,
            published_by_user_id=starter.id,
        )
    )
    sqlite_session.commit()

    report = sod_report.report(sqlite_session, acme.id)

    (release,) = [a for a in report["approvals"] if a["action"] == sod.RELEASE]
    assert release["status"] == "violation"
    assert release["approver"]["user_id"] == starter.id


def _integration_review(
    session,
    company_id,
    sprint,
    integration,
    approver,
    *,
    author_name=None,
    by_agent=False,
):
    uid = approver.id if approver and not by_agent else None
    name = (
        author_name
        or (approver.full_name if approver and not by_agent else None)
        or "Manager agent"
    )
    review = IntegrationReview(
        company_id=company_id,
        sprint_id=sprint.id,
        integration_id=integration.id,
        event="approve",
        body=None,
        outcome="approved",
        commit=integration.commit or "c" * 40,
        merge_base="b" * 40,
        diff_sha256="d" * 64,
        files=1,
        author_name=name,
        author_user_id=uid,
    )
    session.add(review)
    session.commit()
    session.refresh(review)
    return review


def test_the_report_lists_integration_review_approvals_with_each_status(
    sqlite_session,
):
    acme = _company(sqlite_session)
    starter, _ = _person(sqlite_session, acme.id, "Manager")
    other, _ = _person(sqlite_session, acme.id, "Manager")
    solo_starter, _ = _person(sqlite_session, acme.id, "Manager")

    def sprint(started_by=None):
        s = Sprint(
            company_id=acme.id,
            sprint_name=f"S {secrets.token_hex(2)}",
            status="active",
            started_by_user_id=started_by,
        )
        sqlite_session.add(s)
        sqlite_session.commit()
        row = SprintIntegration(
            company_id=acme.id,
            sprint_id=s.id,
            trigger="manual",
            requested_by="human:test",
            status="tests_passed",
            branch="fabrique/integration",
            base="main",
            commit=secrets.token_hex(20),
        )
        sqlite_session.add(row)
        sqlite_session.commit()
        return s, row

    separated_s, separated_i = sprint(started_by=starter.id)
    _integration_review(sqlite_session, acme.id, separated_s, separated_i, other)

    exception_s, exception_i = sprint(started_by=solo_starter.id)
    _integration_review(sqlite_session, acme.id, exception_s, exception_i, solo_starter)
    sqlite_session.add(
        AuditLog(
            company_id=acme.id,
            interaction_id=f"sod:{acme.id}",
            agent_id=f"human:{solo_starter.email}",
            action="sod_exception_applied",
            details={
                "company_id": acme.id,
                "action": sod.RELEASE,
                "subject": {
                    "kind": "sprint_integration_review",
                    "id": exception_i.id,
                    "sprint_id": exception_s.id,
                },
                "actor_user_id": solo_starter.id,
            },
            result={"outcome": "exception"},
        )
    )
    sqlite_session.commit()

    violation_s, violation_i = sprint(started_by=starter.id)
    _integration_review(sqlite_session, acme.id, violation_s, violation_i, starter)

    unattributed_s, unattributed_i = sprint(started_by=None)
    _integration_review(sqlite_session, acme.id, unattributed_s, unattributed_i, other)

    agent_s, agent_i = sprint(started_by=starter.id)
    _integration_review(sqlite_session, acme.id, agent_s, agent_i, None, by_agent=True)

    report = sod_report.report(sqlite_session, acme.id)

    approvals = {
        a["subject"]["id"]: a
        for a in report["approvals"]
        if a["subject"]["kind"] == "sprint_integration_review"
    }
    assert approvals[separated_i.id]["status"] == "separated"
    assert approvals[exception_i.id]["status"] == "exception"
    assert approvals[violation_i.id]["status"] == "violation"
    assert approvals[unattributed_i.id]["status"] == "unattributed"
    assert approvals[agent_i.id]["status"] == "not_a_person"
    for subject_id, row in approvals.items():
        assert row["action"] == sod.RELEASE
        if subject_id != unattributed_i.id:
            assert row["requesters"]
    assert approvals[separated_i.id]["approver"]["user_id"] == other.id
    assert approvals[violation_i.id]["requesters"][0]["why"] == "started the sprint"
    assert approvals[unattributed_i.id]["requesters"] == []
