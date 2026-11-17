"""Who may start an MCP server, and where an HTTP one may connect (WSTG review).

stdio servers are off unless the operator names the command, by its absolute
path, in ``MCP_STDIO_ALLOWED_COMMANDS``; a server's tools are listed only by
a Manager, since listing starts it; a stored row the allowlist no longer
covers is never started; an HTTP server's URL must be public https, checked
when saved and again, pinned, when connecting; a failure answers no
exception text. The refusals come first, then what is allowed.
"""

from __future__ import annotations

import asyncio
import logging
import os
import secrets
import sys
from pathlib import Path

import httpx2
import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "app"))

import auth_tokens
from config import settings
from egress_fakes import PUBLIC, FakeResolver
from mcp.server.mcpserver import MCPServer
from mcp.server.transport_security import TransportSecuritySettings
from models.audit import AuditLog
from models.company import Company, CompanyMember, User
from models.mcp import McpServer
from services.platform import egress
from services.tools import mcp_client, mcp_config, mcp_policy, mcp_tools
from sqlmodel import select

ALLOWED = "/opt/mcp/bin/tracker-server"
NAMES = {
    "mcp.public.test": [PUBLIC],
    "metadata.attacker.test": ["169.254.169.254"],
    "lan.attacker.test": ["10.1.2.3"],
    "mixed.attacker.test": [[PUBLIC, "127.0.0.1"]],
    "rebind.attacker.test": [PUBLIC, "127.0.0.1"],
    "plain.internal.test": ["10.9.8.7"],
    "plain.public.test": [PUBLIC],
}


@pytest.fixture(autouse=True)
def dns(monkeypatch):
    resolver = FakeResolver(NAMES)
    monkeypatch.setattr(egress, "RESOLVER", resolver)
    return resolver


@pytest.fixture(autouse=True)
def no_operator_settings(monkeypatch):
    monkeypatch.setattr(settings, "MCP_STDIO_ALLOWED_COMMANDS", "")
    monkeypatch.setattr(settings, "MCP_HTTP_ALLOWED_HOSTS", "")


@pytest.fixture()
def allow(monkeypatch):
    def _allow(*paths: str) -> None:
        monkeypatch.setattr(settings, "MCP_STDIO_ALLOWED_COMMANDS", ",".join(paths))

    return _allow


@pytest.fixture()
def started(monkeypatch):
    """Every ``list_tools`` the code asks for: nothing may run unless listed here."""
    calls: list[dict] = []

    async def fake_list(spec):
        calls.append(spec)
        return [{"name": "search", "description": "", "input_schema": {}}]

    monkeypatch.setattr(mcp_client, "list_tools", fake_list)
    return calls


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
    company = Company(company_name=f"MCP sec {secrets.token_hex(3)}")
    sqlite_session.add(company)
    sqlite_session.commit()
    return {
        "id": company.id,
        "manager": _member(sqlite_session, company, "Manager"),
        "viewer": _member(sqlite_session, company, "Viewer"),
    }


def _post(client, company, servers):
    body = {"company_id": company["id"], "mcpServers": servers}
    return client.post("/v1/mcp/servers", json=body, headers=company["manager"])


def _stored(session, company_id, **fields) -> McpServer:
    """A row written straight to the database, as one saved before this change."""
    row = McpServer(company_id=company_id, name=fields.pop("name", "old"), **fields)
    session.add(row)
    session.commit()
    session.refresh(row)
    return row


def _audit_rows(session, company_id):
    return session.exec(
        select(AuditLog)
        .where(AuditLog.company_id == company_id)
        .where(AuditLog.action.like("mcp_server_start%"))  # type: ignore[attr-defined]
        .order_by(AuditLog.id)
    ).all()


# -- stdio: refused ----------------------------------------------------------


def test_stdio_with_an_empty_allowlist_is_refused_and_nothing_is_saved(
    test_client, company, sqlite_session
):
    with pytest.raises(mcp_policy.McpPolicyError, match="disabled"):
        mcp_policy.check_stdio(ALLOWED)
    resp = _post(test_client, company, {"gh": {"command": ALLOWED}})
    assert resp.status_code == 400
    assert "MCP_STDIO_ALLOWED_COMMANDS" in resp.json()["detail"]
    assert sqlite_session.exec(select(McpServer)).all() == []


