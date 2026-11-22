"""The group mapping at an SSO sign-in, end to end against the fake provider
(issue #703; ``docs/archive/design/SSO_GROUP_MAPPING.md``, R1–R4, R7–R14, R22): a
sign-in adds and removes, the periods carry ``sso-mapping:<id>`` and the
observation id, the audit row holds the matched values and never an unmatched
one, a failed audit write rolls back the memberships and periods with the
sign-in, no HTTP request leaves for an overage token or any sign-in, linking
in a session applies nothing, auto-join then mapping happen in one sign-in."""

from __future__ import annotations

import json
import time
from urllib.parse import parse_qs, urlsplit

from models import CompanyMember, SsoGroupObservation, TeamMember
from services.platform import locks, sso_group_sync
from services.platform.agent_service import AgentService
from sqlmodel import select
from sso_fake_idp import ISSUER, callback, company, connect, member, sign_in, start
from sso_group_world import (
    GROUP,
    OTHER_GROUP,
    UNMAPPED,
    audits,
    groups_on,
    members_of,
    observations,
    pair,
    periods,
    team,
)


def _world(session, **conn_overrides):
    acme = company(session)
    conn = groups_on(session, connect(session, acme.id, **conn_overrides))
    platform = team(session, acme.id)
    pair(session, conn, GROUP, platform)
    return acme, conn, platform


def _user_id(session, company_id):
    session.expire_all()
    return session.exec(
        select(CompanyMember.user_id).where(CompanyMember.company_id == company_id)
    ).one()


def test_auto_join_then_mapping_in_one_sign_in_and_a_later_one_removes(
    sqlite_session, test_client, fake_idp
):
    acme, conn, platform = _world(sqlite_session)
    fake_idp.claims = {"groups": [GROUP, UNMAPPED]}
    assert "code" in sign_in(test_client, fake_idp, conn.slug)
    ana = _user_id(sqlite_session, acme.id)
    assert members_of(sqlite_session, platform.id) == [ana]
    (first,) = observations(sqlite_session, conn.id)
    assert (first.state, first.applied, first.value_count) == ("present", True, 2)
    assert first.matched_values == [GROUP] and first.values_sha256
    (period,) = periods(sqlite_session, platform.id, ana)
    assert period.started_by == f"sso-mapping:{conn.id}"
    assert period.started_observation_id == first.id
    (row,) = audits(sqlite_session, acme.id, "sso_group_sync")
    assert row.agent_id == f"sso-mapping:{conn.id}"
    assert row.details["matched_values"] == [GROUP]
    assert row.details["observation_id"] == first.id
    assert row.details["issuer"] == ISSUER and row.details["value_count"] == 2
    assert row.result["changes"][0]["action"] == "added"
    assert row.decision_signature and row.chain_hash
    # Data minimisation (G14): the unmatched value is stored nowhere.
    stored = json.dumps([row.details, row.result, first.matched_values, first.skipped])
    assert UNMAPPED not in stored

    fake_idp.claims = {"groups": []}
    assert "code" in sign_in(test_client, fake_idp, conn.slug)
    assert members_of(sqlite_session, platform.id) == []
    second = observations(sqlite_session, conn.id)[-1]
    assert second.state == "empty" and second.applied
    (period,) = periods(sqlite_session, platform.id, ana)
    assert period.ended_at is not None
    assert period.ended_by == f"sso-mapping:{conn.id}"
    assert period.ended_observation_id == second.id
    removed = audits(sqlite_session, acme.id, "sso_group_sync")[-1]
    assert removed.result["changes"][0]["because"] == "claim_empty"


def test_an_absent_claim_changes_nothing_and_writes_no_audit_row(
    sqlite_session, test_client, fake_idp
):
    acme, conn, platform = _world(sqlite_session)
    fake_idp.claims = {"groups": [GROUP]}
    sign_in(test_client, fake_idp, conn.slug)
    ana = _user_id(sqlite_session, acme.id)
    fake_idp.claims = {}
    assert "code" in sign_in(test_client, fake_idp, conn.slug)
    assert members_of(sqlite_session, platform.id) == [ana]
    assert observations(sqlite_session, conn.id)[-1].state == "absent"
    assert len(audits(sqlite_session, acme.id, "sso_group_sync")) == 1


