"""Four-eyes on a Manager pair and the break-glass exclusion (issue #703;
``docs/design/SSO_GROUP_MAPPING.md``, R23–R25, R27–R29, G17, G19–G21): a
Manager pair stays pending, self-approval is refused with a ``sod_refused``
row, an approver id in the body is ignored, an API token is refused, a
changed or expired proposal is refused, the single-Owner confirmation is
honoured only with one Owner and refused after a recent removal of an Owner,
and is flagged in the access review; an exclusion removes at once, notifies
the other Owners, survives sign-ins and is lifted by another Owner; both are
listed in the access review."""

from __future__ import annotations

from datetime import timedelta

from models import (
    AuditLog,
    OutboxEvent,
    SsoGroupMapping,
    SsoManagerApproval,
    SsoTeamExclusion,
)
from services.platform import api_tokens
from sqlmodel import select
from sso_fake_idp import company, connect, member, sign_in
from sso_group_world import (
    GROUP,
    audits,
    groups_on,
    mappings_url,
    members_of,
    now,
    observations,
    pair,
    periods,
    team,
    teams_url,
)


def _manager_world(session, owners: int = 2):
    acme = company(session)
    conn = groups_on(session, connect(session, acme.id), cap="Manager")
    leads = team(session, acme.id, role="Manager")
    people = [member(session, acme.id, "Owner") for _ in range(owners)]
    return acme, conn, leads, people


def _propose(client, company_id, headers, team_id):
    body = {
        "groups_enabled": True,
        "groups_claim": "groups",
        "group_role_cap": "Manager",
        "mappings": [{"group_value": GROUP, "team_id": team_id}],
    }
    response = client.put(mappings_url(company_id), json=body, headers=headers)
    assert response.status_code == 200, response.text
    return response.json()


def _approval(session) -> SsoManagerApproval:
    session.expire_all()
    return session.exec(select(SsoManagerApproval)).one()


def test_a_manager_pair_waits_for_a_second_owner(sqlite_session, test_client, fake_idp):
    acme, _conn, leads, [(alba, alba_h), (beto, beto_h)] = _manager_world(
        sqlite_session
    )
    body = _propose(test_client, acme.id, alba_h, leads.id)
    assert body["mappings"] == []
    (pending,) = body["approvals"]
    assert pending["state"] == "pending" and pending["requested_by"] == alba.id
    assert sqlite_session.exec(select(SsoGroupMapping)).all() == []
    (row,) = audits(sqlite_session, acme.id, "sso_manager_pair_proposed")
    assert row.result["proposal_sha256"] == _approval(sqlite_session).proposal_sha256
    # Proposing again keeps the one proposal.
    _propose(test_client, acme.id, alba_h, leads.id)
    assert len(sqlite_session.exec(select(SsoManagerApproval)).all()) == 1

    url = mappings_url(acme.id, f"/approvals/{pending['id']}/approve")
    # The proposer, even naming someone else in the body, is refused.
    mine = test_client.post(url, json={"approver_id": beto.id}, headers=alba_h)
    assert mine.status_code == 403
    (refusal,) = audits(sqlite_session, acme.id, "sod_refused")
    assert refusal.result["requested_by"] == alba.id
    assert refusal.result["conflict"] == "proposed the pair"
    assert _approval(sqlite_session).state == "pending"

    approved = test_client.post(url, headers=beto_h)
    assert approved.status_code == 200, approved.text
    approval = _approval(sqlite_session)
    assert (approval.state, approval.decided_by) == ("approved", beto.id)
    (mapping,) = sqlite_session.exec(select(SsoGroupMapping)).all()
    assert (mapping.team_id, mapping.approval_id) == (leads.id, approval.id)
    assert audits(sqlite_session, acme.id, "sso_manager_pair_approved")
    assert test_client.post(url, headers=beto_h).status_code == 409


