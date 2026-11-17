"""Opt-in authentication, company roles, and legacy compatibility: a run's
approvals (a Bearer challenge, a non-member's 403 that hides whether the
company exists, a Viewer lists but does not approve, a Manager's approval
audited with the identity), only an Owner invites, a company's init creates
exactly one Owner and rolls back with it, the signed webhook stays public,
and with the flag off nothing is enforced. The routes' roles are in
``test_auth_enforcement_routes.py``, the audit and memory scope in
``test_auth_enforcement_audit.py``; the shared fixtures in
``auth_enforcement_helpers``.
"""

import secrets

import auth_enforcement_helpers
import pytest
from auth_enforcement_helpers import _member, _run
from config import settings
from models import (
    AuditLog,
    Company,
    CompanyMember,
    User,
)
from models.github import GitHubRepoBinding
from services.loop.company_service import CompanyService
from sqlmodel import select
from test_github_adapter import _webhook

# The shared fixtures.
enforcement = auth_enforcement_helpers.enforcement
signing_key = auth_enforcement_helpers.signing_key
identity = auth_enforcement_helpers.identity


def _approval(company_id):
    return {
        "company_id": company_id,
        "approver_name": "Payload Reviewer",
        "decision": "approved",
    }


def test_list_requires_bearer_challenge(test_client):
    response = test_client.get("/v1/agent_runs", params={"company_id": 1})
    assert response.status_code == 401
    assert response.headers["www-authenticate"] == "Bearer"


@pytest.mark.parametrize("company_id", [1, 99999999])
def test_nonmember_gets_403_not_company_existence(test_client, identity, company_id):
    response = test_client.get(
        "/v1/agent_runs", params={"company_id": company_id}, headers=identity[2]
    )
    assert response.status_code == 403
    assert response.json() == {"detail": "Not a member of this company"}


def test_viewer_can_list_but_not_approve(test_client, sqlite_session, identity):
    _member(sqlite_session, identity, "Viewer")
    run = _run(sqlite_session, identity[1].id)
    listed = test_client.get(
        "/v1/agent_runs", params={"company_id": run.company_id}, headers=identity[2]
    )
    assert listed.status_code == 200
    assert run.id in [item["id"] for item in listed.json()["agent_runs"]]
    response = test_client.post(
        f"/v1/agent_runs/{run.id}/approve",
        json=_approval(run.company_id),
        headers=identity[2],
    )
    assert response.status_code == 403
    assert response.json() == {"detail": "Requires Manager role"}


def test_manager_approval_preserves_payload_audit_identity(
    test_client, sqlite_session, identity
):
    _member(sqlite_session, identity, "Manager")
    run = _run(sqlite_session, identity[1].id)
    response = test_client.post(
        f"/v1/agent_runs/{run.id}/approve",
        json=_approval(run.company_id),
        headers=identity[2],
    )
    assert response.status_code == 201
    audit = sqlite_session.get(AuditLog, response.json()["audit_log_id"])
    assert audit.agent_id == "human:Payload Reviewer"
    assert audit.action == "agent_run_approved"


@pytest.mark.parametrize("role, expected", [("Manager", 403), ("Owner", 202)])
def test_only_an_owner_invites_an_owner(
    test_client, sqlite_session, identity, role, expected
):
    """Inviting adds nobody (a pending invitation, ``test_invitations.py``),
    and a Manager cannot invite above their own role."""
    _member(sqlite_session, identity, role)
    invitee = User(
        email=f"invite-{secrets.token_hex(8)}@example.com",
        full_name="Invitee",
        password_hash="unused",
    )
    sqlite_session.add(invitee)
    sqlite_session.commit()
    response = test_client.post(
        "/v1/company/invite",
        json={
            "company_id": identity[1].id,
            "email": invitee.email,
            "human_role": "Owner",
        },
        headers=identity[2],
    )
    assert response.status_code == expected
    memberships = sqlite_session.exec(
        select(CompanyMember).where(CompanyMember.user_id == invitee.id)
    ).all()
    assert memberships == []
    if expected == 403:
        assert "cannot invite" in response.json()["detail"]
    else:
        assert response.json()["status"] == "pending"