@pytest.mark.parametrize(
    "command",
    [
        "tracker-server",  # a bare name, looked up on PATH
        "npx",
        "bin/tracker-server",  # relative
        "./tracker-server",
        "/opt/mcp/bin/../bin/tracker-server",
        "/opt/mcp/../mcp/bin/tracker-server",
        "/opt/mcp//bin/tracker-server",
        "/opt/mcp/bin/./tracker-server",
        "/opt/mcp/bin/tracker-server/",
        "/OPT/MCP/BIN/TRACKER-SERVER",
        "/opt/mcp/bin/Tracker-Server",
        "/opt/mcp/bin/tracker-server\x00",
        "/opt/mcp/bin/tracker-server-evil",
        "/bin/sh",
    ],
)
def test_a_command_not_exactly_on_the_list_is_refused(allow, command):
    allow(ALLOWED)
    with pytest.raises(mcp_policy.McpPolicyError):
        mcp_policy.check_stdio(command)
    with pytest.raises(mcp_config.McpConfigError):
        mcp_config.normalize_server("x", {"transport": "stdio", "command": command})


def test_a_symlink_to_an_allowed_program_under_another_name_is_refused(allow, tmp_path):
    real = tmp_path / "server"
    real.write_text("#!/bin/sh\n")
    real.chmod(0o755)
    link = tmp_path / "link"
    link.symlink_to(real)
    allow(str(real))
    with pytest.raises(mcp_policy.McpPolicyError, match="not on"):
        mcp_policy.check_stdio(str(link))
    assert mcp_policy.check_stdio(str(real)) == str(real)


def test_entries_that_are_not_absolute_normalized_paths_allow_nothing(allow):
    allow("npx", "bin/server", "/opt/../opt/server")
    assert mcp_policy.allowed_commands() == frozenset()
    with pytest.raises(mcp_policy.McpPolicyError, match="disabled"):
        mcp_policy.check_stdio("npx")


@pytest.mark.parametrize(
    "env_name",
    [
        "LD_PRELOAD",
        "DYLD_INSERT_LIBRARIES",
        "PATH",
        "PYTHONPATH",
        "NODE_OPTIONS",
        "BASH_ENV",
        "GIT_SSH_COMMAND",
        "JAVA_TOOL_OPTIONS",
        "ld_preload",
        "BAD-NAME",
    ],
)
def test_an_environment_variable_that_loads_other_code_is_refused(allow, env_name):
    allow(ALLOWED)
    with pytest.raises(mcp_config.McpConfigError, match="env"):
        mcp_config.normalize_server(
            "x", {"command": ALLOWED, "env": {env_name: "/tmp/evil.so"}}
        )


@pytest.mark.parametrize(
    "extra",
    [
        {"args": ["a"] * (mcp_policy.MAX_ARGS + 1)},
        {"args": ["x" * (mcp_policy.MAX_ARG_CHARS + 1)]},
        {"args": ["ok", "nul\x00byte"]},
        {"cwd": "relative/dir"},
        {"cwd": "/srv/../etc"},
    ],
)
def test_bad_arguments_or_working_directory_are_refused(allow, extra):
    allow(ALLOWED)
    with pytest.raises(mcp_config.McpConfigError):
        mcp_config.normalize_server("x", {"command": ALLOWED, **extra})


def test_a_viewer_listing_tools_gets_403_and_nothing_runs(
    test_client, company, sqlite_session, started, allow
):
    allow(ALLOWED)
    stdio = _stored(
        sqlite_session, company["id"], name="gh", transport="stdio", command=ALLOWED
    )
    http = _stored(
        sqlite_session,
        company["id"],
        name="docs",
        transport="http",
        url="https://mcp.public.test/mcp",
    )
    for row in (stdio, http):
        resp = test_client.get(
            f"/v1/mcp/servers/{row.id}/tools", headers=company["viewer"]
        )
        assert resp.status_code == 403
    assert started == [] and _audit_rows(sqlite_session, company["id"]) == []


