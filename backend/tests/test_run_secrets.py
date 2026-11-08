"""Run secrets (services.run_secrets)."""

import secrets

from models import Company
from services import run_secrets


def _company(session):
    company = Company(company_name=f"C {secrets.token_hex(3)}", autonomy_level=2)
    session.add(company)
    session.commit()
    return company


def test_create_and_decrypt(sqlite_session):
    company = _company(sqlite_session)
    secret = run_secrets.create(sqlite_session, company.id, 1, "TOKEN", "secret-value")
    assert secret.id is not None
    env = run_secrets.env_for_task(sqlite_session, 1)
    assert env["TOKEN"] == "secret-value"


def test_delete(sqlite_session):
    company = _company(sqlite_session)
    secret = run_secrets.create(sqlite_session, company.id, 999, "TOKEN", "v")
    assert run_secrets.delete(sqlite_session, secret.id, company.id)
    assert run_secrets.env_for_task(sqlite_session, 999) == {}