def test_an_api_token_cannot_approve(sqlite_session, test_client, fake_idp):
    acme, _conn, leads, [(_, alba_h), (beto, _)] = _manager_world(sqlite_session)
    pending = _propose(test_client, acme.id, alba_h, leads.id)["approvals"][0]
    _, raw = api_tokens.create_token(
        sqlite_session, acme.id, "ci", role="Manager", created_by=beto.email
    )
    token = sqlite_session.exec(select(api_tokens.ApiToken)).all()[-1]
    token.role, token.created_by_user_id = "Owner", beto.id
    sqlite_session.add(token)
    sqlite_session.commit()
    response = test_client.post(
        mappings_url(acme.id, f"/approvals/{pending['id']}/approve"),
        headers={"Authorization": f"Bearer {raw}"},
    )
    assert response.status_code == 403
    assert _approval(sqlite_session).state == "pending"


def test_a_changed_or_expired_proposal_is_refused(
    sqlite_session, test_client, fake_idp
):
    acme, _conn, leads, [(_, alba_h), (_, beto_h)] = _manager_world(sqlite_session)
    pending = _propose(test_client, acme.id, alba_h, leads.id)["approvals"][0]
    url = mappings_url(acme.id, f"/approvals/{pending['id']}/approve")
    leads.role = "Viewer"  # the proposal described a Manager team
    sqlite_session.add(leads)
    sqlite_session.commit()
    changed = test_client.post(url, headers=beto_h)
    assert changed.status_code == 409 and "changed" in changed.json()["detail"]
    assert _approval(sqlite_session).state == "pending"  # left for the proposer
    withdrawn = test_client.post(
        mappings_url(acme.id, f"/approvals/{pending['id']}/withdraw"), headers=beto_h
    )
    assert withdrawn.status_code == 403  # only the proposer withdraws
    assert (
        test_client.post(
            mappings_url(acme.id, f"/approvals/{pending['id']}/withdraw"),
            headers=alba_h,
        ).status_code
        == 200
    )
    assert _approval(sqlite_session).state == "withdrawn"

    leads.role = "Manager"
    sqlite_session.add(leads)
    sqlite_session.commit()
    again = _propose(test_client, acme.id, alba_h, leads.id)["approvals"][0]
    row = sqlite_session.get(SsoManagerApproval, again["id"])
    row.expires_at = now() - timedelta(minutes=1)
    sqlite_session.add(row)
    sqlite_session.commit()
    expired = test_client.post(
        mappings_url(acme.id, f"/approvals/{again['id']}/approve"), headers=beto_h
    )
    assert expired.status_code == 409
    sqlite_session.refresh(row)
    assert row.state == "expired"
    rejected_one = _propose(test_client, acme.id, alba_h, leads.id)["approvals"][0]
    rejected = test_client.post(
        mappings_url(acme.id, f"/approvals/{rejected_one['id']}/reject"),
        json={"reason": "not this quarter"},
        headers=beto_h,
    )
    assert rejected.status_code == 200
    assert (
        sqlite_session.get(SsoManagerApproval, rejected_one["id"]).state == "rejected"
    )
    assert sqlite_session.exec(select(SsoGroupMapping)).all() == []


def _confirm(client, company_id, headers, approval_id, confirmation, reason):
    return client.post(
        mappings_url(company_id, f"/approvals/{approval_id}/confirm-single-owner"),
        json={"confirmation": confirmation, "reason": reason},
        headers=headers,
    )


def test_a_single_owner_confirms_alone_and_the_review_flags_it(
    sqlite_session, test_client, fake_idp
):
    acme, _conn, leads, [(_alba, alba_h)] = _manager_world(sqlite_session, owners=1)
    pending = _propose(test_client, acme.id, alba_h, leads.id)["approvals"][0]
    reason = "The only Owner of a small company"
    assert (
        _confirm(
            test_client, acme.id, alba_h, pending["id"], "wrong", reason
        ).status_code
        == 422
    )
    assert (
        _confirm(
            test_client, acme.id, alba_h, pending["id"], leads.name, "short"
        ).status_code
        == 422
    )
    done = _confirm(test_client, acme.id, alba_h, pending["id"], leads.name, reason)
    assert done.status_code == 200, done.text
    approval = _approval(sqlite_session)
    assert approval.state == "confirmed_single_owner" and approval.reason == reason
    assert sqlite_session.exec(select(SsoGroupMapping)).one().approval_id == approval.id
    (row,) = audits(sqlite_session, acme.id, "sso_manager_pair_single_owner")
    assert row.result["flagged_for_access_review"] is True

    review = test_client.get(f"/v1/companies/{acme.id}/access-review", headers=alba_h)
    (flagged,) = [i for i in review.json()["sso"] if i["kind"] == "manager_pair"]
    assert flagged["id"] == approval.id and flagged["flagged"] is True

    # A second Owner arrives and confirms it in the next review.
    _beto, beto_h = member(sqlite_session, acme.id, "Owner")
    listing = test_client.get(
        f"/v1/companies/{acme.id}/access-review", headers=beto_h
    ).json()
    decisions = [
        {"kind": i["kind"], "id": i["id"], "decision": "confirm"}
        for i in listing["members"] + listing["tokens"] + listing["sso"]
    ]
    completed = test_client.post(
        f"/v1/companies/{acme.id}/access-review",
        json={"decisions": decisions},
        headers=beto_h,
    )
    assert completed.status_code == 201, completed.text
    sqlite_session.expire_all()
    assert _approval(sqlite_session).reviewed_in == completed.json()["id"]
    later = test_client.get(f"/v1/companies/{acme.id}/access-review", headers=beto_h)
    assert [i for i in later.json()["sso"] if i["kind"] == "manager_pair"] == []


