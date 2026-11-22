"""The Owner's configuration of the SSO group mapping and of SSO-managed teams
(issue #703; ``docs/archive/design/SSO_GROUP_MAPPING.md``, R15–R22, R26): the Owner
only (an API token refused whatever its role), Google refused without the
Directory sync, Entra GUIDs for ``groups`` and exact ``roles``, raising a
mapped team's role refused, the conversion preview, its confirmation and
``preview_stale``, manual exceptions created and ended, the member routes
refused on an SSO-managed team, and removing a pair removes only what it
alone justified."""

from __future__ import annotations

from models import (
    SsoGroupMapping,
    SsoManualException,
    SsoTeamExclusion,
    TeamMember,
)
from services.platform import api_tokens, teams
from sqlmodel import select
from sso_fake_idp import company, connect, member
from sso_group_world import (
    GROUP,
    OTHER_GROUP,
    audits,
    groups_on,
    mappings_url,
    members_of,
    observe,
    pair,
    periods,
    team,
    teams_url,
)

GUID = "6F1C5D3A-0B2E-4C7D-9A8B-1E2F3A4B5C6D"


def _put(client, company_id, headers, mappings=(), **settings):
    body = {
        "groups_enabled": True,
        "groups_claim": "groups",
        "group_role_cap": "Viewer",
        "mappings": [{"group_value": v, "team_id": t} for v, t in mappings],
        **settings,
    }
    return client.put(mappings_url(company_id), json=body, headers=headers)


def _acme(session, **overrides):
    acme = company(session)
    conn = connect(session, acme.id, **overrides)
    owner, headers = member(session, acme.id, "Owner")
    return acme, conn, owner, headers


def test_only_a_signed_in_human_owner_reaches_the_mapping(
    sqlite_session, test_client, fake_idp
):
    acme, conn, _, _ = _acme(sqlite_session)
    platform = team(sqlite_session, acme.id)
    other = company(sqlite_session)
    _, manager = member(sqlite_session, acme.id, "Manager")
    _, viewer = member(sqlite_session, acme.id, "Viewer")
    _, outsider = member(sqlite_session, other.id, "Owner")
    calls = [
        ("get", mappings_url(acme.id), None),
        ("put", mappings_url(acme.id), {"mappings": []}),
        ("post", mappings_url(acme.id, "/approvals/1/approve"), None),
        ("post", teams_url(acme.id, f"/{platform.id}/membership-source-preview"), None),
        (
            "post",
            teams_url(acme.id, f"/{platform.id}/exclusions"),
            {"user_id": 1, "reason": "a reason long enough"},
        ),
    ]
    for method, url, body in calls:
        for headers in (manager, viewer, outsider):
            kwargs = {"headers": headers}
            if body is not None:
                kwargs["json"] = body
            assert getattr(test_client, method)(url, **kwargs).status_code == 403
        assert test_client.get(mappings_url(acme.id)).status_code == 401
    _, raw = api_tokens.create_token(sqlite_session, acme.id, "ci", role="Manager")
    token = sqlite_session.exec(select(api_tokens.ApiToken)).all()[-1]
    token.role = "Owner"  # even a token carrying the Owner role is refused
    sqlite_session.add(token)
    sqlite_session.commit()
    bearer = {"Authorization": f"Bearer {raw}"}
    assert test_client.get(mappings_url(acme.id), headers=bearer).status_code == 403
    assert _put(test_client, acme.id, bearer).status_code == 403
    assert conn.groups_enabled is False