def test_a_stored_stdio_row_the_allowlist_does_not_cover_never_starts(
    test_client, company, sqlite_session, started, monkeypatch, allow
):
    allow(ALLOWED)
    row = _stored(
        sqlite_session,
        company["id"],
        name="legacy",
        transport="stdio",
        command="npx",
        args=["-y", "some-server"],
    )
    # Listing the servers names the refusal and starts nothing.
    listed = test_client.get(
        "/v1/mcp/servers",
        params={"company_id": company["id"]},
        headers=company["viewer"],
    )
    assert listed.status_code == 200
    assert "MCP_STDIO_ALLOWED_COMMANDS" in listed.json()["servers"][0]["start_refused"]
    # A Manager asking for its tools gets the reason, audited, and no start.
    resp = test_client.get(
        f"/v1/mcp/servers/{row.id}/tools", headers=company["manager"]
    )
    assert resp.status_code == 409 and "MCP_STDIO_ALLOWED_COMMANDS" in resp.text
    audit = _audit_rows(sqlite_session, company["id"])
    assert [a.action for a in audit] == ["mcp_server_start_refused"]
    assert audit[0].agent_id.startswith("human:manager-")
    # Turning it back on is refused too.
    sqlite_session.refresh(row)
    row.enabled = False
    sqlite_session.add(row)
    sqlite_session.commit()
    patched = test_client.patch(
        f"/v1/mcp/servers/{row.id}", json={"enabled": True}, headers=company["manager"]
    )
    assert patched.status_code == 409
    # The task worker leaves it out and says why.
    row.enabled = True
    sqlite_session.add(row)
    sqlite_session.commit()
    box = mcp_tools.Toolbox.load(
        company["id"], "Developer", engine=sqlite_session.get_bind()
    )
    assert box is None
    assert started == []


def test_a_row_saved_while_allowed_is_refused_once_the_operator_removes_it(
    sqlite_session, company, started, allow
):
    allow(ALLOWED)
    rows = mcp_config.upsert_servers(
        sqlite_session,
        company["id"],
        mcp_config.parse_mcp_servers({"gh": {"command": ALLOWED}}),
        "m",
    )
    assert mcp_config.start_refusal(rows[0]) is None
    allow()
    assert "disabled" in mcp_config.start_refusal(rows[0])
    spec = mcp_config.connection_spec(rows[0])

    with pytest.raises(mcp_policy.McpPolicyError):
        asyncio.run(_connect_once(spec))


async def _connect_once(spec):
    async with mcp_client.connect(spec):
        raise AssertionError("connected")


def test_a_toolbox_without_an_audit_never_starts_a_stdio_server(allow, started):
    allow(ALLOWED)
    box = mcp_tools.Toolbox([{"name": "gh", "transport": "stdio", "command": ALLOWED}])
    assert box.schemas() == [] and "audited" in box.unavailable["gh"]
    assert started == []


# -- HTTP: refused -----------------------------------------------------------


@pytest.mark.parametrize(
    "host",
    [
        "127.0.0.1",
        "127.1",
        "2130706433",
        "[::1]",
        "0.0.0.0",
        "169.254.169.254",
        "[::ffff:169.254.169.254]",
        "[fe80::1]",
        "10.0.0.5",
        "172.16.0.1",
        "192.168.1.1",
        "100.64.0.1",
        "localhost",
        "metadata.attacker.test",
        "lan.attacker.test",
        "mixed.attacker.test",
        "unknown.invalid",
    ],
)
def test_an_http_url_on_a_non_public_address_is_refused_when_saved(
    test_client, company, host, dns, monkeypatch
):
    monkeypatch.setattr(dns, "default", None)  # an unknown name does not resolve
    resp = _post(test_client, company, {"docs": {"url": f"https://{host}/mcp"}})
    assert resp.status_code == 400, host
    assert "private" in resp.json()["detail"]


