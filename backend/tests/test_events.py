"""Task lifecycle events (services.events)."""

import secrets

import auth_tokens
import pytest
from auth_tokens import create_access_token
from config import settings
from models import Company, CompanyMember, Sprint, User
from services.tasks import events


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


def test_record_and_list_events(sqlite_session):
    company = _company(sqlite_session)
    sprint = Sprint(company_id=company.id, sprint_name="S", scope_size="S")
    sqlite_session.add(sprint)
    sqlite_session.commit()
    sqlite_session.refresh(sprint)

    events.record(
        sqlite_session, company.id, sprint.id, None, "sprint.started", {"x": 1}
    )
    rows = events.list_for_sprint(sqlite_session, sprint.id)
    assert len(rows) == 1
    assert rows[0].event_type == "sprint.started"


def _headers(session, company_id, role="Viewer"):
    user = User(
        email=f"e-{secrets.token_hex(4)}@x.com", full_name="u", password_hash="x"
    )
    session.add(user)
    session.commit()
    session.add(CompanyMember(company_id=company_id, user_id=user.id, human_role=role))
    session.commit()
    return {
        "Authorization": f"Bearer {create_access_token(user_id=user.id, email=user.email)}"
    }


def test_events_endpoint_auth_and_isolation(
    test_client, sqlite_session, signing_key, monkeypatch
):
    monkeypatch.setattr(settings, "AUTH_REQUIRED", True)
    monkeypatch.setattr(auth_tokens, "_configured_key", lambda: signing_key)
    company = _company(sqlite_session)
    other = _company(sqlite_session)
    sprint = Sprint(company_id=company.id, sprint_name="S", scope_size="S")
    sqlite_session.add(sprint)
    sqlite_session.commit()
    sqlite_session.refresh(sprint)
    events.record(
        sqlite_session, company.id, sprint.id, None, "sprint.started", {"x": 1}
    )

    headers = _headers(sqlite_session, company.id, "Viewer")
    r = test_client.get(f"/v1/sprints/{sprint.id}/events", headers=headers)
    assert r.status_code == 200
    assert len(r.json()["events"]) == 1

    outsider_headers = _headers(sqlite_session, other.id, "Viewer")
    r2 = test_client.get(f"/v1/sprints/{sprint.id}/events", headers=outsider_headers)
    assert r2.status_code == 403