def test_an_owner_saves_the_mapping_and_every_change_is_audited(
    sqlite_session, test_client, fake_idp
):
    acme, _conn, owner, headers = _acme(sqlite_session)
    platform = team(sqlite_session, acme.id)
    saved = _put(test_client, acme.id, headers, [(GROUP, platform.id)])
    assert saved.status_code == 200, saved.text
    body = saved.json()
    assert body["groups_enabled"] and body["groups_claim"] == "groups"
    assert [(m["group_value"], m["team_id"]) for m in body["mappings"]] == [
        (GROUP, platform.id)
    ]
    assert body["sso_teams"] == [
        {"id": platform.id, "name": platform.name, "role": "Viewer"}
    ]
    (row,) = audits(sqlite_session, acme.id, "sso_group_mapping_updated")
    assert row.agent_id == f"human:{owner.email}"
    assert row.result["pairs_added"] == [{"group_value": GROUP, "team_id": platform.id}]
    assert set(row.result["changed"]) == {"groups_enabled"}
    # Adding a pair changes no membership until a sign-in (R21).
    assert members_of(sqlite_session, platform.id) == []
    # Saving the same thing again writes nothing.
    assert (
        _put(test_client, acme.id, headers, [(GROUP, platform.id)]).status_code == 200
    )
    assert len(audits(sqlite_session, acme.id, "sso_group_mapping_updated")) == 1
    got = test_client.get(mappings_url(acme.id), headers=headers)
    assert got.status_code == 200 and got.headers["cache-control"] == "no-store"
    assert got.json()["last_overage"] is None


def test_a_pair_is_refused_for_another_company_an_owner_team_or_above_the_cap(
    sqlite_session, test_client, fake_idp
):
    acme, conn, _, headers = _acme(sqlite_session)
    other = company(sqlite_session)
    theirs = team(sqlite_session, other.id)
    by_hand = team(sqlite_session, acme.id, source="owner")
    leads = team(sqlite_session, acme.id, role="Manager")
    for target in (theirs, by_hand, leads):
        response = _put(test_client, acme.id, headers, [(GROUP, target.id)])
        assert response.status_code == 422, response.text
    assert sqlite_session.exec(select(SsoGroupMapping)).all() == []
    assert (
        _put(test_client, acme.id, headers, group_role_cap="Owner").status_code == 422
    )
    assert _put(test_client, acme.id, headers, groups_claim="a b").status_code == 422
    sqlite_session.refresh(conn)
    assert conn.groups_enabled is False


def test_google_is_refused_without_the_directory_sync(
    sqlite_session, test_client, fake_idp
):
    acme, _conn, _, headers = _acme(sqlite_session, preset="google")
    platform = team(sqlite_session, acme.id)
    response = _put(test_client, acme.id, headers, [(GROUP, platform.id)])
    assert response.status_code == 422
    assert "groups_unavailable" in response.json()["detail"]
    assert (
        test_client.get(mappings_url(acme.id), headers=headers).json()[
            "groups_available"
        ]
        is False
    )


def test_entra_groups_are_guids_stored_lower_cased_and_app_roles_exact(
    sqlite_session, test_client, fake_idp
):
    acme, _conn, _, headers = _acme(sqlite_session, preset="entra")
    platform = team(sqlite_session, acme.id)
    assert (
        test_client.get(mappings_url(acme.id), headers=headers).json()["proposed_claim"]
        == "roles"
    )
    refused = _put(test_client, acme.id, headers, [("All Engineers", platform.id)])
    assert refused.status_code == 422 and "GUID" in refused.json()["detail"]
    saved = _put(test_client, acme.id, headers, [(GUID, platform.id)])
    assert saved.json()["mappings"][0]["group_value"] == GUID.lower()
    assert saved.json()["compares_lower"] is True
    roles = _put(
        test_client,
        acme.id,
        headers,
        [("Ops.Admin", platform.id)],
        groups_claim="roles",
    )
    assert roles.status_code == 200
    assert roles.json()["mappings"][0]["group_value"] == "Ops.Admin"
    assert roles.json()["compares_lower"] is False


