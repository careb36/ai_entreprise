"""The roles each protected route requires: a token on every protected route, a
sprint scoped by its owning company, the backlog and dispatch roles, missing
rows still 404, the authentication guard (a no-op on roles with the flag
off), company mutations for a Manager, the quota reset by the business
unit's company and the enterprise reads refused to non-members. The shared
fixtures are in ``auth_enforcement_helpers``.
"""

import pytest
from auth_policy import require_authenticated, require_company_role
from config import settings
from fastapi import HTTPException
from models import (
    BacklogItem,
    BusinessUnit,
    Company,
    Sprint,
)

import auth_enforcement_helpers
from auth_enforcement_helpers import _member

# The shared fixtures.
enforcement = auth_enforcement_helpers.enforcement
signing_key = auth_enforcement_helpers.signing_key
identity = auth_enforcement_helpers.identity


@pytest.mark.parametrize(
    "method, path",
    [
        ("GET", "/v1/agent_runs?company_id=1"),
        ("POST", "/v1/agent_runs"),
        ("GET", "/v1/agent_runs/1?company_id=1"),
        ("GET", "/v1/agent_runs/1/verifications?company_id=1"),
        ("GET", "/v1/agent_runs/1/approvals?company_id=1"),
        ("POST", "/v1/agent_runs/1/verify"),
        ("POST", "/v1/agent_runs/1/approve"),
        ("POST", "/v1/plan_sprint"),
        ("POST", "/v1/plan_self_improvement_sprint"),
        ("POST", "/v1/start_sprint"),
        ("POST", "/v1/sprints/1/dispatch"),
        ("PATCH", "/v1/backlog/1"),
        ("GET", "/v1/sprints/1/board"),
        ("GET", "/v1/sprints?company_id=1"),
        ("POST", "/v1/generate"),
        ("POST", "/v1/audit"),
        ("POST", "/v1/memory/store?content=test"),
        ("GET", "/v1/memory/search?query=test"),
        ("GET", "/v1/audit/verify?interaction_id=test"),
        ("GET", "/v1/audit/chain?company_id=1"),
        ("GET", "/v1/messages?company_id=1"),
        ("GET", "/v1/procedures?company_id=1"),
        ("POST", "/v1/procedures"),
        ("POST", "/v1/procedures/1/adopt"),
        ("GET", "/v1/support/tickets"),
        ("GET", "/v1/support/tickets/FAB-00001"),
        ("PATCH", "/v1/support/tickets/FAB-00001"),
        ("GET", "/v1/support/logs"),
        ("POST", "/v1/integrations/github/bindings"),
        ("POST", "/v1/integrations/github/backfill"),
        ("GET", "/v1/enterprise/quotas?company_id=1"),
        ("POST", "/v1/enterprise/quotas/1/reset"),
        ("GET", "/v1/analytics/summary?sprint_id=1"),
        ("GET", "/v1/analytics/orchestration_health?company_id=1"),
        ("GET", "/v1/enterprise/lineage?file_path=test"),
        ("POST", "/v1/discover_team"),
        ("POST", "/v1/company/invite"),
        ("GET", "/v1/workspace/status"),
        ("GET", "/v1/workspace/browse"),
        ("GET", "/v1/workspace/inspect?path=/tmp"),
    ],
)
def test_protected_route_requires_token(test_client, method, path):
    response = test_client.request(method, path, json={})
    assert response.status_code == 401
    assert response.headers["www-authenticate"] == "Bearer"


def test_sprint_scope_uses_owning_company(test_client, sqlite_session, identity):
    _member(sqlite_session, identity, "Owner")
    other = Company(company_name="Other tenant")
    sqlite_session.add(other)
    sqlite_session.flush()
    sprint = Sprint(company_id=other.id, sprint_name="Private", start_date="2026-10-17")
    sqlite_session.add(sprint)
    sqlite_session.commit()
    response = test_client.get(f"/v1/sprints/{sprint.id}/board", headers=identity[2])
    assert response.status_code == 403
    assert response.json() == {"detail": "Not a member of this company"}


@pytest.mark.parametrize("role, expected", [("Viewer", 403), ("Manager", 200)])
def test_backlog_and_dispatch_roles(
    test_client, sqlite_session, identity, role, expected
):
    _member(sqlite_session, identity, role)
    sprint = Sprint(
        company_id=identity[1].id, sprint_name="Scoped", start_date="2026-10-17"
    )
    sqlite_session.add(sprint)
    sqlite_session.flush()
    item = BacklogItem(sprint_id=sprint.id, title="Scoped item")
    sqlite_session.add(item)
    sqlite_session.commit()
    updated = test_client.patch(
        f"/v1/backlog/{item.id}", json={"status": "done"}, headers=identity[2]
    )
    dispatched = test_client.post(
        f"/v1/sprints/{sprint.id}/dispatch", headers=identity[2]
    )
    assert updated.status_code == expected
    assert dispatched.status_code == expected
    sqlite_session.refresh(item)
    assert item.status == ("done" if expected == 200 else "todo")


