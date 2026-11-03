"""Connections to MCP servers, from async code or from a worker thread.

Built on the official ``mcp`` package. A connection spec (``mcp_config.
connection_spec``) names the transport: ``stdio`` spawns the command inside
the backend's process space, ``http`` speaks streamable HTTP, ``sse`` the
older SSE transport; ``inproc`` connects to a server object in the same
process and exists for tests.

``list_tools`` and ``call_tool`` open a connection for the one exchange. The
task worker calls tools from a thread without an event loop, so ``Loop``
runs a private loop in a daemon thread and hands results back with a
timeout; a hung server costs the caller the timeout, never the thread.
"""

from __future__ import annotations

import asyncio
import threading
from collections.abc import Coroutine
from contextlib import asynccontextmanager
from typing import Any

import httpx2
from mcp import Client
from mcp.client.sse import sse_client
from mcp.client.stdio import StdioServerParameters
from mcp.client.streamable_http import streamable_http_client
from mcp.shared._httpx_utils import create_mcp_http_client

CONNECT_TIMEOUT_SECONDS = 20.0


class McpConnectionError(RuntimeError):
    """The server could not be reached or refused the handshake."""


@asynccontextmanager
async def connect(spec: dict[str, Any]):
    """An initialized ``mcp.Client`` for *spec*, closed on exit."""
    transport = spec.get("transport")
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
        elif transport == "sse":
            async with (
                sse_client(
                    spec["url"], headers=dict(spec.get("headers") or {})
                ) as streams,
                Client(streams) as client,  # type: ignore[arg-type]
            ):
                yield client
        elif transport == "http":
            http = create_mcp_http_client(
                headers=dict(spec.get("headers") or {}) or None
            )
            async with (
                streamable_http_client(spec["url"], http_client=http) as streams,
                Client(streams) as client,  # type: ignore[arg-type]
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
