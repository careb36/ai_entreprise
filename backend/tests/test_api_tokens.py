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