def test_a_mapped_teams_role_is_never_raised(sqlite_session, test_client, fake_idp):
    acme, conn, _, headers = _acme(sqlite_session)
    platform = team(sqlite_session, acme.id)
    pair(sqlite_session, conn, GROUP, platform)
    raised = test_client.patch(
        teams_url(acme.id, f"/{platform.id}"), json={"role": "Manager"}, headers=headers
    )
    assert raised.status_code == 409 and "team_mapped" in raised.json()["detail"]
    sqlite_session.refresh(platform)
    assert platform.role == "Viewer"
    unmapped = team(sqlite_session, acme.id)
    assert (
        test_client.patch(
            teams_url(acme.id, f"/{unmapped.id}"),
            json={"role": "Manager"},
            headers=headers,
        ).status_code
        == 200
    )


def test_converting_a_team_with_members_needs_a_fresh_confirmed_preview(
    sqlite_session, test_client, fake_idp
):
    acme, _conn, owner, headers = _acme(sqlite_session)
    ana, _ = member(sqlite_session, acme.id, "Viewer")
    bruno, _ = member(sqlite_session, acme.id, "Viewer")
    hand = team(sqlite_session, acme.id, source="owner", members=(ana.id,))
    url = teams_url(acme.id, f"/{hand.id}")
    blind = test_client.patch(url, json={"membership_source": "sso"}, headers=headers)
    assert blind.status_code == 409 and "preview_stale" in blind.json()["detail"]
    preview = test_client.post(f"{url}/membership-source-preview", headers=headers)
    assert preview.status_code == 200, preview.text
    assert preview.json()["members"] == [ana.id]
    unconfirmed = test_client.patch(
        url,
        json={"membership_source": "sso", "preview_id": preview.json()["id"]},
        headers=headers,
    )
    assert unconfirmed.status_code == 409
    # A member added after the preview makes it stale (G22).
    test_client.put(f"{url}/members/{bruno.id}", headers=headers)
    stale = test_client.patch(
        url,
        json={
            "membership_source": "sso",
            "preview_id": preview.json()["id"],
            "confirm": True,
        },
        headers=headers,
    )
    assert stale.status_code == 409 and "preview_stale" in stale.json()["detail"]
    fresh = test_client.post(f"{url}/membership-source-preview", headers=headers).json()
    done = test_client.patch(
        url,
        json={"membership_source": "sso", "preview_id": fresh["id"], "confirm": True},
        headers=headers,
    )
    assert done.status_code == 200, done.text
    assert done.json()["membership_source"] == "sso"
    kept = sqlite_session.exec(
        select(SsoManualException).where(SsoManualException.team_id == hand.id)
    ).all()
    assert sorted(k.user_id for k in kept) == sorted([ana.id, bruno.id])
    assert all(k.preview_id == fresh["id"] and k.created_by == owner.id for k in kept)
    row = audits(sqlite_session, acme.id, "team_updated")[-1]
    assert row.result["changed"]["membership_source"] == {"from": "owner", "to": "sso"}
    assert row.result["preview_id"] == fresh["id"]
    assert sorted(row.result["manual_exceptions"]) == sorted(k.id for k in kept)
    # The preview is single use.
    again = test_client.patch(
        url,
        json={"membership_source": "owner"},
        headers=headers,
    )
    assert again.status_code == 200
    reused = test_client.patch(
        url,
        json={"membership_source": "sso", "preview_id": fresh["id"], "confirm": True},
        headers=headers,
    )
    assert reused.status_code == 409


def test_an_empty_team_converts_without_a_preview(
    sqlite_session, test_client, fake_idp
):
    acme, _, _, headers = _acme(sqlite_session)
    empty = team(sqlite_session, acme.id, source="owner")
    done = test_client.patch(
        teams_url(acme.id, f"/{empty.id}"),
        json={"membership_source": "sso"},
        headers=headers,
    )
    assert done.status_code == 200 and done.json()["membership_source"] == "sso"


