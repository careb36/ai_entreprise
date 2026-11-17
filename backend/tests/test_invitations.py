"""Pending invitations to a company (WSTG-v42-IDNT-04; ``docs/contexts/platform.md``,
Auth and multi-tenancy).

``POST /v1/company/invite`` answers the same whether or not the address has
an account, and adds nobody: it records a pending invitation (the company,
the normalised address, the role, who invited, an expiry and a single-use
token kept only as its sha256). The invitee sees it once signed in and
accepts or declines it; only an acceptance makes them a member. An inviter
invites up to their own role, never with an API token, and every step is
audited.
"""

from __future__ import annotations

import hashlib
import secrets
from datetime import UTC, datetime, timedelta

import auth_tokens
import models
import pytest
from config import settings
from models import AuditLog, Company, CompanyMember, User
from services.platform import api_tokens
from sqlmodel import select

# Read from the package, so that the tests of the answer itself run (and fail)
# on a backend that has no such table yet.
CompanyInvitation = getattr(models, "CompanyInvitation", None)


@pytest.fixture(autouse=True)
def auth_on(monkeypatch, tmp_path):
    monkeypatch.setattr(settings, "AUTH_REQUIRED", True)
    monkeypatch.setenv("AUDIT_LOG_PATH", str(tmp_path))
    # A throwaway signing key, generated per test: no literal secret.
    key = secrets.token_urlsafe(48)
    monkeypatch.setattr(auth_tokens, "_configured_key", lambda: key)


def _address(label: str = "person") -> str:
    return f"{label}-{secrets.token_hex(5)}@example.com"


def _person(session, email: str | None = None):
    user = User(email=email or _address(), full_name="Person", password_hash="")
    session.add(user)
    session.commit()
    headers = {
        "Authorization": "Bearer "
        + auth_tokens.create_access_token(user_id=user.id, email=user.email)
    }
    return user, headers


def _company(session, owner: User | None = None, role: str = "Owner") -> Company:
    row = Company(company_name=f"Invites {secrets.token_hex(3)}")
    session.add(row)
    session.commit()
    if owner is not None:
        session.add(CompanyMember(company_id=row.id, user_id=owner.id, human_role=role))
        session.commit()
    return row


def _invite(client, headers, company_id, email, role="Viewer"):
    return client.post(
        "/v1/company/invite",
        json={"company_id": company_id, "email": email, "human_role": role},
        headers=headers,
    )


def _memberships(session, user: User) -> list[CompanyMember]:
    return list(
        session.exec(
            select(CompanyMember).where(CompanyMember.user_id == user.id)
        ).all()
    )


def _actions(session, company_id: int) -> list[str]:
    return [
        row.action
        for row in session.exec(
            select(AuditLog)
            .where(AuditLog.company_id == company_id)
            .order_by(AuditLog.id)
        ).all()
    ]


# -- the invite answers alike and adds nobody -------------------------------


def test_the_invite_answers_alike_for_a_known_and_an_unknown_address(
    test_client, sqlite_session
):
    owner, headers = _person(sqlite_session)
    company = _company(sqlite_session, owner)
    known, _ = _person(sqlite_session)
    to_known = _invite(test_client, headers, company.id, known.email)
    to_unknown = _invite(test_client, headers, company.id, _address("nobody"))
    assert to_known.status_code == to_unknown.status_code == 202
    assert set(to_known.json()) == set(to_unknown.json())
    assert to_known.json()["status"] == to_unknown.json()["status"] == "pending"
    assert to_known.json()["detail"] == to_unknown.json()["detail"]
    assert "user_id" not in to_known.json()


def test_the_invite_adds_nobody_and_keeps_only_the_tokens_digest(
    test_client, sqlite_session
):
    owner, headers = _person(sqlite_session)
    company = _company(sqlite_session, owner)
    guest, _ = _person(sqlite_session)
    response = _invite(test_client, headers, company.id, guest.email, "Manager")
    assert response.status_code == 202, response.text
    assert _memberships(sqlite_session, guest) == []
    token = response.json()["token"]
    row = sqlite_session.exec(
        select(CompanyInvitation).where(CompanyInvitation.company_id == company.id)
    ).one()
    assert row.email == guest.email
    assert row.human_role == "Manager"
    assert row.status == "pending"
    assert row.invited_by_user_id == owner.id
    assert row.token_sha256 == hashlib.sha256(token.encode()).hexdigest()
    assert token not in repr(row.model_dump())
    assert row.expires_at > datetime.now(UTC).replace(tzinfo=None)


