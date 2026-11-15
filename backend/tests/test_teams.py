"""Teams inside a company and the effective role (issue #512, phase 5;
``docs/design/TEAMS_AND_USAGE.md``, T1–T6).

A member's effective role is the higher of their own role and their teams'
roles (Viewer or Manager, never Owner); ``auth_policy.require_company_role``
checks it. Without teams nothing changes: every own role passes and fails the
same minimums as before. A team grants Manager routes while the person is in
it and stops when they leave; it never grants Owner, never makes anyone a
member and never reaches another company. Only a signed-in human Owner reads
or changes teams, and every change is audited."""

from __future__ import annotations

import secrets

import auth_tokens
import pytest
from auth_policy import require_company_role
from config import settings
from fastapi import HTTPException
from models import AuditLog, Company, CompanyMember, Team, TeamMember, User
from services.management import brief_mail
from services.platform import api_tokens, teams
from sqlmodel import select

RANK = {"Viewer": 1, "Manager": 2, "Owner": 3}


@pytest.fixture(autouse=True)
def auth_on(monkeypatch):
    monkeypatch.setattr(settings, "AUTH_REQUIRED", True)
    # A throwaway signing key, generated per test: no literal secret.
    key = secrets.token_urlsafe(48)
    monkeypatch.setattr(auth_tokens, "_configured_key", lambda: key)


def _company(session) -> Company:
    row = Company(company_name=f"Teams {secrets.token_hex(3)}")
    session.add(row)
    session.commit()
    return row


def _person(session, company_id: int | None, role: str | None):
    user = User(
        email=f"{(role or 'none').lower()}-{secrets.token_hex(4)}@example.test",
        full_name=f"{role or 'Nobody'} {secrets.token_hex(2)}",
        password_hash="unused",
    )
    session.add(user)
    session.commit()
    if company_id is not None and role is not None:
        session.add(
            CompanyMember(company_id=company_id, user_id=user.id, human_role=role)
        )
        session.commit()
    token = auth_tokens.create_access_token(user_id=user.id, email=user.email)
    return user, {"Authorization": f"Bearer {token}"}


def _team(session, company_id: int, role: str, *members: int) -> Team:
    team = Team(company_id=company_id, name=f"t-{secrets.token_hex(3)}", role=role)
    session.add(team)
    session.commit()
    for user_id in members:
        session.add(TeamMember(company_id=company_id, team_id=team.id, user_id=user_id))
    session.commit()
    return team


def _allowed(session, user, company_id, minimum) -> bool:
    try:
        require_company_role(session, user, company_id, minimum)
    except HTTPException as exc:
        assert exc.status_code == 403
        return False
    return True


# -- The rule (pure) ---------------------------------------------------------


def test_the_effective_role_is_the_highest_and_a_team_never_grants_owner():
    assert teams.effective_role("Viewer", []) == "Viewer"
    assert teams.effective_role("Viewer", ["Manager"]) == "Manager"
    assert teams.effective_role("Manager", ["Viewer"]) == "Manager"
    assert teams.effective_role("Owner", ["Viewer", "Manager"]) == "Owner"
    assert teams.effective_role("Viewer", ["Owner"]) == "Viewer"
    assert teams.effective_role("Viewer", ["Admin", None]) == "Viewer"
    assert teams.effective_role(None, ["Manager"]) is None
    assert teams.effective_role("Stranger", ["Manager"]) is None


# -- No team: no route gains or loses access ---------------------------------


@pytest.mark.parametrize("own", ["Viewer", "Manager", "Owner"])
@pytest.mark.parametrize("minimum", ["Viewer", "Manager", "Owner"])
def test_without_teams_every_role_passes_exactly_what_it_passed_before(
    sqlite_session, own, minimum
):
    acme = _company(sqlite_session)
    user, _ = _person(sqlite_session, acme.id, own)
    assert _allowed(sqlite_session, user, acme.id, minimum) == (
        RANK[own] >= RANK[minimum]
    )
    # A Viewer team adds nothing to anyone.
    _team(sqlite_session, acme.id, "Viewer", user.id)
    assert _allowed(sqlite_session, user, acme.id, minimum) == (
        RANK[own] >= RANK[minimum]
    )


