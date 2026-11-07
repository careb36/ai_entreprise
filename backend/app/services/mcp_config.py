"""The MCP servers a company configures, read from and written to the database.

The input is the ``mcpServers`` map every MCP client understands (Claude
Desktop, Cursor, OpenHands): ``{"github": {"command": "npx", "args": [...],
"env": {...}}, "docs": {"url": "https://...", "headers": {...}}}``. A server
declaring ``command`` is a stdio server; one declaring ``url`` speaks
streamable HTTP unless its ``type``/``transport`` says ``sse``. Unknown keys
are dropped, as OpenHands does (``drop_unknown_mcp_server_fields``).

Secrets (``env``, ``headers``) are encrypted with the Fernet key and the API
only ever lists their names (``serialize``); ``connection_spec`` decrypts them
for the client, inside the backend.
"""

from __future__ import annotations

import json
from collections.abc import Mapping
from datetime import UTC, datetime
from typing import Any

import crypto
from models.mcp import MCP_TRANSPORTS, McpServer
from sqlmodel import Session, select

NAME_LIMIT = 64
# Names the built-in toolboxes use for their tools (``<name>__<tool>``).
RESERVED_SERVER_NAMES = {"workspace"}
_TRANSPORT_ALIASES = {
    "streamable-http": "http",
    "streamable_http": "http",
    "shttp": "http",
    "http": "http",
    "sse": "sse",
    "stdio": "stdio",
}


class McpConfigError(ValueError):
    """A server definition the backend refuses, with the reason."""


def _string_map(value: Any, field: str, name: str) -> dict[str, str]:
    if value is None:
        return {}
    if not isinstance(value, Mapping) or not all(
        isinstance(k, str) and isinstance(v, str) for k, v in value.items()
    ):
        raise McpConfigError(f"{name}: '{field}' must map names to strings")
    return dict(value)


def normalize_server(name: str, raw: Any) -> dict[str, Any]:
    """One server from the ``mcpServers`` map, validated and in the row's shape.

    Returns the row fields plus ``env`` and ``headers`` in clear; the caller
    stores those through ``store_secrets``.
    """
    name = (name or "").strip()
    if not name or len(name) > NAME_LIMIT or "__" in name:
        raise McpConfigError(
            f"server name {name!r} must be 1-{NAME_LIMIT} characters without '__'"
        )
    if name.lower() in RESERVED_SERVER_NAMES:
        # The built-in toolboxes (``services/developer_tools.py``) own it.
        raise McpConfigError(f"server name {name!r} is reserved for a built-in toolbox")
    if not isinstance(raw, Mapping):
        raise McpConfigError(f"{name}: a server is an object")
    declared = raw.get("transport", raw.get("type"))
    transport = None
    if declared is not None:
        transport = _TRANSPORT_ALIASES.get(str(declared).strip().lower())
        if transport is None:
            raise McpConfigError(
                f"{name}: unknown transport {declared!r}; use one of {', '.join(MCP_TRANSPORTS)}"
            )
    command = raw.get("command")
    url = raw.get("url")
    if transport is None:
        transport = "stdio" if command else "http"
    if transport == "stdio" and not (isinstance(command, str) and command.strip()):
        raise McpConfigError(f"{name}: a stdio server needs 'command'")
    if transport != "stdio" and not (isinstance(url, str) and url.strip()):
        raise McpConfigError(f"{name}: a {transport} server needs 'url'")
    args = raw.get("args") or []
    if not isinstance(args, list) or not all(isinstance(a, str) for a in args):
        raise McpConfigError(f"{name}: 'args' must be a list of strings")
    roles = raw.get("roles") or []
    if not isinstance(roles, list) or not all(isinstance(r, str) for r in roles):
        raise McpConfigError(f"{name}: 'roles' must be a list of role names")
    timeout = raw.get("timeout")
    if timeout is not None and (
        isinstance(timeout, bool)
        or not isinstance(timeout, (int, float))
        or timeout <= 0
    ):
        raise McpConfigError(f"{name}: 'timeout' must be a positive number of seconds")
    return {
        "name": name,
        "transport": transport,
        "url": url.strip() if transport != "stdio" and isinstance(url, str) else None,
        "command": command.strip() if transport == "stdio" else None,
        "args": list(args) if transport == "stdio" else [],
        "cwd": raw.get("cwd") if isinstance(raw.get("cwd"), str) else None,
        "env": _string_map(raw.get("env"), "env", name),
        "headers": _string_map(raw.get("headers"), "headers", name),
        "roles": roles,
        "enabled": bool(raw.get("enabled", True)),
        "description": raw.get("description")
        if isinstance(raw.get("description"), str)
        else None,
        "timeout_seconds": float(timeout) if timeout is not None else None,
    }


