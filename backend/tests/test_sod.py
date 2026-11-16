"""Segregation of duties: who asked for a change does not approve it (issue #543;
``docs/design/SEGREGATION_OF_DUTIES.md``).

The person who started a sprint does not accept its held delivery (web, an
API token they made, or a chat button), does not merge its runs or its
integration; the person who drafted a specification does not approve it.
Each refusal is audited and changes nothing. A company with one person who
can approve gets an explicit, audited exception, never a silent bypass; the
report lists every approval with requester and approver, computed from the
records.
"""

from __future__ import annotations

import secrets

import auth_tokens
import pytest
from chat_fakes import (
    drain_chat_outbox,  # noqa: F401 - an autouse fixture
    held_task,
    slack_action,
    slack_request,
    world,
)
from chat_fakes import link as link_account
from config import settings
from models import (
    AuditLog,
    ChatApprovalMessage,
    Company,
    CompanyMember,
    IntegrationReview,
    RunPublication,
    RunReview,
    Specification,
    Sprint,
    SprintIntegration,
    Team,
    TeamMember,
    User,
)
from services.integrations.chat_messages import APPROVE
from services.management import sod_report
from services.platform import api_tokens, sod
from services.platform.sod import Person, Requester
from sqlmodel import select

GOOD_SPEC = {
    "title": "Task board",
    "summary": "A board where a team sees its tasks.",
    "requirements": [
        {
            "title": "List the tasks",
            "description": "The board lists the tasks of the company.",
            "criteria": [
                {
                    "given": "a company with two tasks",
                    "when": "a member opens the board",
                    "then": "the list has one row per task",
                }
            ],
        }
    ],
    "quality": [
        {"attribute": "performance", "target": "the board renders in under 300 ms"}
    ],
    "constraints": ["TypeScript and React"],
}


@pytest.fixture(autouse=True)
def auth_on(monkeypatch):
    monkeypatch.setattr(settings, "AUTH_REQUIRED", True)
    key = secrets.token_urlsafe(48)
    monkeypatch.setattr(auth_tokens, "_configured_key", lambda: key)


def _company(session) -> Company:
    row = Company(company_name=f"SoD {secrets.token_hex(3)}")
    session.add(row)
    session.commit()
    return row


def _person(session, company_id: int, role: str):
    user = User(
        email=f"{role.lower()}-{secrets.token_hex(4)}@example.test",
        full_name=f"{role} {secrets.token_hex(2)}",
        password_hash="unused",
    )
    session.add(user)
    session.commit()
    session.add(CompanyMember(company_id=company_id, user_id=user.id, human_role=role))
    session.commit()
    token = auth_tokens.create_access_token(user_id=user.id, email=user.email)
    return user, {"Authorization": f"Bearer {token}"}


def _started_by(session, item, user_id):
    sprint = session.get(Sprint, item.sprint_id)
    sprint.started_by_user_id = user_id
    session.add(sprint)
    session.commit()
    return sprint


def _audits(session, company_id, action):
    return session.exec(
        select(AuditLog).where(
            AuditLog.company_id == company_id, AuditLog.action == action
        )
    ).all()


def _approve(client, company_id, run, headers):
    return client.post(
        f"/v1/agent_runs/{run.id}/reviews",
        json={"company_id": company_id, "event": "approve", "body": None},
        headers=headers,
    )


# -- The rule (pure) ---------------------------------------------------------