def test_the_member_routes_on_an_sso_team_end_only_a_manual_exception(
    sqlite_session, test_client, fake_idp
):
    acme, _conn, owner, headers = _acme(sqlite_session)
    ana, _ = member(sqlite_session, acme.id, "Viewer")
    bruno, _ = member(sqlite_session, acme.id, "Viewer")
    platform = team(sqlite_session, acme.id, members=(ana.id, bruno.id))
    sqlite_session.add(
        SsoManualException(
            company_id=acme.id, team_id=platform.id, user_id=ana.id, created_by=owner.id
        )
    )
    sqlite_session.commit()
    url = teams_url(acme.id, f"/{platform.id}/members")
    carol, _ = member(sqlite_session, acme.id, "Viewer")
    added = test_client.put(f"{url}/{carol.id}", headers=headers)
    assert added.status_code == 409 and "team_managed_by_sso" in added.json()["detail"]
    refused = test_client.delete(f"{url}/{bruno.id}", headers=headers)
    assert refused.status_code == 409
    assert "use_break_glass" in refused.json()["detail"]
    assert "/exclusions" in refused.json()["detail"]
    ended = test_client.delete(f"{url}/{ana.id}", headers=headers)
    assert ended.status_code == 200, ended.text
    assert members_of(sqlite_session, platform.id) == [bruno.id]
    exception = sqlite_session.exec(select(SsoManualException)).one()
    assert exception.ended_at is not None and exception.ended_by == owner.id
    (row,) = audits(sqlite_session, acme.id, "sso_manual_exception_ended")
    assert row.result["user_id"] == ana.id


def test_removing_a_pair_removes_only_what_it_alone_justified(
    sqlite_session, test_client, fake_idp
):
    acme, conn, owner, headers = _acme(sqlite_session)
    groups_on(sqlite_session, conn)
    platform = team(sqlite_session, acme.id)
    ana, _ = member(sqlite_session, acme.id, "Viewer")  # matched GROUP only
    bruno, _ = member(sqlite_session, acme.id, "Viewer")  # matched both
    carol, _ = member(sqlite_session, acme.id, "Viewer")  # a manual exception
    for person in (ana, bruno, carol):
        sqlite_session.add(
            TeamMember(company_id=acme.id, team_id=platform.id, user_id=person.id)
        )
        teams.open_membership(sqlite_session, platform, person.id, by="test")
    sqlite_session.add(
        SsoManualException(
            company_id=acme.id,
            team_id=platform.id,
            user_id=carol.id,
            created_by=owner.id,
        )
    )
    sqlite_session.commit()
    pair(sqlite_session, conn, GROUP, platform)
    pair(sqlite_session, conn, OTHER_GROUP, platform)
    observe(sqlite_session, conn, ana.id, [GROUP])
    observe(sqlite_session, conn, bruno.id, [GROUP, OTHER_GROUP])
    observe(sqlite_session, conn, carol.id, [GROUP])
    saved = _put(test_client, acme.id, headers, [(OTHER_GROUP, platform.id)])
    assert saved.status_code == 200, saved.text
    assert members_of(sqlite_session, platform.id) == sorted([bruno.id, carol.id])
    (period,) = periods(sqlite_session, platform.id, ana.id)
    assert period.ended_by == f"sso-mapping:{conn.id}"
    assert period.ended_at is not None and period.ended_observation_id is None
    assert periods(sqlite_session, platform.id, bruno.id)[0].ended_at is None
    row = audits(sqlite_session, acme.id, "sso_group_mapping_updated")[-1]
    assert row.result["memberships_removed"] == [
        {"team_id": platform.id, "user_id": ana.id}
    ]
    assert row.result["pairs_removed"] == [
        {"group_value": GROUP, "team_id": platform.id}
    ]


def test_turning_the_mapping_off_changes_no_membership(
    sqlite_session, test_client, fake_idp
):
    acme, conn, _, headers = _acme(sqlite_session)
    groups_on(sqlite_session, conn)
    ana, _ = member(sqlite_session, acme.id, "Viewer")
    platform = team(sqlite_session, acme.id, members=(ana.id,))
    pair(sqlite_session, conn, GROUP, platform)
    off = _put(
        test_client, acme.id, headers, [(GROUP, platform.id)], groups_enabled=False
    )
    assert off.status_code == 200 and off.json()["groups_enabled"] is False
    assert members_of(sqlite_session, platform.id) == [ana.id]


