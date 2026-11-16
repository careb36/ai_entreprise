"""What the tests of opt-in authentication and company roles share: the
enforcement switched on for every test (autouse), a signing key, a signed-in
identity with its company, a member of a role and a run.
The tests are ``test_auth_enforcement*.py``.
"""

import secrets

import auth_tokens
import pytest
from config import settings
from models import (
    Company,
    CompanyMember,
    User,
)
from models.agent_run import AgentRun


@pytest.fixture(autouse=True)
def enforcement(monkeypatch, tmp_path):
    monkeypatch.setattr(settings, "AUTH_REQUIRED", True)
    monkeypatch.setenv("AUDIT_LOG_PATH", str(tmp_path))


@pytest.fixture
def signing_key(monkeypatch):
    key = secrets.token_urlsafe(48)
    monkeypatch.setattr(auth_tokens, "_configured_key", lambda: key)
    return key


@pytest.fixture
def identity(sqlite_session, signing_key):
    user = User(
        email=f"auth-{secrets.token_hex(8)}@example.com",
        full_name="Authenticated reviewer",
        password_hash="unused",
    )
    company = Company(company_name="Authorization test")
    sqlite_session.add(user)
    sqlite_session.add(company)
    sqlite_session.commit()
    headers = {
        "Authorization": "Bearer "
        + auth_tokens.create_access_token(user_id=user.id, email=user.email)
    }
    return user, company, headers


def _member(session, identity, role):
    user, company, _ = identity
    member = CompanyMember(company_id=company.id, user_id=user.id, human_role=role)
    session.add(member)
    session.commit()
    return member


def _run(session, company_id):
    run = AgentRun(
        company_id=company_id,
        external_ref=f"auth:{secrets.token_hex(8)}",
        producer="test",
        artifact_digest="unused",
        artifact_size=0,
    )
    session.add(run)
    session.commit()
    return run