def test_the_invite_normalises_the_address(test_client, sqlite_session):
    owner, headers = _person(sqlite_session)
    company = _company(sqlite_session, owner)
    guest, guest_headers = _person(sqlite_session, _address("ana"))
    shouted = "  " + guest.email.upper() + " "
    assert _invite(test_client, headers, company.id, shouted).status_code == 202
    mine = test_client.get("/v1/invitations", headers=guest_headers).json()
    assert [i["company_id"] for i in mine["invitations"]] == [company.id]


# -- the invitee accepts or declines ----------------------------------------


def test_the_invitee_sees_accepts_once_and_becomes_a_member(
    test_client, sqlite_session
):
    owner, headers = _person(sqlite_session)
    company = _company(sqlite_session, owner)
    guest, guest_headers = _person(sqlite_session)
    stranger, stranger_headers = _person(sqlite_session)
    _invite(test_client, headers, company.id, guest.email, "Manager")

    listed = test_client.get("/v1/invitations", headers=guest_headers)
    assert listed.status_code == 200
    [invitation] = listed.json()["invitations"]
    assert invitation["company_id"] == company.id
    assert invitation["human_role"] == "Manager"
    assert "token" not in invitation and "token_sha256" not in invitation
    assert (
        test_client.get("/v1/invitations", headers=stranger_headers).json()[
            "invitations"
        ]
        == []
    )

    # Somebody else cannot accept it, and learns nothing about it.
    refused = test_client.post(
        f"/v1/invitations/{invitation['id']}/accept", headers=stranger_headers
    )
    assert refused.status_code == 404
    assert _memberships(sqlite_session, stranger) == []

    accepted = test_client.post(
        f"/v1/invitations/{invitation['id']}/accept", headers=guest_headers
    )
    assert accepted.status_code == 200, accepted.text
    [member] = _memberships(sqlite_session, guest)
    assert (member.company_id, member.human_role) == (company.id, "Manager")

    again = test_client.post(
        f"/v1/invitations/{invitation['id']}/accept", headers=guest_headers
    )
    assert again.status_code == 404
    assert test_client.get("/v1/invitations", headers=guest_headers).json() == {
        "invitations": []
    }
    assert _actions(sqlite_session, company.id) == [
        "invitation_created",
        "invitation_accepted",
    ]


def test_a_declined_invitation_makes_nobody_a_member(test_client, sqlite_session):
    owner, headers = _person(sqlite_session)
    company = _company(sqlite_session, owner)
    guest, guest_headers = _person(sqlite_session)
    _invite(test_client, headers, company.id, guest.email)
    [invitation] = test_client.get("/v1/invitations", headers=guest_headers).json()[
        "invitations"
    ]
    declined = test_client.post(
        f"/v1/invitations/{invitation['id']}/decline", headers=guest_headers
    )
    assert declined.status_code == 200
    assert _memberships(sqlite_session, guest) == []
    accept_after = test_client.post(
        f"/v1/invitations/{invitation['id']}/accept", headers=guest_headers
    )
    assert accept_after.status_code == 404
    assert _actions(sqlite_session, company.id)[-1] == "invitation_declined"


def test_an_expired_invitation_cannot_be_accepted(test_client, sqlite_session):
    owner, headers = _person(sqlite_session)
    company = _company(sqlite_session, owner)
    guest, guest_headers = _person(sqlite_session)
    _invite(test_client, headers, company.id, guest.email)
    row = sqlite_session.exec(
        select(CompanyInvitation).where(CompanyInvitation.company_id == company.id)
    ).one()
    row.expires_at = datetime.now(UTC).replace(tzinfo=None) - timedelta(minutes=1)
    sqlite_session.add(row)
    sqlite_session.commit()
    # No longer listed, and refused.
    assert test_client.get("/v1/invitations", headers=guest_headers).json() == {
        "invitations": []
    }
    response = test_client.post(
        f"/v1/invitations/{row.id}/accept", headers=guest_headers
    )
    assert response.status_code == 410
    assert _memberships(sqlite_session, guest) == []


