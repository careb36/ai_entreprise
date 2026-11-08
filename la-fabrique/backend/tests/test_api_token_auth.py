"""API token authentication (auth_deps + api_tokens)."""

import secrets

import auth_tokens
import pytest
from config import settings
from models import Company
from services import api_tokens


@pytest.fixture()
def signing_key(monkeypatch):
    key = secrets.token_urlsafe(48)
    monkeypatch.setattr(auth_tokens, "_configured_key", lambda: key)
    return key


def _company(session):
    company = Company(company_name=f"C {secrets.token_hex(3)}", autonomy_level=2)
    session.add(company)
    session.commit()
    return company


def test_api_token_authenticates(test_client, sqlite_session, signing_key, monkeypatch):
    monkeypatch.setattr(settings, "AUTH_REQUIRED", True)
    monkeypatch.setattr(auth_tokens, "_configured_key", lambda: signing_key)
    company = _company(sqlite_session)
    _token, raw = api_tokens.create_token(sqlite_session, company.id, "ci")
    headers = {"Authorization": f"Bearer {raw}"}
    r = test_client.get(f"/v1/companies/{company.id}/model_settings", headers=headers)
    # model_settings endpoint may or may not exist; check auth passes (not 401)
    assert r.status_code != 401