def test_a_manager_team_grants_manager_routes_until_the_person_leaves_it(
    sqlite_session, test_client
):
    acme = _company(sqlite_session)
    viewer, headers = _person(sqlite_session, acme.id, "Viewer")
    usage = f"/v1/companies/{acme.id}/usage?since=2026-11-01&until=2026-11-07"
    assert test_client.get(usage, headers=headers).status_code == 403
    team = _team(sqlite_session, acme.id, "Manager", viewer.id)
    assert _allowed(sqlite_session, viewer, acme.id, "Manager")
    assert test_client.get(usage, headers=headers).status_code == 200
    # Owner routes stay closed: a team never grants Owner.
    assert not _allowed(sqlite_session, viewer, acme.id, "Owner")
    assert (
        test_client.get(f"/v1/companies/{acme.id}/teams", headers=headers).status_code
        == 403
    )
    me = test_client.get("/v1/auth/me", headers=headers).json()
    assert me["memberships"] == [
        {"company_id": acme.id, "human_role": "Viewer", "effective_role": "Manager"}
    ]
    member = sqlite_session.exec(
        select(TeamMember).where(TeamMember.team_id == team.id)
    ).one()
    sqlite_session.delete(member)
    sqlite_session.commit()
    assert not _allowed(sqlite_session, viewer, acme.id, "Manager")
    assert test_client.get(usage, headers=headers).status_code == 403


def test_a_team_never_makes_a_member_nor_reaches_another_company(sqlite_session):
    acme, other = _company(sqlite_session), _company(sqlite_session)
    outsider, _ = _person(sqlite_session, other.id, "Viewer")
    # A team row of acme for someone who is not acme's member: still not one.
    _team(sqlite_session, acme.id, "Manager", outsider.id)
    assert teams.member_role(sqlite_session, acme.id, outsider.id) is None
    assert not _allowed(sqlite_session, outsider, acme.id, "Viewer")
    # And acme's team does not raise them in their own company.
    assert teams.member_role(sqlite_session, other.id, outsider.id) == "Viewer"
    # A team row claiming Owner (a database edited by hand) grants nothing.
    viewer, _ = _person(sqlite_session, acme.id, "Viewer")
    _team(sqlite_session, acme.id, "Owner", viewer.id)
    assert teams.member_role(sqlite_session, acme.id, viewer.id) == "Viewer"


def test_an_api_token_is_capped_by_the_effective_role(sqlite_session, test_client):
    acme = _company(sqlite_session)
    viewer, headers = _person(sqlite_session, acme.id, "Viewer")
    body = {"company_id": acme.id, "name": "ci", "role": "Manager"}
    assert (
        test_client.post("/v1/auth/tokens", json=body, headers=headers).status_code
        == 403
    )
    _team(sqlite_session, acme.id, "Manager", viewer.id)
    made = test_client.post("/v1/auth/tokens", json=body, headers=headers)
    assert made.status_code == 200, made.text
    assert made.json()["role"] == "Manager"


def test_the_brief_goes_to_members_a_team_makes_managers(sqlite_session):
    acme = _company(sqlite_session)
    owner, _ = _person(sqlite_session, acme.id, "Owner")
    viewer, _ = _person(sqlite_session, acme.id, "Viewer")
    other_viewer, _ = _person(sqlite_session, acme.id, "Viewer")
    assert brief_mail.recipients(sqlite_session, acme.id) == [owner.email]
    _team(sqlite_session, acme.id, "Manager", viewer.id)
    assert brief_mail.recipients(sqlite_session, acme.id) == sorted(
        [owner.email, viewer.email]
    )
    assert other_viewer.email not in brief_mail.recipients(sqlite_session, acme.id)


# -- Managing teams ----------------------------------------------------------


def _url(company_id, suffix=""):
    return f"/v1/companies/{company_id}/teams{suffix}"


