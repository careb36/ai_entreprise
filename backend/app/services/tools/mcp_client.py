"""Connections to MCP servers, from async code or from a worker thread.

Built on the official ``mcp`` package. A connection spec (``mcp_config.
connection_spec``) names the transport: ``stdio`` spawns the command inside
the backend's process space, ``http`` speaks streamable HTTP, ``sse`` the
older SSE transport; ``inproc`` connects to a server object in the same
process and exists for tests.

Before anything starts, ``connect`` asks ``mcp_policy``: a stdio command must
be on the operator's allowlist (checked again here, since the allowlist may
have shrunk since the server was saved), and an HTTP or SSE URL is resolved
once and refused unless every address is public. The HTTP client then sends
every request to that pinned address, with the host in ``Host`` and TLS SNI
(``PinnedTransport``), and refuses a request for any other host, so neither a
DNS answer that changes after the check nor a redirect can reach the
backend's own network. Environment proxies are not used for these requests.

``list_tools`` and ``call_tool`` open a connection for the one exchange. The
task worker calls tools from a thread without an event loop, so ``Loop``
runs a private loop in a daemon thread and hands results back with a
timeout; a hung server costs the caller the timeout, never the thread.
"""

from __future__ import annotations

import asyncio
import threading
from collections.abc import Callable, Coroutine
from contextlib import asynccontextmanager
from typing import Any

import httpx2
from mcp import Client
from mcp.client.sse import sse_client
from mcp.client.stdio import StdioServerParameters
from mcp.client.streamable_http import streamable_http_client
from mcp.shared._httpx_utils import MCP_DEFAULT_SSE_READ_TIMEOUT, MCP_DEFAULT_TIMEOUT

from services.platform.egress import Target
from services.tools import mcp_policy

CONNECT_TIMEOUT_SECONDS = 20.0
# The transport the pinned client wraps; the tests put an httpx2 mock here.
INNER_TRANSPORT: Callable[[], httpx2.AsyncBaseTransport] | None = None


class McpConnectionError(RuntimeError):
    """The server could not be reached or refused the handshake."""


McpPolicyError = mcp_policy.McpPolicyError


class PinnedTransport(httpx2.AsyncBaseTransport):
    """Sends each request for the target's host to the address that was checked."""

    def __init__(self, target: Target, inner: httpx2.AsyncBaseTransport) -> None:
        self._target = target
        self._inner = inner
        original = httpx2.URL(target.url)
        self._origin = (original.scheme, original.host, original.port)
        self._connect = httpx2.URL(target.connect_url)

    async def handle_async_request(self, request: httpx2.Request) -> httpx2.Response:
        url = request.url
        if (url.scheme, url.host, url.port) != self._origin:
            raise httpx2.ConnectError(
                "refused: a request for a host other than the checked one",
                request=request,
            )
        if self._target.host_header:
            request.url = url.copy_with(host=self._connect.host)
            request.headers["Host"] = self._target.host_header
        if self._target.sni_hostname:
            request.extensions = {
                **request.extensions,
                "sni_hostname": self._target.sni_hostname,
            }
        return await self._inner.handle_async_request(request)

    async def aclose(self) -> None:
        await self._inner.aclose()


def _pinned_client_factory(target: Target):
    """An ``McpHttpClientFactory`` whose clients reach only *target*."""

    def factory(
        headers: dict[str, str] | None = None,
        timeout: httpx2.Timeout | None = None,
        auth: httpx2.Auth | None = None,
    ) -> httpx2.AsyncClient:
        inner = INNER_TRANSPORT() if INNER_TRANSPORT else httpx2.AsyncHTTPTransport()
        kwargs: dict[str, Any] = {
            "timeout": timeout
            or httpx2.Timeout(MCP_DEFAULT_TIMEOUT, read=MCP_DEFAULT_SSE_READ_TIMEOUT),
            "transport": PinnedTransport(target, inner),
            "follow_redirects": False,
            "trust_env": False,
        }
        if headers:
            kwargs["headers"] = headers
        if auth is not None:
            kwargs["auth"] = auth
        return httpx2.AsyncClient(**kwargs)

    return factory