def parse_mcp_servers(payload: Any) -> dict[str, dict[str, Any]]:
    """The ``mcpServers`` map (or a bare map of servers), each one normalized."""
    servers = payload
    if isinstance(payload, Mapping) and "mcpServers" in payload:
        servers = payload.get("mcpServers") or {}
    if not isinstance(servers, Mapping):
        raise McpConfigError("expected an object mapping server names to definitions")
    if not servers:
        raise McpConfigError("no servers given")
    return {name: normalize_server(name, raw) for name, raw in servers.items()}


def store_secrets(env: Mapping[str, str], headers: Mapping[str, str]) -> str | None:
    if not env and not headers:
        return None
    return crypto.encrypt_value(
        json.dumps({"env": dict(env), "headers": dict(headers)})
    )


def load_secrets(stored: str | None) -> dict[str, dict[str, str]]:
    if not stored:
        return {"env": {}, "headers": {}}
    data = json.loads(crypto.decrypt_value(stored))
    return {
        "env": dict(data.get("env") or {}),
        "headers": dict(data.get("headers") or {}),
    }


def secret_names(stored: str | None) -> dict[str, list[str]]:
    """The names of a server's secrets, never their values."""
    try:
        secrets = load_secrets(stored)
    except Exception:  # noqa: BLE001 - A row this key cannot read still lists as a server.
        return {"env_keys": [], "header_names": [], "secrets_readable": False}
    return {
        "env_keys": sorted(secrets["env"]),
        "header_names": sorted(secrets["headers"]),
        "secrets_readable": True,
    }


def apply(server: McpServer, fields: Mapping[str, Any]) -> McpServer:
    """Write a normalized definition onto a row (new or existing)."""
    for key in (
        "name",
        "transport",
        "url",
        "command",
        "args",
        "cwd",
        "roles",
        "enabled",
        "description",
        "timeout_seconds",
    ):
        setattr(server, key, fields[key])
    server.secrets = store_secrets(fields["env"], fields["headers"])
    server.updated_at = datetime.now(UTC).replace(tzinfo=None)
    return server


def upsert_servers(
    session: Session,
    company_id: int,
    definitions: Mapping[str, Mapping[str, Any]],
    actor: str | None,
) -> list[McpServer]:
    """Create or replace, by name, the company's servers in *definitions*."""
    existing = {
        row.name: row
        for row in session.exec(
            select(McpServer).where(McpServer.company_id == company_id)
        ).all()
    }
    rows = []
    for name, fields in definitions.items():
        row = existing.get(name) or McpServer(
            company_id=company_id,
            name=name,
            transport=fields["transport"],
            created_by=actor,
        )
        apply(row, fields)
        session.add(row)
        rows.append(row)
    session.commit()
    for row in rows:
        session.refresh(row)
    return rows


def serialize(server: McpServer) -> dict[str, Any]:
    """The server as the API shows it: configuration in full, secrets by name."""
    return {
        "id": server.id,
        "company_id": server.company_id,
        "name": server.name,
        "transport": server.transport,
        "url": server.url,
        "command": server.command,
        "args": list(server.args or []),
        "cwd": server.cwd,
        "roles": list(server.roles or []),
        "enabled": server.enabled,
        "description": server.description,
        "timeout_seconds": server.timeout_seconds,
        "created_by": server.created_by,
        "created_at": server.created_at.isoformat() if server.created_at else None,
        "updated_at": server.updated_at.isoformat() if server.updated_at else None,
        **secret_names(server.secrets),
    }


def connection_spec(server: McpServer) -> dict[str, Any]:
    """What the client needs to connect, secrets decrypted. Backend-internal."""
    secrets = load_secrets(server.secrets)
    return {
        "name": server.name,
        "transport": server.transport,
        "url": server.url,
        "command": server.command,
        "args": list(server.args or []),
        "cwd": server.cwd,
        "env": secrets["env"],
        "headers": secrets["headers"],
        "timeout_seconds": server.timeout_seconds,
    }


def servers_for_role(
    session: Session, company_id: int, role: str | None
) -> list[McpServer]:
    """The company's enabled servers whose roles include *role* (or name none)."""
    rows = session.exec(
        select(McpServer)
        .where(McpServer.company_id == company_id, McpServer.enabled.is_(True))  # type: ignore[union-attr]
        .order_by(McpServer.name)
    ).all()
    wanted = (role or "").strip().lower()
    return [
        row
        for row in rows
        if not row.roles or wanted in {str(r).strip().lower() for r in row.roles}
    ]