def test_the_rule_refuses_the_requester_and_knows_its_modes():
    ana, bruno = Person(1, "ana@example.test"), Person(2, "bruno@example.test")
    started = [Requester(ana, "started the sprint")]
    assert sod.decide("high_risk", sod.ACCEPT_HELD, ana, started, False).outcome == (
        "refused"
    )
    assert sod.decide("high_risk", sod.ACCEPT_HELD, bruno, started, False).outcome == (
        "separated"
    )
    assert sod.decide("high_risk", sod.ACCEPT_HELD, ana, started, True).outcome == (
        "exception"
    )
    assert sod.decide("off", sod.ACCEPT_HELD, ana, started, False).outcome == "not_held"
    # high_risk holds held deliveries, specifications and releases, not the rest.
    assert not sod.held("high_risk", sod.APPROVE_ARCH)
    assert sod.held("all", sod.APPROVE_ARCH) and sod.held("all", sod.APPROVE_RUN)
    assert sod.decide("high_risk", sod.RELEASE, ana, [], False).outcome == (
        "unattributed"
    )
    # Matched by account id when both have one, else by email (any case).
    assert sod.same_person(
        Person(None, "Ana@Example.test"), Person(None, "ana@example.test")
    )
    assert not sod.same_person(Person(1, "x@example.test"), Person(2, "x@example.test"))


# -- A held delivery: web, token, chat ---------------------------------------


def test_who_started_the_sprint_cannot_accept_its_held_delivery_from_the_web(
    sqlite_session, test_client
):
    acme = _company(sqlite_session)
    starter, starter_h = _person(sqlite_session, acme.id, "Manager")
    _, other_h = _person(sqlite_session, acme.id, "Manager")
    item, run = held_task(sqlite_session, acme.id)
    _started_by(sqlite_session, item, starter.id)

    refused = _approve(test_client, acme.id, run, starter_h)

    assert refused.status_code == 403
    assert "Segregation of duties" in refused.json()["detail"]
    assert "started the sprint" in refused.json()["detail"]
    sqlite_session.refresh(item)
    assert item.status == "blocked"
    assert not sqlite_session.exec(
        select(RunReview).where(RunReview.agent_run_id == run.id)
    ).all()
    (row,) = _audits(sqlite_session, acme.id, "sod_refused")
    assert row.details["action"] == sod.ACCEPT_HELD
    assert row.details["actor_user_id"] == starter.id
    assert row.details["subject"]["kind"] == "task"

    accepted = _approve(test_client, acme.id, run, other_h)
    assert accepted.status_code == 201 and accepted.json()["outcome"] == "accepted"
    (approved,) = _audits(sqlite_session, acme.id, "run_review_approve")
    assert approved.details["sod"] == "separated"


def test_a_token_the_starter_made_is_refused_like_its_creator(
    sqlite_session, test_client
):
    acme = _company(sqlite_session)
    starter, _ = _person(sqlite_session, acme.id, "Manager")
    item, run = held_task(sqlite_session, acme.id)
    _started_by(sqlite_session, item, starter.id)
    _, raw = api_tokens.create_token(
        sqlite_session, acme.id, "ci", starter.email, [], role="Manager"
    )

    refused = _approve(test_client, acme.id, run, {"Authorization": f"Bearer {raw}"})

    assert refused.status_code == 403
    (row,) = _audits(sqlite_session, acme.id, "sod_refused")
    assert row.details["actor_user_id"] == starter.id
    assert row.details["actor_token_id"] is not None


def test_a_chat_button_refuses_the_starter_and_keeps_the_message_open(
    sqlite_session, test_client, monkeypatch
):
    w = world(sqlite_session, monkeypatch)
    manager, _ = w["manager"]
    _started_by(sqlite_session, w["item"], manager.id)
    chat_user = "U" + secrets.token_hex(5).upper()
    link_account(
        sqlite_session, w["company"].id, manager, "slack", w["team_id"], chat_user
    )

    response = slack_request(
        test_client,
        w["slack"],
        w["slack_secret"],
        payload=slack_action(w, chat_user, APPROVE),
    )

    assert response.status_code == 200
    sqlite_session.refresh(w["item"])
    assert w["item"].status == "blocked"
    message = sqlite_session.get(ChatApprovalMessage, w["messages"]["slack"].id)
    sqlite_session.refresh(message)
    assert message.status == "posted"  # released: another person can decide
    (row,) = _audits(sqlite_session, w["company"].id, "sod_refused")
    assert row.details["subject"]["via"] == "slack"
    refusal = _audits(sqlite_session, w["company"].id, "chat_approval_refused")
    assert "Segregation of duties" in refusal[-1].result["reason"]


