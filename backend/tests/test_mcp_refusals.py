"""MCP refusals that must be clean and audited (review of PR #635).

A malformed URL is a 400 when saved and a reason when listed, never a 500;
the task worker audits a stdio row it refuses; saving resolves DNS off the
event loop; a host that resolves to a private address when connecting is
audited as a refused start beside the start attempt.
"""

from __future__ import annotations

import asyncio
import secrets
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "app"))

import auth_tokens
from config import settings
from egress_fakes import PUBLIC, FakeResolver
from models.audit import AuditLog
from models.company import Company, CompanyMember, User
from models.mcp import McpServer
from services.platform import egress
from services.tools import mcp_client, mcp_policy, mcp_tools
from sqlmodel import select

ALLOWED = "/opt/mcp/bin/tracker-server"
NAMES = {"mcp.public.test": [PUBLIC], "lan.attacker.test": ["10.1.2.3"]}
MALFORMED = [
    "https://[::1/mcp",
    "https://[::1]x/mcp",
    "https://[not-an-address]/mcp",
    "https://mcp.public.test:port/mcp",
]


@pytest.fixture(autouse=True)
def dns(monkeypatch):
    resolver = FakeResolver(NAMES)
    monkeypatch.setattr(egress, "RESOLVER", resolver)
    monkeypatch.setattr(settings, "MCP_STDIO_ALLOWED_COMMANDS", "")
    monkeypatch.setattr(settings, "MCP_HTTP_ALLOWED_HOSTS", "")
    return resolver


def _member(session, company, role):
    user = User(
        email=f"{role.lower()}-{secrets.token_hex(6)}@example.com",
        full_name=role,
        password_hash="unused",
    )
    session.add(user)
    session.commit()
    session.add(CompanyMember(company_id=company.id, user_id=user.id, human_role=role))
    session.commit()
    token = auth_tokens.create_access_token(user_id=user.id, email=user.email)
    return {"Authorization": f"Bearer {token}"}


@pytest.fixture()
def company(sqlite_session, monkeypatch):
    monkeypatch.setattr(settings, "AUTH_REQUIRED", True)
    key = secrets.token_urlsafe(48)
    monkeypatch.setattr(auth_tokens, "_configured_key", lambda: key)
    company = Company(company_name=f"MCP refusals {secrets.token_hex(3)}")
    sqlite_session.add(company)
    sqlite_session.commit()
    return {"id": company.id, "manager": _member(sqlite_session, company, "Manager")}


def _stored(session, company_id, **fields) -> McpServer:
    row = McpServer(company_id=company_id, **fields)
    session.add(row)
    session.commit()
    session.refresh(row)
    return row


def _actions(session, company_id):
    return [
        (a.action, a.agent_id)
        for a in session.exec(
            select(AuditLog)
            .where(AuditLog.company_id == company_id)
            .where(AuditLog.action.like("mcp_server_start%"))  # type: ignore[attr-defined]
            .order_by(AuditLog.id)
        ).all()
    ]


# -- 1. a malformed URL is a refusal, not a crash ----------------------------


@pytest.mark.parametrize("url", MALFORMED)
def test_a_malformed_url_is_a_policy_refusal(url):
    with pytest.raises(mcp_policy.McpPolicyError, match="not a URL"):
        mcp_policy.check_url(url)
    with pytest.raises(mcp_policy.McpPolicyError, match="not a URL"):
        mcp_policy.pin_url(url)


@pytest.mark.parametrize("url", MALFORMED)
def test_saving_a_malformed_url_is_a_400(test_client, company, url):
    resp = test_client.post(
        "/v1/mcp/servers",
        json={"company_id": company["id"], "mcpServers": {"bad": {"url": url}}},
        headers=company["manager"],
    )
    assert resp.status_code == 400, resp.text
    assert "not a URL" in resp.json()["detail"]


def test_a_stored_malformed_url_still_lists_with_its_refusal(
    test_client, company, sqlite_session
):
    _stored(
        sqlite_session,
        company["id"],
        name="bad",
        transport="http",
        url="https://[::1/mcp",
    )
    listed = test_client.get(
        "/v1/mcp/servers",
        params={"company_id": company["id"]},
        headers=company["manager"],
    )
    assert listed.status_code == 200, listed.text
    assert "not a URL" in listed.json()["servers"][0]["start_refused"]


# -- 2. the task worker audits a stdio row it refuses -----------------------


def test_the_task_worker_audits_a_stdio_row_off_the_allowlist(
    sqlite_session, company, monkeypatch
):
    started = []

    async def fake_list(spec):
        started.append(spec)
        return []

    monkeypatch.setattr(mcp_client, "list_tools", fake_list)
    _stored(
        sqlite_session, company["id"], name="gh", transport="stdio", command=ALLOWED
    )
    box = mcp_tools.Toolbox.load(
        company["id"], "Developer", engine=sqlite_session.get_bind()
    )
    assert box is None and started == []
    assert _actions(sqlite_session, company["id"]) == [
        ("mcp_server_start_refused", "agent:Developer")
    ]


# -- 3. saving resolves DNS off the event loop -------------------------------


def test_saving_a_server_resolves_its_host_off_the_event_loop(
    test_client, company, dns, monkeypatch
):
    on_loop: list[bool] = []

    def resolver(host, port=None, *args, **kwargs):
        try:
            asyncio.get_running_loop()
            on_loop.append(True)
        except RuntimeError:
            on_loop.append(False)
        return dns(host, port, *args, **kwargs)

    monkeypatch.setattr(egress, "RESOLVER", resolver)
    resp = test_client.post(
        "/v1/mcp/servers",
        json={
            "company_id": company["id"],
            "mcpServers": {"docs": {"url": "https://mcp.public.test/mcp"}},
        },
        headers=company["manager"],
    )
    assert resp.status_code == 201, resp.text
    assert on_loop and not any(on_loop)


# -- 4. a private address at connect time is audited as refused --------------


def test_a_host_private_at_connect_time_is_audited_as_a_refused_start(
    test_client, company, sqlite_session
):
    row = _stored(
        sqlite_session,
        company["id"],
        name="lan",
        transport="http",
        url="https://lan.attacker.test/mcp",
    )
    resp = test_client.get(
        f"/v1/mcp/servers/{row.id}/tools", headers=company["manager"]
    )
    assert resp.status_code == 409 and "private" in resp.json()["detail"]
    actions = [action for action, _ in _actions(sqlite_session, company["id"])]
    assert actions == ["mcp_server_start", "mcp_server_start_refused"]