def test_an_overage_token_changes_nothing_and_nothing_is_fetched(
    sqlite_session, test_client, fake_idp
):
    acme, conn, platform = _world(sqlite_session)
    fake_idp.claims = {"groups": [GROUP]}
    sign_in(test_client, fake_idp, conn.slug)
    ana = _user_id(sqlite_session, acme.id)
    fake_idp.requests.clear()
    fake_idp.claims = {
        "groups": None,
        "_claim_names": {"groups": "src1"},
        "_claim_sources": {
            "src1": {"endpoint": "https://graph.windows.net/t/users/u/getMemberObjects"}
        },
    }
    assert "code" in sign_in(test_client, fake_idp, conn.slug)
    assert members_of(sqlite_session, platform.id) == [ana]
    overage = observations(sqlite_session, conn.id)[-1]
    assert (overage.state, overage.applied) == ("overage", False)
    assert overage.overage_host == "graph.windows.net"
    row = audits(sqlite_session, acme.id, "sso_group_sync")[-1]
    assert row.details["claim_state"] == "overage"
    assert row.details["overage_host"] == "graph.windows.net"
    # Only the provider's own endpoints were asked; never the overage source.
    assert fake_idp.requests and all(
        urlsplit(url).hostname == urlsplit(ISSUER).hostname for url in fake_idp.requests
    )


def test_a_stale_token_is_recorded_and_changes_nothing(
    sqlite_session, test_client, fake_idp
):
    acme, conn, platform = _world(sqlite_session)
    fake_idp.claims = {"groups": [GROUP]}
    sign_in(test_client, fake_idp, conn.slug)
    ana = _user_id(sqlite_session, acme.id)
    old = int(time.time()) - 120
    fake_idp.claims = {"groups": [], "iat": old, "exp": old + 600}
    assert "code" in sign_in(test_client, fake_idp, conn.slug)
    assert members_of(sqlite_session, platform.id) == [ana]
    stale = observations(sqlite_session, conn.id)[-1]
    assert not stale.applied
    assert stale.skipped == [{"team_id": None, "reason": "stale_token"}]
    assert audits(sqlite_session, acme.id, "sso_group_sync")[-1].result["skipped"] == [
        {"team_id": None, "reason": "stale_token"}
    ]


def test_the_apply_runs_under_the_lock_of_the_company_and_person(
    sqlite_session, test_client, fake_idp, monkeypatch
):
    acme, conn, _ = _world(sqlite_session)
    taken = []
    monkeypatch.setattr(
        locks,
        "in_transaction",
        lambda session, name, ident: taken.append((name, ident)),
    )
    fake_idp.claims = {"groups": [GROUP]}
    sign_in(test_client, fake_idp, conn.slug)
    ana = _user_id(sqlite_session, acme.id)
    assert taken == [("sso-groups", f"{acme.id}:{ana}")]


def test_a_failed_audit_write_rolls_back_the_memberships_with_the_sign_in(
    sqlite_session, test_client, fake_idp, monkeypatch
):
    acme, conn, platform = _world(sqlite_session)
    real = AgentService.audit_interaction

    def failing(self, *args, **kwargs):
        if kwargs.get("action") == "sso_sign_in":
            raise RuntimeError("the audit store is down")
        return real(self, *args, **kwargs)

    monkeypatch.setattr(AgentService, "audit_interaction", failing)
    fake_idp.claims = {"groups": [GROUP]}
    params = start(test_client, fake_idp, conn.slug)
    response = test_client.get(
        "/v1/auth/sso/callback",
        params={"state": params["state"], "code": "c"},
        follow_redirects=False,
    )
    assert response.status_code == 500
    sqlite_session.rollback()  # what closing the request's session does
    assert members_of(sqlite_session, platform.id) == []
    assert observations(sqlite_session, conn.id) == []
    assert sqlite_session.exec(select(TeamMember)).all() == []
    assert audits(sqlite_session, acme.id, "sso_group_sync") == []