@pytest.mark.parametrize(
    "path", ["/v1/sprints/99999999/board", "/v1/analytics/summary?sprint_id=99999999"]
)
def test_missing_sprint_stays_404(test_client, identity, path):
    response = test_client.get(path, headers=identity[2])
    assert response.status_code == 404
    assert response.json() == {"detail": "Sprint not found"}


def test_missing_backlog_stays_404(test_client, identity):
    response = test_client.patch(
        "/v1/backlog/99999999", json={"status": "done"}, headers=identity[2]
    )
    assert response.status_code == 404
    assert response.json() == {"detail": "Backlog item not found"}


def test_authentication_guard_and_flag_off_role_noop(sqlite_session, monkeypatch):
    with pytest.raises(HTTPException) as exc:
        require_authenticated(None)
    assert exc.value.status_code == 401
    assert exc.value.headers == {"WWW-Authenticate": "Bearer"}
    monkeypatch.setattr(settings, "AUTH_REQUIRED", False)
    assert require_authenticated(None) is None
    assert require_company_role(sqlite_session, None, 99999999, "Owner") is None


@pytest.mark.parametrize("role", [None, "Viewer"])
@pytest.mark.parametrize(
    "path, payload",
    [
        ("/v1/generate", {"story": "Test"}),
        ("/v1/audit", {"action": "test"}),
        ("/v1/memory/store?content=test", None),
        (
            "/v1/integrations/github/bindings",
            {
                "owner": "test",
                "repo": "test",
                "webhook_secret": "test-only",
            },
        ),
        ("/v1/integrations/github/backfill", {"owner": "test", "repo": "test"}),
        ("/v1/plan_sprint", {"prompt": "Test"}),
        ("/v1/plan_self_improvement_sprint", {}),
        (
            "/v1/start_sprint",
            {
                "sprint_name": "Test",
                "start_date": "2026-10-17",
                "backlog_items": [],
            },
        ),
    ],
)
def test_company_mutations_require_manager(
    test_client, sqlite_session, identity, role, path, payload
):
    if role:
        _member(sqlite_session, identity, role)
    company_id = identity[1].id
    # La query se compone aquí: pasar `params=` descarta la que ya lleva `path`
    # en el client de Starlette 1.x, y estos casos prueban el orden entre la
    # validación del cuerpo y la autorización.
    url = f"{path}{'&' if '?' in path else '?'}company_id={company_id}"
    # The body names the company only where its model declares the field;
    # ``POST /v1/audit`` takes it from the query, and an undeclared
    # ``company_id`` in a body is refused with 422 since issue #541
    # (test_mass_assignment.py).
    if payload is not None and path != "/v1/audit":
        payload = {**payload, "company_id": company_id}
    response = test_client.post(url, json=payload, headers=identity[2])
    assert response.status_code == 403
    assert response.json() == {
        "detail": "Requires Manager role" if role else "Not a member of this company"
    }


@pytest.mark.parametrize("role, expected", [("Viewer", 403), ("Manager", 200)])
def test_quota_reset_uses_business_unit_company(
    test_client, sqlite_session, identity, role, expected
):
    _member(sqlite_session, identity, role)
    unit = BusinessUnit(
        company_id=identity[1].id, unit_name="Test", token_quota_used=123
    )
    sqlite_session.add(unit)
    sqlite_session.commit()
    response = test_client.post(
        f"/v1/enterprise/quotas/{unit.id}/reset", headers=identity[2]
    )
    assert response.status_code == expected
    sqlite_session.refresh(unit)
    assert unit.token_quota_used == (0 if expected == 200 else 123)


@pytest.mark.parametrize(
    "path",
    [
        "/v1/enterprise/quotas",
        "/v1/analytics/orchestration_health",
        "/v1/enterprise/lineage?file_path=test",
    ],
)
def test_enterprise_reads_reject_nonmembers(test_client, identity, path):
    url = f"{path}{'&' if '?' in path else '?'}company_id={identity[1].id}"
    response = test_client.get(url, headers=identity[2])
    assert response.status_code == 403
    assert response.json() == {"detail": "Not a member of this company"}