@pytest.mark.parametrize(
    "url, reason",
    [
        ("http://mcp.public.test/mcp", "must be https"),
        ("ftp://mcp.public.test/mcp", "https"),
        ("file:///etc/passwd", "URL"),
        ("https://user:pw@mcp.public.test/mcp", "credentials"),
        ("https://mcp.public.test:99999/mcp", "URL"),
        ("https:// mcp.public.test/mcp", "URL"),
    ],
)
def test_an_http_url_must_be_https_without_credentials(url, reason):
    with pytest.raises(mcp_policy.McpPolicyError, match=reason):
        mcp_policy.check_url(url)


def test_http_for_a_named_host_still_needs_a_public_address(monkeypatch):
    monkeypatch.setattr(
        settings, "MCP_HTTP_ALLOWED_HOSTS", "plain.public.test, plain.internal.test"
    )
    assert mcp_policy.check_url("http://plain.public.test/mcp")
    with pytest.raises(mcp_policy.McpPolicyError, match="private"):
        mcp_policy.check_url("http://plain.internal.test/mcp")
    with pytest.raises(mcp_policy.McpPolicyError, match="https"):
        mcp_policy.check_url("http://mcp.public.test/mcp")


class _Recorder(httpx2.AsyncBaseTransport):
    """Records each request the pinned client sends; *inner* answers it."""

    def __init__(self, inner=None):
        self.inner = inner
        self.requests: list[tuple[str, str | None, str | None]] = []

    async def handle_async_request(self, request):
        self.requests.append(
            (
                str(request.url),
                request.headers.get("host"),
                request.extensions.get("sni_hostname"),
            )
        )
        if self.inner is None:
            return httpx2.Response(500)
        return await self.inner.handle_async_request(request)


def test_a_name_that_rebinds_to_loopback_after_the_save_is_refused_when_connecting(
    sqlite_session, company, monkeypatch
):
    recorder = _Recorder()
    monkeypatch.setattr(mcp_client, "INNER_TRANSPORT", lambda: recorder)
    rows = mcp_config.upsert_servers(
        sqlite_session,
        company["id"],
        mcp_config.parse_mcp_servers({"r": {"url": "https://rebind.attacker.test/m"}}),
        "m",
    )  # the first answer was public
    spec = mcp_config.connection_spec(rows[0])

    with pytest.raises(mcp_policy.McpPolicyError, match="private"):
        asyncio.run(_connect_once(spec))
    assert recorder.requests == []


def test_a_stored_private_url_is_never_connected_to(
    monkeypatch, sqlite_session, company
):
    recorder = _Recorder()
    monkeypatch.setattr(mcp_client, "INNER_TRANSPORT", lambda: recorder)
    _stored(
        sqlite_session,
        company["id"],
        name="meta",
        transport="http",
        url="https://169.254.169.254/latest",
    )
    box = mcp_tools.Toolbox.load(
        company["id"], "Developer", engine=sqlite_session.get_bind()
    )
    assert box is not None and box.schemas() == []
    assert "private" in box.unavailable["meta"] and recorder.requests == []


def test_the_pinned_client_refuses_a_request_for_another_host():
    target = egress.pin("https://mcp.public.test/mcp")
    recorder = _Recorder()
    transport = mcp_client.PinnedTransport(target, recorder)

    async def send(url):
        async with httpx2.AsyncClient(transport=transport) as client:
            return await client.get(url)

    with pytest.raises(httpx2.ConnectError):
        asyncio.run(send("https://169.254.169.254/latest/meta-data"))
    with pytest.raises(httpx2.ConnectError):
        asyncio.run(send("http://mcp.public.test/mcp"))  # another scheme
    assert recorder.requests == []