def test_the_single_owner_path_is_closed_with_two_owners_or_after_a_recent_removal(
    sqlite_session, test_client, fake_idp
):
    acme, _conn, leads, [(_, alba_h), _] = _manager_world(sqlite_session, owners=2)
    pending = _propose(test_client, acme.id, alba_h, leads.id)["approvals"][0]
    reason = "Trying to approve my own pair"
    two = _confirm(test_client, acme.id, alba_h, pending["id"], leads.name, reason)
    assert two.status_code == 409 and "not_single_owner" in two.json()["detail"]

    solo, _solo_conn, solo_leads, [(_, solo_h), (gone, _)] = _manager_world(
        sqlite_session, owners=2
    )
    removed = test_client.delete(
        f"/v1/company/{solo.id}/members/{gone.id}", headers=solo_h
    )
    assert removed.status_code == 204
    pending = _propose(test_client, solo.id, solo_h, solo_leads.id)["approvals"][0]
    recent = _confirm(
        test_client, solo.id, solo_h, pending["id"], solo_leads.name, reason
    )
    assert recent.status_code == 409
    assert "recent_owner_change" in recent.json()["detail"]
    old = sqlite_session.exec(
        select(AuditLog).where(
            AuditLog.company_id == solo.id, AuditLog.action == "member_removed"
        )
    ).one()
    assert old.result["human_role"] == "Owner"


def _exclusion_world(session):
    acme = company(session)
    conn = groups_on(session, connect(session, acme.id))
    platform = team(session, acme.id)
    pair(session, conn, GROUP, platform)
    owners = [member(session, acme.id, "Owner") for _ in range(2)]
    return acme, conn, platform, owners


def test_a_break_glass_exclusion_removes_at_once_notifies_and_survives_sign_ins(
    sqlite_session, test_client, fake_idp
):
    acme, conn, platform, [(alba, alba_h), (beto, beto_h)] = _exclusion_world(
        sqlite_session
    )
    fake_idp.claims = {"groups": [GROUP]}
    sign_in(test_client, fake_idp, conn.slug)
    (ana,) = members_of(sqlite_session, platform.id)
    url = teams_url(acme.id, f"/{platform.id}/exclusions")
    short = test_client.post(
        url, json={"user_id": ana, "reason": "short"}, headers=alba_h
    )
    assert short.status_code == 422
    created = test_client.post(
        url,
        json={"user_id": ana, "reason": "Suspected account compromise"},
        headers=alba_h,
    )
    assert created.status_code == 201, created.text
    exclusion_id = created.json()["id"]
    assert members_of(sqlite_session, platform.id) == []
    (period,) = periods(sqlite_session, platform.id, ana)
    assert period.ended_by == f"owner:{alba.id}"
    assert period.ended_exclusion_id == exclusion_id
    (notice,) = sqlite_session.exec(select(OutboxEvent)).all()
    message = notice.payload["message"]
    assert message["kind"] == "sso_team_exclusion_created"
    assert message["recipients"] == [beto.id]  # every other Owner, not the actor
    (row,) = audits(sqlite_session, acme.id, "sso_team_exclusion_created")
    assert row.result["owners_notified"] == [beto.id] and row.decision_signature
    duplicate = test_client.post(
        url,
        json={"user_id": ana, "reason": "Suspected account compromise"},
        headers=beto_h,
    )
    assert duplicate.status_code == 409

    # The IdP still says the group: the mapping does not add them back.
    sign_in(test_client, fake_idp, conn.slug)
    assert members_of(sqlite_session, platform.id) == []
    last = observations(sqlite_session, conn.id)[-1]
    assert {"team_id": platform.id, "reason": "excluded"} in last.skipped
    assert audits(sqlite_session, acme.id, "sso_group_sync")[-1].result["skipped"]

    # Another Owner lifts it; nobody is added back until the next sign-in.
    lift_url = f"{url}/{exclusion_id}"
    lifted = test_client.request(
        "DELETE",
        lift_url,
        json={"reason": "Account recovered and checked"},
        headers=beto_h,
    )
    assert lifted.status_code == 200, lifted.text
    assert lifted.json()["lifted_by"] == beto.id
    assert members_of(sqlite_session, platform.id) == []
    notices = sqlite_session.exec(select(OutboxEvent).order_by(OutboxEvent.id)).all()
    assert notices[-1].payload["message"]["kind"] == "sso_team_exclusion_lifted"
    assert notices[-1].payload["message"]["recipients"] == [alba.id]
    assert audits(sqlite_session, acme.id, "sso_team_exclusion_lifted")
    sign_in(test_client, fake_idp, conn.slug)
    assert members_of(sqlite_session, platform.id) == [ana]