def test_the_token_accepts_only_for_the_invited_address(test_client, sqlite_session):
    owner, headers = _person(sqlite_session)
    company = _company(sqlite_session, owner)
    guest, guest_headers = _person(sqlite_session)
    _, stranger_headers = _person(sqlite_session)
    token = _invite(test_client, headers, company.id, guest.email).json()["token"]

    stolen = test_client.post(
        "/v1/invitations/accept", json={"token": token}, headers=stranger_headers
    )
    assert stolen.status_code == 404
    forged = test_client.post(
        "/v1/invitations/accept",
        json={"token": secrets.token_urlsafe(32)},
        headers=guest_headers,
    )
    assert forged.status_code == 404
    accepted = test_client.post(
        "/v1/invitations/accept", json={"token": token}, headers=guest_headers
    )
    assert accepted.status_code == 200, accepted.text
    assert [m.company_id for m in _memberships(sqlite_session, guest)] == [company.id]
    reused = test_client.post(
        "/v1/invitations/accept", json={"token": token}, headers=guest_headers
    )
    assert reused.status_code == 404


def test_inviting_again_replaces_the_pending_invitation(test_client, sqlite_session):
    owner, headers = _person(sqlite_session)
    company = _company(sqlite_session, owner)
    guest, guest_headers = _person(sqlite_session)
    first = _invite(test_client, headers, company.id, guest.email).json()["token"]
    _invite(test_client, headers, company.id, guest.email, "Manager")
    listed = test_client.get("/v1/invitations", headers=guest_headers).json()
    assert [i["human_role"] for i in listed["invitations"]] == ["Manager"]
    old = test_client.post(
        "/v1/invitations/accept", json={"token": first}, headers=guest_headers
    )
    assert old.status_code == 404


# -- who may invite whom -----------------------------------------------------


@pytest.mark.parametrize(
    "inviter, role, expected",
    [
        ("Owner", "Owner", 202),
        ("Owner", "Manager", 202),
        ("Manager", "Manager", 202),
        ("Manager", "Viewer", 202),
        ("Manager", "Owner", 403),
        ("Viewer", "Viewer", 403),
    ],
)
def test_an_inviter_invites_up_to_their_own_role(
    test_client, sqlite_session, inviter, role, expected
):
    person, headers = _person(sqlite_session)
    company = _company(sqlite_session, person, inviter)
    response = _invite(test_client, headers, company.id, _address(), role)
    assert response.status_code == expected, response.text
    count = len(
        sqlite_session.exec(
            select(CompanyInvitation).where(CompanyInvitation.company_id == company.id)
        ).all()
    )
    assert count == (1 if expected == 202 else 0)


def test_an_api_token_cannot_invite(test_client, sqlite_session):
    owner, _ = _person(sqlite_session)
    company = _company(sqlite_session, owner)
    _row, raw = api_tokens.create_token(
        sqlite_session,
        company_id=company.id,
        name="automation",
        role="Manager",
        created_by=owner.email,
    )
    response = _invite(
        test_client, {"Authorization": f"Bearer {raw}"}, company.id, _address()
    )
    assert response.status_code == 403
    assert sqlite_session.exec(select(CompanyInvitation)).all() == []


def test_a_manager_lists_and_revokes_the_companys_pending_invitations(
    test_client, sqlite_session
):
    manager, headers = _person(sqlite_session)
    company = _company(sqlite_session, manager, "Manager")
    guest, guest_headers = _person(sqlite_session)
    _invite(test_client, headers, company.id, guest.email)
    listed = test_client.get(
        f"/v1/companies/{company.id}/invitations", headers=headers
    ).json()
    [pending] = listed["invitations"]
    assert pending["email"] == guest.email and "token_sha256" not in pending
    revoked = test_client.delete(
        f"/v1/companies/{company.id}/invitations/{pending['id']}", headers=headers
    )
    assert revoked.status_code == 204
    accept = test_client.post(
        f"/v1/invitations/{pending['id']}/accept", headers=guest_headers
    )
    assert accept.status_code == 404
    assert _actions(sqlite_session, company.id) == [
        "invitation_created",
        "invitation_revoked",
    ]
