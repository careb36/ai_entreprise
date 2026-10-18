"""``GET /v1/sprints`` — a company's sprints with progress, scoped by membership."""

import secrets

import auth_tokens
import pytest
from config import settings
from models import BacklogItem, Company, CompanyMember, Sprint, User


@pytest.fixture
def signing_key(monkeypatch):
    key = secrets.token_urlsafe(48)
    monkeypatch.setattr(auth_tokens, "_configured_key", lambda: key)
    return key


def _company(session, name):
    company = Company(company_name=name)
    session.add(company)
    session.commit()
    return company


def _sprint(session, company_id, name, statuses=(), **fields):
    sprint = Sprint(company_id=company_id, sprint_name=name, **fields)
    session.add(sprint)
    session.flush()
    for index, status in enumerate(statuses):
        session.add(
            BacklogItem(sprint_id=sprint.id, title=f"{name} {index}", status=status)
        )
    session.commit()
    return sprint


def _user_headers(session, company_id=None, role="Viewer"):
    user = User(
        email=f"list-{secrets.token_hex(8)}@example.com",
        full_name="Sprint list reader",
        password_hash="unused",
    )
    session.add(user)
    session.commit()
    if company_id is not None:
        session.add(
            CompanyMember(company_id=company_id, user_id=user.id, human_role=role)
        )
        session.commit()
    token = auth_tokens.create_access_token(user_id=user.id, email=user.email)
    return {"Authorization": f"Bearer {token}"}


def test_member_lists_own_sprints_newest_first_with_progress(
    test_client, sqlite_session, signing_key, monkeypatch
):
    monkeypatch.setattr(settings, "AUTH_REQUIRED", True)
    mine = _company(sqlite_session, "Sprint list owner")
    other = _company(sqlite_session, "Sprint list other tenant")
    older = _sprint(
        sqlite_session,
        mine.id,
        "Foundation",
        statuses=("done", "todo", "in_progress"),
        start_date="2026-10-01",
        goal="Lay the base",
        scope_size="S",
        status="completed",
    )
    newer = _sprint(sqlite_session, mine.id, "Hardening", start_date="2026-10-15")
    _sprint(sqlite_session, other.id, "Someone else's", statuses=("done",))
    headers = _user_headers(sqlite_session, mine.id, role="Viewer")

    response = test_client.get(
        "/v1/sprints", params={"company_id": mine.id}, headers=headers
    )

    assert response.status_code == 200
    assert response.json() == [
        {
            "id": newer.id,
            "sprint_name": "Hardening",
            "status": "active",
            "goal": None,
            "start_date": "2026-10-15",
            "scope_size": "M",
            "total": 0,
            "done": 0,
        },
        {
            "id": older.id,
            "sprint_name": "Foundation",
            "status": "completed",
            "goal": "Lay the base",
            "start_date": "2026-10-01",
            "scope_size": "S",
            "total": 3,
            "done": 1,
        },
    ]


def test_nonmember_gets_403_when_auth_required(
    test_client, sqlite_session, signing_key, monkeypatch
):
    monkeypatch.setattr(settings, "AUTH_REQUIRED", True)
    target = _company(sqlite_session, "Sprint list private tenant")
    _sprint(sqlite_session, target.id, "Private")
    member_elsewhere = _company(sqlite_session, "Sprint list elsewhere")
    headers = _user_headers(sqlite_session, member_elsewhere.id, role="Owner")

    response = test_client.get(
        "/v1/sprints", params={"company_id": target.id}, headers=headers
    )

    assert response.status_code == 403
    assert response.json() == {"detail": "Not a member of this company"}


def test_requires_token_when_auth_required(test_client, monkeypatch):
    monkeypatch.setattr(settings, "AUTH_REQUIRED", True)
    response = test_client.get("/v1/sprints", params={"company_id": 1})
    assert response.status_code == 401
    assert response.headers["www-authenticate"] == "Bearer"


def test_company_id_is_required(test_client):
    response = test_client.get("/v1/sprints")
    assert response.status_code == 422
