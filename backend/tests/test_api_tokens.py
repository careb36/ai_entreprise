"""API tokens for external callers (services.api_tokens)."""

import secrets

import auth_tokens
import pytest
from auth_tokens import create_access_token
from config import settings
from models import Company, CompanyMember, User
from services import api_tokens


def _company(session):
    company = Company(company_name=f"C {secrets.token_hex(3)}", autonomy_level=2)
    session.add(company)
    session.commit()
    return company


def test_create_and_verify_token(sqlite_session):
    company = _company(sqlite_session)
    token, raw = api_tokens.create_token(sqlite_session, company.id, "ci")
    assert raw.startswith("fab_")
    found = api_tokens.verify(sqlite_session, raw)
    assert found is not None and found.id == token.id


def test_revoke_token(sqlite_session):
    company = _company(sqlite_session)
    token, raw = api_tokens.create_token(sqlite_session, company.id, "ci")
    assert api_tokens.revoke(sqlite_session, token.id, company.id)
    assert api_tokens.verify(sqlite_session, raw) is None


@pytest.fixture()
def signing_key(monkeypatch):
    key = secrets.token_urlsafe(48)
    monkeypatch.setattr(auth_tokens, "_configured_key", lambda: key)
    return key


def _headers(session, company_id, role="Manager"):
    user = User(
        email=f"t-{secrets.token_hex(4)}@x.com", full_name="u", password_hash="x"
    )
    session.add(user)
    session.commit()
    session.add(CompanyMember(company_id=company_id, user_id=user.id, human_role=role))
    session.commit()
    return {
        "Authorization": f"Bearer {create_access_token(user_id=user.id, email=user.email)}"
    }


def test_api_token_http_create_and_list(
    test_client, sqlite_session, signing_key, monkeypatch
):
    monkeypatch.setattr(settings, "AUTH_REQUIRED", True)
    monkeypatch.setattr(auth_tokens, "_configured_key", lambda: signing_key)
    company = _company(sqlite_session)
    headers = _headers(sqlite_session, company.id, "Manager")
    r = test_client.post(
        "/v1/auth/tokens",
        json={"company_id": company.id, "name": "ci"},
        headers=headers,
    )
    assert r.status_code == 200
    assert "token" in r.json()
    r2 = test_client.get(f"/v1/auth/tokens?company_id={company.id}", headers=headers)
    assert r2.status_code == 200
    assert len(r2.json()["tokens"]) == 1


def _mint(test_client, headers, company_id, **body):
    return test_client.post(
        "/v1/auth/tokens",
        json={"company_id": company_id, "name": "ci", **body},
        headers=headers,
    )


def test_a_token_acts_with_its_role_only(
    test_client, sqlite_session, signing_key, monkeypatch
):
    monkeypatch.setattr(settings, "AUTH_REQUIRED", True)
    monkeypatch.setattr(auth_tokens, "_configured_key", lambda: signing_key)
    company = _company(sqlite_session)
    manager = _headers(sqlite_session, company.id, "Manager")

    viewer_token = _mint(test_client, manager, company.id)  # Viewer by default
    assert viewer_token.status_code == 200
    body = viewer_token.json()
    assert body["role"] == "Viewer" and body["expires_at"] and not body["expired"]
    as_viewer = {"Authorization": f"Bearer {body['token']}"}
    # A Viewer token reads, and cannot do what only a Manager does.
    assert (
        test_client.get(
            f"/v1/auth/tokens?company_id={company.id}", headers=as_viewer
        ).status_code
        == 200
    )
    assert _mint(test_client, as_viewer, company.id).status_code == 403

    manager_token = _mint(test_client, manager, company.id, role="Manager").json()
    as_token = {"Authorization": f"Bearer {manager_token['token']}"}
    # A token never mints tokens, whatever its role.
    refused = _mint(test_client, as_token, company.id)
    assert refused.status_code == 403
    assert "cannot create API tokens" in refused.json()["detail"]
    listed = test_client.get(
        f"/v1/auth/tokens?company_id={company.id}", headers=manager
    ).json()["tokens"]
    assert {t["role"] for t in listed} == {"Viewer", "Manager"}
    assert all("token" not in t and "token_hash" not in t for t in listed)


def test_no_one_mints_a_token_above_their_role_or_an_owner_token(
    test_client, sqlite_session, signing_key, monkeypatch
):
    monkeypatch.setattr(settings, "AUTH_REQUIRED", True)
    monkeypatch.setattr(auth_tokens, "_configured_key", lambda: signing_key)
    company = _company(sqlite_session)
    viewer = _headers(sqlite_session, company.id, "Viewer")
    owner = _headers(sqlite_session, company.id, "Owner")
    assert _mint(test_client, viewer, company.id).status_code == 403
    assert _mint(test_client, owner, company.id, role="Owner").status_code == 422
    assert _mint(test_client, owner, company.id, role="Manager").status_code == 200
    assert _mint(test_client, owner, company.id, expires_in_days=0).status_code == 422
    assert _mint(test_client, owner, company.id, expires_in_days=366).status_code == 422
    forever = _mint(test_client, owner, company.id, expires_in_days=None).json()
    assert forever["expires_at"] is None


def test_an_expired_token_is_refused(
    test_client, sqlite_session, signing_key, monkeypatch
):
    from datetime import UTC, datetime, timedelta

    monkeypatch.setattr(settings, "AUTH_REQUIRED", True)
    monkeypatch.setattr(auth_tokens, "_configured_key", lambda: signing_key)
    company = _company(sqlite_session)
    token, raw = api_tokens.create_token(
        sqlite_session, company.id, "ci", role="Viewer", expires_in_days=1
    )
    assert api_tokens.verify(sqlite_session, raw) is not None
    token.expires_at = datetime.now(UTC) - timedelta(seconds=1)
    sqlite_session.add(token)
    sqlite_session.commit()
    assert api_tokens.verify(sqlite_session, raw) is None
    response = test_client.get(
        f"/v1/auth/tokens?company_id={company.id}",
        headers={"Authorization": f"Bearer {raw}"},
    )
    assert response.status_code == 401
    # A token from before expiries never expires, and keeps acting as a Manager.
    legacy, legacy_raw = api_tokens.create_token(
        sqlite_session, company.id, "old", role="Manager", expires_in_days=None
    )
    assert legacy.expires_at is None
    assert api_tokens.verify(sqlite_session, legacy_raw) is not None


def test_an_unknown_role_is_refused_by_the_service(sqlite_session):
    company = _company(sqlite_session)
    with pytest.raises(ValueError):
        api_tokens.create_token(sqlite_session, company.id, "ci", role="Owner")
