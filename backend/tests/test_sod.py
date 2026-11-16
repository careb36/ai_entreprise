"""Segregation of duties: who asked for a change does not approve it (issue #543;
``docs/design/SEGREGATION_OF_DUTIES.md``).

The person who started a sprint does not accept its held delivery (web, an
API token they made, a chat button or the board), does not merge its runs or
its integration; the person who drafted a specification does not approve it.
Each refusal is audited and changes nothing. The policy, the single-person
exception and the report are in ``test_sod_policy.py``; the shared rows in
``sod_helpers``.
"""

from __future__ import annotations

import secrets

import sod_helpers
from chat_fakes import (
    drain_chat_outbox,  # noqa: F401 - an autouse fixture
    held_task,
    slack_action,
    slack_request,
    world,
)
from chat_fakes import link as link_account
from models import (
    ChatApprovalMessage,
    RunPublication,
    RunReview,
    Specification,
    SprintIntegration,
)
from services.integrations.chat_messages import APPROVE
from services.platform import api_tokens, sod
from services.platform.sod import Person, Requester
from sod_helpers import _approve, _audits, _company, _person, _started_by
from sqlmodel import select

# The shared fixtures.
# The shared fixtures.
auth_on = sod_helpers.auth_on


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
