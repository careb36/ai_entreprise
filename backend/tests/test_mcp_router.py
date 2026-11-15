"""``/v1/mcp/servers``: a Manager configures, a Viewer reads, secrets stay in."""

from __future__ import annotations

import secrets
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "app"))

import auth_tokens
from config import settings
from models.company import Company, CompanyMember, User
from services.tools import mcp_client


@pytest.fixture(autouse=True)
def auth_on(monkeypatch):
    monkeypatch.setattr(settings, "AUTH_REQUIRED", True)
    key = secrets.token_urlsafe(48)
    monkeypatch.setattr(auth_tokens, "_configured_key", lambda: key)


def _member(session, company, role):
    user = User(
        email=f"{role.lower()}-{secrets.token_hex(6)}@example.com",
        full_name=role,
        password_hash="unused",
    )
    session.add(user)
    session.commit()
    if company is not None:
        session.add(
            CompanyMember(company_id=company.id, user_id=user.id, human_role=role)
        )
        session.commit()
    token = auth_tokens.create_access_token(user_id=user.id, email=user.email)
    return {"Authorization": f"Bearer {token}"}


@pytest.fixture()
def company(sqlite_session):
    company = Company(company_name=f"MCP {secrets.token_hex(3)}")
    sqlite_session.add(company)
    sqlite_session.commit()
    return {
        "id": company.id,
        "manager": _member(sqlite_session, company, "Manager"),
        "viewer": _member(sqlite_session, company, "Viewer"),
        "outsider": _member(sqlite_session, None, "Owner"),
    }


_DEFINITION = {
    "docs": {
        "url": "https://docs.example/mcp",
        "headers": {"Authorization": "Bearer placeholder-qz"},
    },
    "gh": {
        "command": "npx",
        "args": ["server-github"],
        "env": {"GITHUB_TOKEN": "ghp_secret"},
    },
}


def test_a_manager_adds_servers_and_the_answer_never_carries_a_secret(
    test_client, company
):
    resp = test_client.post(
        "/v1/mcp/servers",
        json={"company_id": company["id"], "mcpServers": _DEFINITION},
        headers=company["manager"],
    )
    assert resp.status_code == 201, resp.text
    body = resp.json()
    assert [s["name"] for s in body["servers"]] == ["docs", "gh"]
    assert "placeholder-qz" not in resp.text and "ghp_secret" not in resp.text
    docs = body["servers"][0]
    assert docs["header_names"] == ["Authorization"] and docs["transport"] == "http"
    assert body["servers"][1]["env_keys"] == ["GITHUB_TOKEN"] and body["servers"][1][
        "created_by"
    ].startswith("manager-")

    listed = test_client.get(
        "/v1/mcp/servers",
        params={"company_id": company["id"]},
        headers=company["viewer"],
    )
    assert listed.status_code == 200 and [
        s["name"] for s in listed.json()["servers"]
    ] == ["docs", "gh"]
    assert "ghp_secret" not in listed.text

    # The same name replaces the server; a new definition without secrets drops them.
    again = test_client.post(
        "/v1/mcp/servers",
        json={
            "company_id": company["id"],
            "mcpServers": {"docs": {"url": "https://docs2.example/mcp"}},
        },
        headers=company["manager"],
    )
    assert again.status_code == 201
    assert again.json()["servers"][0]["url"] == "https://docs2.example/mcp"
    assert again.json()["servers"][0]["header_names"] == []
    assert (
        len(
            test_client.get(
                "/v1/mcp/servers",
                params={"company_id": company["id"]},
                headers=company["viewer"],
            ).json()["servers"]
        )
        == 2
    )


def test_a_bad_definition_is_a_400_with_the_reason(test_client, company):
    resp = test_client.post(
        "/v1/mcp/servers",
        json={"company_id": company["id"], "mcpServers": {"x": {"transport": "stdio"}}},
        headers=company["manager"],
    )
    assert resp.status_code == 400 and "needs 'command'" in resp.json()["detail"]