def test_a_502_names_no_exception_text_and_the_log_keeps_it(
    test_client, company, sqlite_session, monkeypatch, caplog
):
    row = _stored(
        sqlite_session,
        company["id"],
        name="docs",
        transport="http",
        url="https://mcp.public.test/mcp",
    )

    async def failing(spec):
        raise mcp_client.McpConnectionError(
            "ConnectError: [Errno 111] refused by internal-db.lan:5432"
        )

    monkeypatch.setattr(mcp_client, "list_tools", failing)
    with caplog.at_level(logging.WARNING):
        resp = test_client.get(
            f"/v1/mcp/servers/{row.id}/tools", headers=company["manager"]
        )
    assert resp.status_code == 502
    assert "internal-db" not in resp.text and "Errno" not in resp.text
    assert "ConnectError" not in resp.text
    assert resp.json()["detail"].startswith("docs could not be reached")
    assert "internal-db.lan" in caplog.text


# -- allowed -----------------------------------------------------------------

_ECHO_SERVER = """from mcp.server.mcpserver import MCPServer
srv = MCPServer("echo")
@srv.tool()
def echo(text: str) -> str:
    return text
srv.run()
"""


def test_an_allowed_command_runs_listed_by_a_manager_and_audited(
    test_client, company, sqlite_session, allow, tmp_path
):
    script = tmp_path / "echo_server.py"
    script.write_text(_ECHO_SERVER)
    python = os.path.normpath(sys.executable)
    allow(python)
    # The argument is never read by a shell: it reaches the program as it is.
    created = _post(
        test_client,
        company,
        {"echo": {"command": python, "args": [str(script), "; touch /tmp/x"]}},
    )
    assert created.status_code == 201, created.text
    server = created.json()["servers"][0]
    assert server["start_refused"] is None
    assert server["args"] == [str(script), "; touch /tmp/x"]
    resp = test_client.get(
        f"/v1/mcp/servers/{server['id']}/tools", headers=company["manager"]
    )
    assert resp.status_code == 200, resp.text
    assert [t["name"] for t in resp.json()["tools"]] == ["echo"]
    audit = _audit_rows(sqlite_session, company["id"])
    assert [a.action for a in audit] == ["mcp_server_start"]
    assert audit[0].result["command"] == python
    assert audit[0].details["purpose"] == "discovery"


def test_the_task_worker_audits_each_start_of_an_allowed_stdio_server(
    sqlite_session, company, allow, started
):
    allow(ALLOWED)
    mcp_config.upsert_servers(
        sqlite_session,
        company["id"],
        mcp_config.parse_mcp_servers(
            {"gh": {"command": ALLOWED, "env": {"TRACKER_TOKEN": "placeholder"}}}
        ),
        "m",
    )
    box = mcp_tools.Toolbox.load(
        company["id"], "Developer", engine=sqlite_session.get_bind()
    )
    assert box is not None and [s["function"]["name"] for s in box.schemas()] == [
        "gh__search"
    ]
    box.close()
    audit = _audit_rows(sqlite_session, company["id"])
    assert [(a.action, a.agent_id) for a in audit] == [
        ("mcp_server_start", "agent:Developer")
    ]
    assert "placeholder" not in str(audit[0].result) + str(audit[0].details)
    assert len(started) == 1


def test_a_public_https_server_works_through_the_pinned_address(
    sqlite_session, company, monkeypatch
):
    srv = MCPServer("docs")

    @srv.tool()
    def lookup(term: str) -> str:
        """Look a term up."""
        return term

    app = srv.streamable_http_app(
        json_response=True,
        stateless_http=True,
        transport_security=TransportSecuritySettings(
            enable_dns_rebinding_protection=False
        ),
    )
    recorder = _Recorder(httpx2.ASGITransport(app=app))
    monkeypatch.setattr(mcp_client, "INNER_TRANSPORT", lambda: recorder)
    rows = mcp_config.upsert_servers(
        sqlite_session,
        company["id"],
        mcp_config.parse_mcp_servers({"docs": {"url": "https://mcp.public.test/mcp"}}),
        "m",
    )
    spec = mcp_config.connection_spec(rows[0])

    async def run():
        async with srv.session_manager.run():
            return await mcp_client.list_tools(spec)

    tools = asyncio.run(run())
    assert [t["name"] for t in tools] == ["lookup"]
    assert recorder.requests
    assert all(
        r == (f"https://{PUBLIC}/mcp", "mcp.public.test", "mcp.public.test")
        for r in recorder.requests
    )
