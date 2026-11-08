"""A company's own provider connection, sent by the backend for one request.

The backend (``services/provider_connections.py``) stores each company's
provider keys encrypted and, when an agent uses a model of one of them, sends
the connection with the request in ``X-Provider-Connection`` (base64 JSON:
``base_url``, ``api_key``, ``headers``, ``name``). The proxy then calls that
OpenAI-compatible endpoint with that key instead of its own, and the company's
token budget still applies (``X-Business-Unit-ID``).

The header is honoured only with ``X-Internal-Token`` equal to
``LLM_PROXY_INTERNAL_TOKEN``, a secret the backend and the proxy share: the
proxy's port can be reached from outside the backend, and without that check
anyone reaching it could make it call any URL with any key. With no token
configured, connections are refused. The key never reaches a log line or a
response.
"""

from __future__ import annotations

import base64
import hmac
import json
import os
from urllib.parse import urlparse

from upstream import UpstreamError
from usage import openai_reported_count, openai_text, with_usage

CONNECTION_HEADER = "X-Provider-Connection"
TOKEN_HEADER = "X-Internal-Token"


class ConnectionRefused(Exception):
    """A connection header the proxy will not use; the message says why."""


def configured_token() -> str:
    return os.getenv("LLM_PROXY_INTERNAL_TOKEN", "").strip()


def from_headers(headers) -> dict | None:
    """The connection the request carries, or ``None``; raises when refused."""
    raw = headers.get(CONNECTION_HEADER)
    if not raw:
        return None
    token = configured_token()
    if not token:
        raise ConnectionRefused("company connections are disabled: LLM_PROXY_INTERNAL_TOKEN is not set")
    if not hmac.compare_digest(str(headers.get(TOKEN_HEADER) or ""), token):
        raise ConnectionRefused("the internal token is missing or wrong")
    try:
        conn = json.loads(base64.b64decode(raw, validate=True).decode("utf-8"))
    except (ValueError, UnicodeDecodeError) as exc:
        raise ConnectionRefused("the connection header is not base64 JSON") from exc
    if not isinstance(conn, dict):
        raise ConnectionRefused("the connection header is not an object")
    base_url = str(conn.get("base_url") or "").rstrip("/")
    parsed = urlparse(base_url)
    if parsed.scheme not in ("http", "https") or not parsed.netloc:
        raise ConnectionRefused("the connection's base_url is not an http(s) URL")
    extra = conn.get("headers") or {}
    if not isinstance(extra, dict):
        raise ConnectionRefused("the connection's headers are not an object")
    return {
        "name": str(conn.get("name") or parsed.netloc),
        "base_url": base_url,
        "api_key": str(conn.get("api_key") or ""),
        "headers": {str(k): str(v) for k, v in extra.items()},
    }


def request_headers(conn: dict) -> dict[str, str]:
    out = dict(conn["headers"])
    if conn["api_key"]:
        out["Authorization"] = f"Bearer {conn['api_key']}"
    return out


def call(conn: dict, model: str, data: dict, post, timeout: float) -> tuple[dict, int]:
    """One chat completion on the connection's endpoint with its key."""
    status, result = post(
        conn["name"], model, f"{conn['base_url']}/chat/completions",
        request_headers(conn), {**data, "model": model}, timeout,
    )
    if not result.get("choices"):
        raise UpstreamError(conn["name"], model, status, "response contained no choices", retriable=False)
    return with_usage(result, openai_reported_count(result), data.get("messages", []), openai_text(result))