def test_a_viewer_reads_but_cannot_change_and_an_outsider_sees_nothing(
    test_client, company
):
    created = test_client.post(
        "/v1/mcp/servers",
        json={"company_id": company["id"], "mcpServers": _DEFINITION},
        headers=company["manager"],
    ).json()["servers"]
    server_id = created[0]["id"]
    body = {"company_id": company["id"], "mcpServers": _DEFINITION}
    for headers, expected in (
        (company["viewer"], 403),
        (company["outsider"], 403),
        ({}, 401),
    ):
        assert (
            test_client.post("/v1/mcp/servers", json=body, headers=headers).status_code
            == expected
        )
        assert (
            test_client.patch(
                f"/v1/mcp/servers/{server_id}", json={"enabled": False}, headers=headers
            ).status_code
            == expected
        )
        assert (
            test_client.delete(
                f"/v1/mcp/servers/{server_id}", headers=headers
            ).status_code
            == expected
        )
    assert (
        test_client.get(
            "/v1/mcp/servers",
            params={"company_id": company["id"]},
            headers=company["outsider"],
        ).status_code
        == 403
    )
    assert (
        test_client.get(
            f"/v1/mcp/servers/{server_id}/tools", headers=company["outsider"]
        ).status_code
        == 403
    )
    assert (
        test_client.get(
            "/v1/mcp/servers/999999/tools", headers=company["manager"]
        ).status_code
        == 404
    )


def test_a_manager_disables_restricts_and_removes_a_server(test_client, company):
    created = test_client.post(
        "/v1/mcp/servers",
        json={"company_id": company["id"], "mcpServers": _DEFINITION},
        headers=company["manager"],
    ).json()["servers"]
    server_id = created[1]["id"]
    patched = test_client.patch(
        f"/v1/mcp/servers/{server_id}",
        json={"enabled": False, "roles": ["QA", " "], "description": "tracker"},
        headers=company["manager"],
    )
    assert patched.status_code == 200
    assert (
        patched.json()["enabled"],
        patched.json()["roles"],
        patched.json()["description"],
    ) == (False, ["QA"], "tracker")
    assert (
        test_client.delete(
            f"/v1/mcp/servers/{server_id}", headers=company["manager"]
        ).status_code
        == 204
    )
    names = [
        s["name"]
        for s in test_client.get(
            "/v1/mcp/servers",
            params={"company_id": company["id"]},
            headers=company["manager"],
        ).json()["servers"]
    ]
    assert names == ["docs"]


def test_the_tools_endpoint_reports_what_the_server_advertises_or_why_not(
    test_client, company, monkeypatch
):
    created = test_client.post(
        "/v1/mcp/servers",
        json={"company_id": company["id"], "mcpServers": _DEFINITION},
        headers=company["manager"],
    ).json()["servers"]
    server_id = created[0]["id"]
    seen = {}

    async def fake_list(spec):
        seen.update(spec)
        return [
            {
                "name": "search",
                "description": "d",
                "input_schema": {"type": "object"},
                "read_only": True,
                "destructive": False,
            }
        ]

    monkeypatch.setattr(mcp_client, "list_tools", fake_list)
    resp = test_client.get(
        f"/v1/mcp/servers/{server_id}/tools", headers=company["viewer"]
    )
    assert resp.status_code == 200
    assert resp.json() == {
        "server": "docs",
        "tools": [
            {
                "name": "search",
                "description": "d",
                "input_schema": {"type": "object"},
                "read_only": True,
                "destructive": False,
            }
        ],
    }
    # The connection carried the decrypted header, and the answer did not.
    assert (
        seen["headers"] == {"Authorization": "Bearer placeholder-qz"}
        and "placeholder-qz" not in resp.text
    )

    async def failing(spec):
        raise mcp_client.McpConnectionError("ConnectError: refused")

    monkeypatch.setattr(mcp_client, "list_tools", failing)
    resp = test_client.get(
        f"/v1/mcp/servers/{server_id}/tools", headers=company["viewer"]
    )
    assert (
        resp.status_code == 502
        and "docs could not be reached: ConnectError: refused" in resp.json()["detail"]
    )
