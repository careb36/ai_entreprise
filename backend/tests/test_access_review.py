"""The periodic access review and the service accounts inventory (issue #543;
``docs/archive/design/SEGREGATION_OF_DUTIES.md``).

An Owner reviews every member (own, team-derived and effective role, SSO and
chat links, last activity) and every working API token on a schedule
(quarterly by default); each item is confirmed or removed, the review is
recorded and audited, and a review left past its grace is flagged missed and
recorded late. The inventory names every credential that acts without a
person, never its value.
"""

from __future__ import annotations

import secrets
from datetime import UTC, datetime, timedelta

import auth_tokens
import pytest
from config import settings
from models import (
    AccessReview,
    ApiToken,
    AuditLog,
    ChatIdentity,
    Company,
    CompanyMember,
    CompanyWebhook,
    SessionFamily,
    Team,
    TeamMember,
    User,
)
from services.platform import access_review, api_tokens, service_accounts
from sqlmodel import select


def _utcnow() -> datetime:
    return datetime.now(UTC).replace(tzinfo=None)


@pytest.fixture(autouse=True)
def auth_on(monkeypatch):
    monkeypatch.setattr(settings, "AUTH_REQUIRED", True)
    key = secrets.token_urlsafe(48)
    monkeypatch.setattr(auth_tokens, "_configured_key", lambda: key)


def _company(session) -> Company:
    row = Company(company_name=f"Review {secrets.token_hex(3)}")
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


def _audit_at(session, company_id, when):
    session.add(
        AuditLog(company_id=company_id, action="company_created", created_at=when)
    )
    session.commit()


# -- The schedule (pure) -----------------------------------------------------


def test_the_schedule_is_scheduled_then_due_then_missed():
    start = datetime(2026, 1, 1)
    due = start + timedelta(days=90)
    assert access_review.schedule(None, None, 90, start)["state"] == "due"
    assert access_review.schedule(start, None, 90, due - timedelta(days=1))[
        "state"
    ] == ("scheduled")
    assert access_review.schedule(start, None, 90, due)["state"] == "due"
    assert access_review.schedule(start, None, 90, due + timedelta(days=14))[
        "state"
    ] == ("due")
    assert access_review.schedule(start, None, 90, due + timedelta(days=15))[
        "state"
    ] == ("missed")
    # The last completed review restarts the clock.
    done = due + timedelta(days=30)
    later = access_review.schedule(start, done, 90, done + timedelta(days=1))
    assert later == {
        "due_at": done + timedelta(days=90),
        "state": "scheduled",
        "grace_days": 14,
    }


def test_inactivity_is_observed_never_assumed():
    now = datetime(2026, 6, 1)
    assert access_review.inactive(None, 90, now)
    assert access_review.inactive(now - timedelta(days=91), 90, now)
    assert not access_review.inactive(now - timedelta(days=10), 90, now)


# -- The review --------------------------------------------------------------


def test_the_owner_sees_members_with_team_roles_tokens_links_and_activity(
    sqlite_session, test_client
):
    acme = _company(sqlite_session)
    owner, owner_h = _person(sqlite_session, acme.id, "Owner")
    viewer, _ = _person(sqlite_session, acme.id, "Viewer")
    team = Team(company_id=acme.id, name="Release", role="Manager")
    sqlite_session.add(team)
    sqlite_session.commit()
    sqlite_session.add(
        TeamMember(company_id=acme.id, team_id=team.id, user_id=viewer.id)
    )
    sqlite_session.add(
        ChatIdentity(
            company_id=acme.id,
            user_id=viewer.id,
            provider="slack",
            workspace_id="T1",
            external_user_id="U1",
        )
    )
    sqlite_session.add(
        SessionFamily(
            public_id=secrets.token_hex(8),
            user_id=owner.id,
            method="password",
            csrf_hash="x",
            created_at=_utcnow(),
            expires_at=_utcnow() + timedelta(days=1),
            last_used_at=_utcnow(),
        )
    )
    sqlite_session.commit()
    api_tokens.create_token(
        sqlite_session, acme.id, "ci", owner.email, [], role="Viewer"
    )

    response = test_client.get(
        f"/v1/companies/{acme.id}/access-review", headers=owner_h
    )

    assert response.status_code == 200
    assert response.headers["cache-control"] == "no-store"
    body = response.json()
    assert body["schedule"]["state"] == "due"  # never reviewed
    members = {m["id"]: m for m in body["members"]}
    assert members[viewer.id]["own_role"] == "Viewer"
    assert members[viewer.id]["effective_role"] == "Manager"
    assert members[viewer.id]["teams"] == [{"name": "Release", "role": "Manager"}]
    assert members[viewer.id]["chat_links"] == ["slack"]
    assert members[viewer.id]["inactive"] is True  # never signed in
    assert members[owner.id]["inactive"] is False
    (token,) = body["tokens"]
    assert token["label"] == "ci" and token["role"] == "Viewer"
    assert "token_hash" not in token


def test_only_a_signed_in_owner_reviews(sqlite_session, test_client):
    acme = _company(sqlite_session)
    owner, _ = _person(sqlite_session, acme.id, "Owner")
    _, manager_h = _person(sqlite_session, acme.id, "Manager")
    _, raw = api_tokens.create_token(
        sqlite_session, acme.id, "ci", owner.email, [], role="Manager"
    )
    for headers in (manager_h, {"Authorization": f"Bearer {raw}"}):
        for path in ("access-review", "service-accounts"):
            url = f"/v1/companies/{acme.id}/{path}"
            assert test_client.get(url, headers=headers).status_code == 403