def test_marking_a_held_delivery_done_on_the_board_is_held_to_the_same_rule(
    sqlite_session, test_client
):
    acme = _company(sqlite_session)
    starter, starter_h = _person(sqlite_session, acme.id, "Manager")
    _, other_h = _person(sqlite_session, acme.id, "Manager")
    item, _run = held_task(sqlite_session, acme.id)
    _started_by(sqlite_session, item, starter.id)

    refused = test_client.patch(
        f"/v1/backlog/{item.id}", json={"status": "done"}, headers=starter_h
    )

    assert refused.status_code == 403
    sqlite_session.refresh(item)
    assert item.status == "blocked"
    (row,) = _audits(sqlite_session, acme.id, "sod_refused")
    assert row.details["subject"] == {"kind": "task", "id": item.id, "via": "board"}
    moved = test_client.patch(
        f"/v1/backlog/{item.id}", json={"status": "done"}, headers=other_h
    )
    assert moved.status_code == 200 and moved.json()["status"] == "done"


# -- A specification and a release -------------------------------------------


def test_who_drafted_a_specification_cannot_approve_it(sqlite_session, test_client):
    acme = _company(sqlite_session)
    _, drafter_h = _person(sqlite_session, acme.id, "Manager")
    _, other_h = _person(sqlite_session, acme.id, "Manager")
    spec = test_client.post(
        "/v1/specifications",
        json={"company_id": acme.id, **GOOD_SPEC},
        headers=drafter_h,
    ).json()

    refused = test_client.post(
        f"/v1/specifications/{spec['id']}/approve", headers=drafter_h
    )

    assert refused.status_code == 403
    assert "drafted the specification" in refused.json()["detail"]
    assert sqlite_session.get(Specification, spec["id"]).status == "draft"
    assert _audits(sqlite_session, acme.id, "sod_refused")[0].details["action"] == (
        sod.APPROVE_SPEC
    )
    ok = test_client.post(f"/v1/specifications/{spec['id']}/approve", headers=other_h)
    assert ok.status_code == 200 and ok.json()["status"] == "approved"


def test_who_started_the_sprint_cannot_merge_its_run_or_its_integration(
    sqlite_session, test_client
):
    acme = _company(sqlite_session)
    starter, starter_h = _person(sqlite_session, acme.id, "Manager")
    item, run = held_task(sqlite_session, acme.id)
    sprint = _started_by(sqlite_session, item, starter.id)
    row = SprintIntegration(
        company_id=acme.id,
        sprint_id=sprint.id,
        trigger="manual",
        requested_by="human:test",
        status="passed",
        branch="fabrique/integration",
        base="main",
    )
    sqlite_session.add(row)
    sqlite_session.commit()

    merge = test_client.post(
        f"/v1/agent_runs/{run.id}/merge?company_id={acme.id}", headers=starter_h
    )
    integrated = test_client.post(
        f"/v1/sprints/{sprint.id}/integration/merge", headers=starter_h
    )

    assert merge.status_code == integrated.status_code == 403
    kinds = sorted(
        r.details["subject"]["kind"]
        for r in _audits(sqlite_session, acme.id, "sod_refused")
    )
    assert kinds == ["agent_run", "sprint_integration"]
    assert not sqlite_session.exec(
        select(RunPublication).where(RunPublication.agent_run_id == run.id)
    ).all()


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
    for row in approvals.values():
        assert row["action"] == sod.RELEASE
        assert row["requesters"]
    assert approvals[separated_i.id]["approver"]["user_id"] == other.id
    assert approvals[violation_i.id]["requesters"][0]["why"] == "started the sprint"
    assert approvals[unattributed_i.id]["requesters"] == []
