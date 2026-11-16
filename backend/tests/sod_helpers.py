"""What the tests of segregation of duties share (issue #543): enforcement on
for every test (autouse), a company, a person of a role, who started a
task's sprint, the audit rows of an action and an approval through the API.
The tests are ``test_sod.py`` and ``test_sod_policy.py``.
"""

from __future__ import annotations

import secrets

import auth_tokens
import pytest
from config import settings
from models import (
    AuditLog,
    Company,
    CompanyMember,
    Sprint,
    User,
)
from sqlmodel import select


@pytest.fixture(autouse=True)
def auth_on(monkeypatch):
    monkeypatch.setattr(settings, "AUTH_REQUIRED", True)
    key = secrets.token_urlsafe(48)
    monkeypatch.setattr(auth_tokens, "_configured_key", lambda: key)


def _company(session) -> Company:
    row = Company(company_name=f"SoD {secrets.token_hex(3)}")
    session.add(row)
    session.commit()
    return row


def _person(session, company_id: int, role: str):
    user = User(
        email=f"{role.lower()}-{secrets.token_hex(4)}@example.test",
        full_name=f"{role} {secrets.token_hex(2)}",
        password_hash="unused",
    )
    session.add(user)
    session.commit()
    session.add(CompanyMember(company_id=company_id, user_id=user.id, human_role=role))
    session.commit()
    token = auth_tokens.create_access_token(user_id=user.id, email=user.email)
    return user, {"Authorization": f"Bearer {token}"}


def _started_by(session, item, user_id):
    sprint = session.get(Sprint, item.sprint_id)
    sprint.started_by_user_id = user_id
    session.add(sprint)
    session.commit()
    return sprint


def _audits(session, company_id, action):
    return session.exec(
        select(AuditLog).where(
            AuditLog.company_id == company_id, AuditLog.action == action
        )
    ).all()


def _approve(client, company_id, run, headers):
    return client.post(
        f"/v1/agent_runs/{run.id}/reviews",
        json={"company_id": company_id, "event": "approve", "body": None},
        headers=headers,
    )