def test_every_item_is_decided_removals_apply_and_the_review_is_recorded(
    sqlite_session, test_client
):
    acme = _company(sqlite_session)
    owner, owner_h = _person(sqlite_session, acme.id, "Owner")
    leaver, _ = _person(sqlite_session, acme.id, "Manager")
    team = Team(company_id=acme.id, name="Ops", role="Manager")
    sqlite_session.add(team)
    sqlite_session.commit()
    sqlite_session.add(
        TeamMember(company_id=acme.id, team_id=team.id, user_id=leaver.id)
    )
    sqlite_session.commit()
    token, _ = api_tokens.create_token(
        sqlite_session, acme.id, "old ci", owner.email, [], role="Viewer"
    )
    url = f"/v1/companies/{acme.id}/access-review"

    partial = test_client.post(
        url,
        json={"decisions": [{"kind": "member", "id": owner.id, "decision": "confirm"}]},
        headers=owner_h,
    )
    assert partial.status_code == 422
    assert f"member:{leaver.id}" in partial.json()["detail"]["missing"]
    selfish = test_client.post(
        url,
        json={
            "decisions": [
                {"kind": "member", "id": owner.id, "decision": "remove"},
                {"kind": "member", "id": leaver.id, "decision": "confirm"},
                {"kind": "token", "id": token.id, "decision": "confirm"},
            ]
        },
        headers=owner_h,
    )
    assert selfish.status_code == 409

    done = test_client.post(
        url,
        json={
            "decisions": [
                {"kind": "member", "id": owner.id, "decision": "confirm"},
                {"kind": "member", "id": leaver.id, "decision": "remove"},
                {"kind": "token", "id": token.id, "decision": "remove"},
            ],
            "note": "Quarterly review.",
        },
        headers=owner_h,
    )

    assert done.status_code == 201
    assert done.json()["summary"]["removed"] == 2
    assert done.json()["schedule"]["state"] == "scheduled"
    assert not sqlite_session.exec(
        select(CompanyMember).where(
            CompanyMember.company_id == acme.id, CompanyMember.user_id == leaver.id
        )
    ).all()
    assert not sqlite_session.exec(
        select(TeamMember).where(TeamMember.user_id == leaver.id)
    ).all()
    sqlite_session.refresh(token)
    assert sqlite_session.get(ApiToken, token.id).revoked_at is not None
    review = sqlite_session.exec(
        select(AccessReview).where(AccessReview.company_id == acme.id)
    ).one()
    assert review.on_time and review.completed_by_user_id == owner.id
    assert {(i["kind"], i["decision"]) for i in review.items} == {
        ("member", "confirm"),
        ("member", "remove"),
        ("token", "remove"),
    }
    audit = sqlite_session.get(AuditLog, review.audit_log_id)
    assert audit.action == "access_review_completed"
    assert audit.result["removed_user_ids"] == [leaver.id]
    assert audit.result["revoked_token_ids"] == [token.id]


def test_a_review_past_its_grace_is_flagged_missed_and_recorded_late(
    sqlite_session, test_client
):
    acme = _company(sqlite_session)
    owner, owner_h = _person(sqlite_session, acme.id, "Owner")
    _audit_at(sqlite_session, acme.id, _utcnow() - timedelta(days=120))
    url = f"/v1/companies/{acme.id}/access-review"

    state = test_client.get(url, headers=owner_h).json()["schedule"]
    assert state["state"] == "missed" and state["interval_days"] == 90

    done = test_client.post(
        url,
        json={"decisions": [{"kind": "member", "id": owner.id, "decision": "confirm"}]},
        headers=owner_h,
    )
    assert done.status_code == 201 and done.json()["on_time"] is False
    history = test_client.get(url, headers=owner_h).json()["history"]
    assert history[0]["on_time"] is False


# -- The service accounts inventory ------------------------------------------


def test_the_inventory_names_owner_scope_and_age_never_a_value(
    sqlite_session, test_client, monkeypatch
):
    acme = _company(sqlite_session)
    owner, owner_h = _person(sqlite_session, acme.id, "Owner")
    _, raw = api_tokens.create_token(
        sqlite_session, acme.id, "deploy", owner.email, ["runs"], role="Manager"
    )
    secret_path = secrets.token_hex(8)
    sqlite_session.add(
        CompanyWebhook(
            company_id=acme.id,
            url=f"https://hooks.example.test/{secret_path}",
            encrypted_secret="not-read",
            events=["task.done"],
        )
    )
    sqlite_session.commit()
    proxy_value = secrets.token_hex(12)
    monkeypatch.setattr(settings, "LLM_PROXY_INTERNAL_TOKEN", proxy_value)

    response = test_client.get(
        f"/v1/companies/{acme.id}/service-accounts", headers=owner_h
    )

    assert response.status_code == 200
    text = response.text
    assert raw not in text and secret_path not in text and proxy_value not in text
    body = response.json()
    kinds = {row["kind"]: row for row in body["company"]}
    assert kinds["api_token"]["owner"] == owner.email
    assert kinds["api_token"]["scope"] == "Manager; runs"
    assert kinds["api_token"]["age_days"] == 0
    assert kinds["webhook"]["name"] == "hooks.example.test"
    deployment = {row["kind"]: row for row in body["deployment"]}
    assert deployment["llm_proxy_token"]["present"] is True
    assert service_accounts.age_days(None, _utcnow()) is None
