"""API tokens for external callers (services.api_tokens)."""

import secrets

from models import Company
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