def test_the_access_review_lists_exclusions_and_manual_exceptions_and_ends_them(
    sqlite_session, test_client, fake_idp
):
    acme, _conn, platform, [(_alba, alba_h), (_, beto_h)] = _exclusion_world(
        sqlite_session
    )
    ana, _ = member(sqlite_session, acme.id, "Viewer")
    bruno, _ = member(sqlite_session, acme.id, "Viewer")
    hand = team(sqlite_session, acme.id, source="owner", members=(bruno.id,))
    preview = test_client.post(
        teams_url(acme.id, f"/{hand.id}/membership-source-preview"), headers=alba_h
    ).json()
    test_client.patch(
        teams_url(acme.id, f"/{hand.id}"),
        json={"membership_source": "sso", "preview_id": preview["id"], "confirm": True},
        headers=alba_h,
    )
    test_client.post(
        teams_url(acme.id, f"/{platform.id}/exclusions"),
        json={"user_id": ana.id, "reason": "Under investigation by security"},
        headers=alba_h,
    )
    listing = test_client.get(
        f"/v1/companies/{acme.id}/access-review", headers=beto_h
    ).json()
    sso = {i["kind"]: i for i in listing["sso"]}
    assert sso["exclusion"]["user_id"] == ana.id
    assert sso["exclusion"]["reason"] == "Under investigation by security"
    assert sso["exclusion"]["age_days"] == 0 and sso["exclusion"]["created_by"]
    assert sso["manual_exception"]["user_id"] == bruno.id
    # Every listed item needs a decision, these included.
    partial = [
        {"kind": i["kind"], "id": i["id"], "decision": "confirm"}
        for i in listing["members"] + listing["tokens"]
    ]
    missing = test_client.post(
        f"/v1/companies/{acme.id}/access-review",
        json={"decisions": partial},
        headers=beto_h,
    )
    assert missing.status_code == 422
    decisions = partial + [
        {"kind": i["kind"], "id": i["id"], "decision": "remove"} for i in listing["sso"]
    ]
    done = test_client.post(
        f"/v1/companies/{acme.id}/access-review",
        json={"decisions": decisions},
        headers=beto_h,
    )
    assert done.status_code == 201, done.text
    sqlite_session.expire_all()
    assert sqlite_session.exec(select(SsoTeamExclusion)).one().lifted_at is not None
    assert members_of(sqlite_session, hand.id) == []
    row = audits(sqlite_session, acme.id, "access_review_completed")[-1]
    assert row.result["exclusions_lifted"] == [sso["exclusion"]["id"]]
    assert row.result["manual_exceptions_ended"] == [sso["manual_exception"]["id"]]
    after = test_client.get(f"/v1/companies/{acme.id}/access-review", headers=beto_h)
    assert after.json()["sso"] == []