def test_switching_back_to_owner_keeps_members_and_ends_the_holds(
    sqlite_session, test_client, fake_idp
):
    acme, conn, owner, headers = _acme(sqlite_session)
    ana, _ = member(sqlite_session, acme.id, "Viewer")
    bruno, _ = member(sqlite_session, acme.id, "Viewer")
    platform = team(sqlite_session, acme.id, members=(ana.id,))
    mapped = pair(sqlite_session, conn, GROUP, platform)
    sqlite_session.add(
        SsoManualException(
            company_id=acme.id, team_id=platform.id, user_id=ana.id, created_by=owner.id
        )
    )
    sqlite_session.add(
        SsoTeamExclusion(
            company_id=acme.id,
            team_id=platform.id,
            user_id=bruno.id,
            created_by=owner.id,
            reason="settling a dispute",
        )
    )
    sqlite_session.commit()
    url = teams_url(acme.id, f"/{platform.id}")
    refused = test_client.patch(
        url, json={"membership_source": "owner"}, headers=headers
    )
    assert refused.status_code == 409
    sqlite_session.delete(mapped)
    sqlite_session.commit()
    done = test_client.patch(url, json={"membership_source": "owner"}, headers=headers)
    assert done.status_code == 200, done.text
    assert members_of(sqlite_session, platform.id) == [ana.id]
    sqlite_session.expire_all()
    assert sqlite_session.exec(select(SsoManualException)).one().ended_at is not None
    exclusion = sqlite_session.exec(select(SsoTeamExclusion)).one()
    assert exclusion.lift_reason == "team switched to owner"
    assert exclusion.lifted_by == owner.id


def test_leaving_the_company_ends_exclusions_and_manual_exceptions(
    sqlite_session, test_client, fake_idp
):
    acme, _conn, owner, headers = _acme(sqlite_session)
    ana, _ = member(sqlite_session, acme.id, "Viewer")
    platform = team(sqlite_session, acme.id, members=(ana.id,))
    other = team(sqlite_session, acme.id)
    sqlite_session.add(
        SsoManualException(
            company_id=acme.id, team_id=platform.id, user_id=ana.id, created_by=owner.id
        )
    )
    sqlite_session.add(
        SsoTeamExclusion(
            company_id=acme.id,
            team_id=other.id,
            user_id=ana.id,
            created_by=owner.id,
            reason="settling a dispute",
        )
    )
    sqlite_session.commit()
    gone = test_client.delete(
        f"/v1/company/{acme.id}/members/{ana.id}", headers=headers
    )
    assert gone.status_code == 204, gone.text
    sqlite_session.expire_all()
    assert members_of(sqlite_session, platform.id) == []
    assert sqlite_session.exec(select(SsoManualException)).one().ended_by == owner.id
    assert sqlite_session.exec(select(SsoTeamExclusion)).one().lift_reason == (
        "left the company"
    )


def test_deleting_the_connection_or_the_team_drops_its_pairs(
    sqlite_session, test_client, fake_idp
):
    acme, conn, _, headers = _acme(sqlite_session)
    platform = team(sqlite_session, acme.id)
    other = team(sqlite_session, acme.id)
    pair(sqlite_session, conn, GROUP, platform)
    pair(sqlite_session, conn, GROUP, other)
    assert (
        test_client.delete(
            teams_url(acme.id, f"/{other.id}"), headers=headers
        ).status_code
        == 204
    )
    assert [m.team_id for m in sqlite_session.exec(select(SsoGroupMapping))] == [
        platform.id
    ]
    assert (
        test_client.delete(f"/v1/companies/{acme.id}/sso", headers=headers).status_code
        == 204
    )
    assert sqlite_session.exec(select(SsoGroupMapping)).all() == []