@pytest.fixture
def company_services(monkeypatch):
    monkeypatch.setattr(
        "services.loop.company_service.plan_foundation_sprint",
        lambda **kwargs: {"goal": "Test", "tasks": []},
    )
    monkeypatch.setattr(CompanyService, "_seed_neo4j", lambda *args: None)


def test_init_requires_token(test_client):
    response = test_client.post("/v1/init_company", json={"company_name": "Denied"})
    assert response.status_code == 401
    assert response.headers["www-authenticate"] == "Bearer"


def test_init_creates_exactly_one_owner(
    test_client, sqlite_session, identity, company_services
):
    response = test_client.post(
        "/v1/init_company", json={"company_name": "Owned"}, headers=identity[2]
    )
    assert response.status_code == 200
    members = sqlite_session.exec(
        select(CompanyMember).where(
            CompanyMember.company_id == response.json()["company_id"]
        )
    ).all()
    assert len(members) == 1
    assert members[0].user_id == identity[0].id
    assert members[0].human_role == "Owner"


def test_owner_bootstrap_rolls_back_with_company(
    test_client, sqlite_session, identity, company_services, monkeypatch
):
    def fail_plan(**kwargs):
        raise RuntimeError("Planning failed")

    monkeypatch.setattr(
        "services.loop.company_service.plan_foundation_sprint", fail_plan
    )
    response = test_client.post(
        "/v1/init_company", json={"company_name": "Rollback"}, headers=identity[2]
    )
    assert response.status_code == 500
    sqlite_session.rollback()
    assert (
        sqlite_session.exec(
            select(Company).where(Company.company_name == "Rollback")
        ).all()
        == []
    )
    assert (
        sqlite_session.exec(
            select(CompanyMember).where(CompanyMember.user_id == identity[0].id)
        ).all()
        == []
    )


def test_signed_webhook_stays_public(test_client, sqlite_session, identity):
    company_id = identity[1].id
    repo = f"auth-public-{company_id}"
    sqlite_session.add(
        GitHubRepoBinding(
            company_id=company_id,
            owner="careb36",
            repo=repo,
            webhook_secret_encrypted="plaintext:test-webhook-secret",
        )
    )
    sqlite_session.commit()
    response = _webhook(test_client, {}, event="ping", repository=f"careb36/{repo}")
    assert response.status_code == 204


def test_flag_off_list_ignores_invalid_token(test_client, monkeypatch, identity):
    monkeypatch.setattr(settings, "AUTH_REQUIRED", False)
    for headers in ({}, {"Authorization": "******"}):
        response = test_client.get(
            "/v1/agent_runs", params={"company_id": identity[1].id}, headers=headers
        )
        assert response.status_code == 200
        assert response.json() == {"agent_runs": []}


def test_flag_off_approve_without_token(
    test_client, sqlite_session, identity, monkeypatch
):
    monkeypatch.setattr(settings, "AUTH_REQUIRED", False)
    run = _run(sqlite_session, identity[1].id)
    response = test_client.post(
        f"/v1/agent_runs/{run.id}/approve", json=_approval(run.company_id)
    )
    assert response.status_code == 201
    assert response.json()["approver_name"] == "Payload Reviewer"


def test_flag_off_init_creates_no_membership(
    test_client, sqlite_session, monkeypatch, company_services
):
    monkeypatch.setattr(settings, "AUTH_REQUIRED", False)
    response = test_client.post("/v1/init_company", json={"company_name": "Legacy"})
    assert response.status_code == 200
    assert (
        sqlite_session.exec(
            select(CompanyMember).where(
                CompanyMember.company_id == response.json()["company_id"]
            )
        ).all()
        == []
    )