def test_an_owner_manages_teams_and_every_change_is_audited(
    sqlite_session, test_client
):
    acme = _company(sqlite_session)
    owner, headers = _person(sqlite_session, acme.id, "Owner")
    viewer, viewer_headers = _person(sqlite_session, acme.id, "Viewer")
    created = test_client.post(
        _url(acme.id),
        json={"name": "  Release   crew ", "description": "Ships", "role": "Manager"},
        headers=headers,
    )
    assert created.status_code == 201, created.text
    team = created.json()
    assert (team["name"], team["role"], team["members"]) == (
        "Release crew",
        "Manager",
        [],
    )
    assert (
        test_client.post(
            _url(acme.id), json={"name": "release CREW"}, headers=headers
        ).status_code
        == 409
    )
    added = test_client.put(
        _url(acme.id, f"/{team['id']}/members/{viewer.id}"), headers=headers
    )
    assert added.status_code == 200 and added.json()["members"] == [viewer.id]
    # Adding again changes nothing and writes no second audit row.
    test_client.put(
        _url(acme.id, f"/{team['id']}/members/{viewer.id}"), headers=headers
    )
    listed = test_client.get(_url(acme.id), headers=headers).json()
    row = next(m for m in listed["members"] if m["user_id"] == viewer.id)
    assert (row["human_role"], row["effective_role"], row["team_ids"]) == (
        "Viewer",
        "Manager",
        [team["id"]],
    )
    assert (
        test_client.get(
            f"/v1/companies/{acme.id}/usage", headers=viewer_headers
        ).status_code
        == 200
    )
    patched = test_client.patch(
        _url(acme.id, f"/{team['id']}"), json={"role": "Viewer"}, headers=headers
    )
    assert patched.status_code == 200 and patched.json()["role"] == "Viewer"
    assert (
        test_client.get(
            f"/v1/companies/{acme.id}/usage", headers=viewer_headers
        ).status_code
        == 403
    )
    removed = test_client.delete(
        _url(acme.id, f"/{team['id']}/members/{viewer.id}"), headers=headers
    )
    assert removed.status_code == 200 and removed.json()["members"] == []
    assert (
        test_client.delete(
            _url(acme.id, f"/{team['id']}/members/{viewer.id}"), headers=headers
        ).status_code
        == 404
    )
    assert (
        test_client.delete(_url(acme.id, f"/{team['id']}"), headers=headers).status_code
        == 204
    )
    assert sqlite_session.get(Team, team["id"]) is None
    audits = sqlite_session.exec(
        select(AuditLog)
        .where(AuditLog.company_id == acme.id, AuditLog.action.like("team_%"))
        .order_by(AuditLog.id)
    ).all()
    assert [a.action for a in audits] == [
        "team_created",
        "team_member_added",
        "team_updated",
        "team_member_removed",
        "team_deleted",
    ]
    assert all(a.agent_id == f"human:{owner.email}" for a in audits)
    assert audits[2].result["changed"] == {"role": {"from": "Manager", "to": "Viewer"}}


def test_only_a_signed_in_owner_reaches_teams(sqlite_session, test_client):
    acme, other = _company(sqlite_session), _company(sqlite_session)
    _, owner = _person(sqlite_session, acme.id, "Owner")
    manager, manager_headers = _person(sqlite_session, acme.id, "Manager")
    _, viewer = _person(sqlite_session, acme.id, "Viewer")
    _, outsider = _person(sqlite_session, other.id, "Owner")
    team = _team(sqlite_session, acme.id, "Manager")
    for headers in (manager_headers, viewer, outsider):
        assert test_client.get(_url(acme.id), headers=headers).status_code == 403
        assert (
            test_client.post(
                _url(acme.id), json={"name": "x"}, headers=headers
            ).status_code
            == 403
        )
        assert (
            test_client.put(
                _url(acme.id, f"/{team.id}/members/{manager.id}"), headers=headers
            ).status_code
            == 403
        )
    assert test_client.get(_url(acme.id)).status_code == 401
    _, raw = api_tokens.create_token(sqlite_session, acme.id, "ci", role="Manager")
    token = sqlite_session.exec(select(api_tokens.ApiToken)).all()[-1]
    token.role = "Owner"  # even a token carrying the Owner role is refused
    sqlite_session.add(token)
    sqlite_session.commit()
    bearer = {"Authorization": f"Bearer {raw}"}
    assert test_client.get(_url(acme.id), headers=bearer).status_code == 403
    # Another company's team is not found from here.
    foreign = _team(sqlite_session, other.id, "Manager")
    assert (
        test_client.patch(
            _url(acme.id, f"/{foreign.id}"), json={"name": "mine"}, headers=owner
        ).status_code
        == 404
    )


def test_the_team_fields_are_checked_and_only_members_join(sqlite_session, test_client):
    acme, other = _company(sqlite_session), _company(sqlite_session)
    _, owner = _person(sqlite_session, acme.id, "Owner")
    stranger, _ = _person(sqlite_session, other.id, "Viewer")
    for bad in ({"name": "x", "role": "Owner"}, {"name": "   "}, {"name": "x" * 81}):
        assert (
            test_client.post(_url(acme.id), json=bad, headers=owner).status_code == 422
        )
    team = test_client.post(_url(acme.id), json={"name": "Ops"}, headers=owner).json()
    assert team["role"] == "Viewer"
    assert (
        test_client.patch(
            _url(acme.id, f"/{team['id']}"), json={"role": "Owner"}, headers=owner
        ).status_code
        == 422
    )
    joined = test_client.put(
        _url(acme.id, f"/{team['id']}/members/{stranger.id}"), headers=owner
    )
    assert joined.status_code == 404
    assert (
        sqlite_session.exec(
            select(TeamMember).where(TeamMember.user_id == stranger.id)
        ).all()
        == []
    )
