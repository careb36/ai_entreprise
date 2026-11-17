"""
MCP router — the servers a company's agents may call tools on.

Endpoints:
- ``GET    /v1/mcp/servers``             — the company's servers (Viewer; secrets by name only).
- ``POST   /v1/mcp/servers``             — add or replace servers from an ``mcpServers`` map (Manager).
- ``PATCH  /v1/mcp/servers/{id}``        — enable or disable one, change its roles or description (Manager).
- ``DELETE /v1/mcp/servers/{id}``        — remove one (Manager).
- ``GET    /v1/mcp/servers/{id}/tools``  — connect now and list what it advertises (Manager).

A server's secrets (environment, headers) go in with the definition and are
never returned: the list shows their names. Only a Manager changes what the
agents can reach, as with the agents themselves, and only a Manager lists a
server's tools, since that starts it (a stdio server is a process on the
backend). What may be saved or started at all is the operator's
(``services/tools/mcp_policy.py``): a stdio command on
``MCP_STDIO_ALLOWED_COMMANDS``, a URL on a public address. Each start asked
for here is audited before it happens; a refused one too. Listing the
servers starts none of them.
"""

import asyncio
import logging
from typing import Any

from auth_policy import current_user_if_required, require_company_role
from database import get_session
from fastapi import APIRouter, Depends, HTTPException
from models import McpServer, User
from pydantic import Field
from schemas import RequestModel
from services.tools import mcp_client, mcp_config
from sqlmodel import Session, select

router = APIRouter(prefix="/v1", tags=["Agents"])
logger = logging.getLogger(__name__)

DISCOVERY_TIMEOUT_SECONDS = 30.0


class McpServersRequest(RequestModel):
    company_id: int
    mcpServers: dict[str, dict[str, Any]] = Field(
        min_length=1
    )  # the map's standard key


class McpServerPatch(RequestModel):
    enabled: bool | None = None
    roles: list[str] | None = None
    description: str | None = None


def _actor(user: User | None) -> str:
    return user.email if user is not None else "local demo"


def _audit_actor(user: User | None) -> str:
    """``human:<email>``, or an API token's own ``api-token:<id>``."""
    actor = _actor(user)
    return actor if actor.startswith("api-token:") else f"human:{actor}"


def _load(
    session: Session, server_id: int, user: User | None, minimum: str
) -> McpServer:
    server = session.get(McpServer, server_id)
    if server is None:
        raise HTTPException(status_code=404, detail="MCP server not found")
    require_company_role(session, user, server.company_id, minimum)
    return server


@router.get("/mcp/servers")
async def list_servers(
    company_id: int,
    session: Session = Depends(get_session),  # noqa: B008 - FastAPI dependency marker.
    user: User | None = Depends(current_user_if_required),  # noqa: B008 - FastAPI dependency marker.
):
    require_company_role(session, user, company_id, "Viewer")
    rows = session.exec(
        select(McpServer)
        .where(McpServer.company_id == company_id)
        .order_by(McpServer.name)
    ).all()
    return {"servers": [mcp_config.serialize(row) for row in rows]}


@router.post("/mcp/servers", status_code=201)
async def put_servers(
    req: McpServersRequest,
    session: Session = Depends(get_session),  # noqa: B008 - FastAPI dependency marker.
    user: User | None = Depends(current_user_if_required),  # noqa: B008 - FastAPI dependency marker.
):
    """Add the servers in the map; one with a name already taken replaces it."""
    require_company_role(session, user, req.company_id, "Manager")
    try:
        definitions = mcp_config.parse_mcp_servers(req.mcpServers)
    except mcp_config.McpConfigError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    rows = mcp_config.upsert_servers(session, req.company_id, definitions, _actor(user))
    return {"servers": [mcp_config.serialize(row) for row in rows]}


@router.patch("/mcp/servers/{server_id}")
async def patch_server(
    server_id: int,
    req: McpServerPatch,
    session: Session = Depends(get_session),  # noqa: B008 - FastAPI dependency marker.
    user: User | None = Depends(current_user_if_required),  # noqa: B008 - FastAPI dependency marker.
):
    server = _load(session, server_id, user, "Manager")
    if req.enabled and not server.enabled:
        refused = mcp_config.start_refusal(server)
        if refused:
            raise HTTPException(status_code=409, detail=f"{server.name}: {refused}")
    if req.enabled is not None:
        server.enabled = req.enabled
    if req.roles is not None:
        server.roles = [r.strip() for r in req.roles if r.strip()]
    if req.description is not None:
        server.description = req.description.strip() or None
    session.add(server)
    session.commit()
    session.refresh(server)
    return mcp_config.serialize(server)


@router.delete("/mcp/servers/{server_id}", status_code=204)
async def delete_server(
    server_id: int,
    session: Session = Depends(get_session),  # noqa: B008 - FastAPI dependency marker.
    user: User | None = Depends(current_user_if_required),  # noqa: B008 - FastAPI dependency marker.
):
    server = _load(session, server_id, user, "Manager")
    session.delete(server)
    session.commit()


@router.get("/mcp/servers/{server_id}/tools")
async def server_tools(
    server_id: int,
    session: Session = Depends(get_session),  # noqa: B008 - FastAPI dependency marker.
    user: User | None = Depends(current_user_if_required),  # noqa: B008 - FastAPI dependency marker.
):
    """What the server advertises right now: an observation, not its configuration.

    It starts the server, so it takes a Manager, and the start is audited
    before it happens. The answer to a failure names no exception text: the
    reason is in the backend's log.
    """
    server = _load(session, server_id, user, "Manager")
    actor = _audit_actor(user)
    refused = mcp_config.start_refusal(server)
    if refused:
        mcp_config.audit_start(
            session, server, server.company_id, actor, "discovery", refused=refused
        )
        raise HTTPException(status_code=409, detail=f"{server.name}: {refused}")
    try:
        spec = mcp_config.connection_spec(server)
    except Exception as exc:  # A secret this key cannot read is a 409, not a crash.
        raise HTTPException(
            status_code=409,
            detail=f"the server's secrets cannot be read: {type(exc).__name__}",
        ) from exc
    name = server.name
    mcp_config.audit_start(session, server, server.company_id, actor, "discovery")
    try:
        tools = await asyncio.wait_for(
            mcp_client.list_tools(spec), timeout=DISCOVERY_TIMEOUT_SECONDS
        )
    except TimeoutError as exc:
        raise HTTPException(
            status_code=502,
            detail=f"{name} did not answer within {DISCOVERY_TIMEOUT_SECONDS:g}s",
        ) from exc
    except mcp_client.McpPolicyError as exc:  # Refused at connect time (rebinding).
        logger.warning("MCP server %s refused when connecting: %s", name, exc)
        raise HTTPException(status_code=409, detail=f"{name}: {exc}") from exc
    except Exception as exc:  # Logged here; the client gets no exception text.
        logger.warning(
            "MCP server %s could not be reached: %s: %s",
            name,
            type(exc).__name__,
            exc,
        )
        raise HTTPException(
            status_code=502,
            detail=f"{name} could not be reached; the reason is in the backend's log",
        ) from exc
    return {"server": name, "tools": tools}