def test_with_the_mapping_off_a_sign_in_records_nothing_for_groups(
    sqlite_session, test_client, fake_idp
):
    _acme, conn, platform = _world(sqlite_session)
    conn.groups_enabled = False
    sqlite_session.add(conn)
    sqlite_session.commit()
    fake_idp.claims = {"groups": [GROUP]}
    assert "code" in sign_in(test_client, fake_idp, conn.slug)
    assert members_of(sqlite_session, platform.id) == []
    assert observations(sqlite_session, conn.id) == []


def test_only_the_mapped_sso_teams_of_the_company_change(
    sqlite_session, test_client, fake_idp
):
    acme, conn, platform = _world(sqlite_session)
    fake_idp.claims = {"groups": [GROUP]}
    sign_in(test_client, fake_idp, conn.slug)
    ana = _user_id(sqlite_session, acme.id)
    by_hand = team(sqlite_session, acme.id, source="owner", members=(ana,))
    leads = team(sqlite_session, acme.id, role="Manager")
    pair(sqlite_session, conn, OTHER_GROUP, leads)  # above the Viewer cap
    fake_idp.claims = {"groups": [OTHER_GROUP]}
    sign_in(test_client, fake_idp, conn.slug)
    assert members_of(sqlite_session, by_hand.id) == [ana]  # never touched
    assert members_of(sqlite_session, leads.id) == []  # role_above_cap
    assert members_of(sqlite_session, platform.id) == []  # not in the claim
    last = observations(sqlite_session, conn.id)[-1]
    assert {"team_id": leads.id, "reason": "role_above_cap"} in last.skipped


def test_the_observation_table_is_append_only(sqlite_session, test_client, fake_idp):
    import pytest

    _acme, conn, _ = _world(sqlite_session)
    fake_idp.claims = {"groups": [GROUP]}
    sign_in(test_client, fake_idp, conn.slug)
    row = sqlite_session.exec(select(SsoGroupObservation)).first()
    row.state = "absent"
    sqlite_session.add(row)
    with pytest.raises(sso_group_sync.ObservationRefused):
        sqlite_session.flush()
    sqlite_session.rollback()
    row = sqlite_session.exec(select(SsoGroupObservation)).first()
    sqlite_session.delete(row)
    with pytest.raises(sso_group_sync.ObservationRefused):
        sqlite_session.flush()
    sqlite_session.rollback()


def test_linking_an_identity_inside_a_session_applies_no_group(
    sqlite_session, test_client, fake_idp
):
    acme = company(sqlite_session)
    conn = groups_on(sqlite_session, connect(sqlite_session, acme.id, auto_join=False))
    platform = team(sqlite_session, acme.id)
    pair(sqlite_session, conn, GROUP, platform)
    test_client.base_url = "https://testserver"
    test_client.cookies.clear()
    ana, _ = member(sqlite_session, acme.id, "Viewer", email="ana@acme.test")
    login = test_client.post(
        "/v1/auth/login", json={"email": ana.email, "password": "correct-password"}
    )
    assert login.status_code == 200, login.text
    csrf = {"X-Fabrique-CSRF": test_client.cookies.get("fabrique_csrf", path="/")}
    started = test_client.post(
        "/v1/auth/sso/link/start", json={"company_id": acme.id}, headers=csrf
    )
    assert started.status_code == 200, started.text
    query = urlsplit(started.json()["authorization_url"]).query
    params = {k: v[0] for k, v in parse_qs(query).items()}
    fake_idp.nonce = params["nonce"]
    fake_idp.claims = {"groups": [GROUP], "sub": "subject-linked"}
    landed = test_client.get(
        "/v1/auth/sso/callback",
        params={"state": params["state"], "code": "c"},
        follow_redirects=False,
    )
    assert "sso_link=linked" in landed.headers["location"], landed.headers
    assert members_of(sqlite_session, platform.id) == []
    assert observations(sqlite_session, conn.id) == []


def test_the_sign_in_callback_helper_still_lands_on_the_login_page(
    sqlite_session, test_client, fake_idp
):
    """A mapping that skips everything never stops a valid sign-in (G16)."""
    _, conn, _ = _world(sqlite_session)
    fake_idp.claims = {"groups": "not-a-list"}
    params = start(test_client, fake_idp, conn.slug)
    assert "code" in callback(test_client, state=params["state"], code="c")
    assert observations(sqlite_session, conn.id)[-1].state == "malformed"