@asynccontextmanager
async def connect(spec: dict[str, Any]):
    """An initialized ``mcp.Client`` for *spec*, closed on exit.

    Raises ``McpPolicyError`` before starting anything the operator does not
    allow, and ``McpConnectionError`` when the server cannot be reached.
    """
    transport = spec.get("transport")
    target: Target | None = None
    if transport == "stdio":
        mcp_policy.check_stdio_spec(spec)
    elif transport in ("http", "sse"):
        target = await asyncio.to_thread(mcp_policy.pin_url, spec.get("url"))
    try:
        if transport == "inproc":
            async with Client(spec["server"]) as client:
                yield client
        elif transport == "stdio":
            params = StdioServerParameters(
                command=spec["command"],
                args=list(spec.get("args") or []),
                env=dict(spec.get("env") or {}) or None,
                cwd=spec.get("cwd"),
            )
            async with Client(params) as client:
                yield client
        elif transport == "sse" and target is not None:
            # ``Client`` enters the transport itself (it takes the context
            # manager, not the streams it yields).
            streams = sse_client(
                target.url,
                headers=dict(spec.get("headers") or {}),
                httpx_client_factory=_pinned_client_factory(target),
            )
            async with Client(streams) as client:
                yield client
        elif transport == "http" and target is not None:
            http = _pinned_client_factory(target)(
                headers=dict(spec.get("headers") or {}) or None
            )
            async with (
                http,
                Client(streamable_http_client(target.url, http_client=http)) as client,
            ):
                yield client
        else:
            raise McpConnectionError(f"unknown transport {transport!r}")
    except McpConnectionError:
        raise
    except (OSError, httpx2.HTTPError, ValueError, RuntimeError) as exc:
        raise McpConnectionError(f"{type(exc).__name__}: {exc}") from exc


def _tool_dict(tool: Any) -> dict[str, Any]:
    annotations = tool.annotations
    return {
        "name": tool.name,
        "description": tool.description or "",
        "input_schema": dict(tool.input_schema or {"type": "object", "properties": {}}),
        "read_only": bool(annotations and annotations.read_only_hint),
        "destructive": bool(annotations and annotations.destructive_hint),
    }


async def list_tools(spec: dict[str, Any]) -> list[dict[str, Any]]:
    """The tools the server advertises right now."""
    tools: list[dict[str, Any]] = []
    async with connect(spec) as client:
        cursor = None
        while True:
            page = await client.list_tools(cursor=cursor)
            tools.extend(_tool_dict(tool) for tool in page.tools)
            cursor = page.next_cursor
            if not cursor:
                break
    return tools


def result_text(result: Any) -> str:
    """The text blocks of a tool result, joined; other blocks named by type."""
    parts = []
    for block in result.content or []:
        text = getattr(block, "text", None)
        if isinstance(text, str):
            parts.append(text)
        else:
            parts.append(f"[{type(block).__name__} omitted]")
    if not parts and result.structured_content is not None:
        import json

        parts.append(json.dumps(result.structured_content, ensure_ascii=False))
    return "\n".join(parts)


async def call_tool(
    spec: dict[str, Any], name: str, arguments: dict[str, Any], timeout: float
) -> dict[str, Any]:
    """One tool call. Returns ``{"text", "is_error"}``; a failure is an error result."""
    async with connect(spec) as client:
        result = await client.call_tool(name, arguments, read_timeout_seconds=timeout)
    return {"text": result_text(result), "is_error": bool(result.is_error)}


class Loop:
    """An event loop in a daemon thread, for sync callers."""

    def __init__(self) -> None:
        self._loop = asyncio.new_event_loop()
        self._thread = threading.Thread(
            target=self._loop.run_forever, name="mcp-loop", daemon=True
        )
        self._thread.start()

    def run(self, coro: Coroutine[Any, Any, Any], timeout: float) -> Any:
        future = asyncio.run_coroutine_threadsafe(coro, self._loop)
        try:
            return future.result(timeout)
        except TimeoutError:
            future.cancel()
            raise

    def close(self) -> None:
        self._loop.call_soon_threadsafe(self._loop.stop)
        self._thread.join(timeout=5)
        if not self._loop.is_running():
            self._loop.close()
