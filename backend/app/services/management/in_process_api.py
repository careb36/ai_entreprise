"""The platform's own API, called in-process as the Owner (``docs/design/owner-actions.md``).

An owner action runs the self-hosting harness's steps (``scripts/self_host``),
which speak to the public API through an ``Api`` object (``get``, ``post``,
``put``, ``patch``, raising the harness's ``ApiError``). ``InProcessApi`` is
that object without a socket and without a token: each call is an ASGI
request to the running application, on the application's own event loop,
with an ``InProcessPrincipal`` in its scope (``services/platform/
in_process_principal.py``). The harness runs in a worker thread; each call is
handed to the loop and awaited there, so the routes keep their NATS client
and database sessions.

Before each call ``before_call`` runs (the job's cancel and budget checks);
it raises to stop the run between two calls.
"""

from __future__ import annotations

import asyncio
import json
from collections.abc import Callable
from typing import Any
from urllib.parse import urlsplit

from services.platform.in_process_principal import SCOPE_KEY, InProcessPrincipal

CALL_TIMEOUT_SECONDS = 600


async def asgi_request(
    app: Any,
    principal: InProcessPrincipal,
    method: str,
    path: str,
    body: Any = None,
) -> tuple[int, bytes]:
    """One request to *app* as *principal*: the status and the body's bytes."""
    url = urlsplit(path)
    payload = json.dumps(body).encode() if body is not None else b""
    scope = {
        "type": "http",
        "asgi": {"version": "3.0", "spec_version": "2.3"},
        "http_version": "1.1",
        "method": method.upper(),
        "scheme": "http",
        "path": url.path,
        "raw_path": url.path.encode(),
        "root_path": "",
        "query_string": url.query.encode(),
        "headers": [
            (b"host", b"in-process"),
            (b"content-type", b"application/json"),
            (b"content-length", str(len(payload)).encode()),
        ],
        "client": ("in-process", 0),
        "server": ("in-process", 80),
        "state": {},
        SCOPE_KEY: principal,
    }
    sent = False
    status = 500
    chunks: list[bytes] = []

    async def receive() -> dict:
        nonlocal sent
        if not sent:
            sent = True
            return {"type": "http.request", "body": payload, "more_body": False}
        await asyncio.sleep(3600)  # never disconnects while the app answers
        return {"type": "http.disconnect"}

    async def send(message: dict) -> None:
        nonlocal status
        if message["type"] == "http.response.start":
            status = int(message["status"])
        elif message["type"] == "http.response.body":
            chunks.append(message.get("body", b""))

    await app(scope, receive, send)
    return status, b"".join(chunks)


class InProcessApi:
    """The harness's ``Api`` over the running application (called from a thread)."""

    def __init__(
        self,
        app: Any,
        principal: InProcessPrincipal,
        loop: asyncio.AbstractEventLoop,
        error: type[Exception],
        before_call: Callable[[], None] = lambda: None,
    ):
        self.app, self.principal, self.loop = app, principal, loop
        self.error, self.before_call = error, before_call

    def call(self, method: str, path: str, body: Any = None, raw: bool = False):
        self.before_call()
        future = asyncio.run_coroutine_threadsafe(
            asgi_request(self.app, self.principal, method, path, body), self.loop
        )
        status, data = future.result(timeout=CALL_TIMEOUT_SECONDS)
        text = data.decode("utf-8", "replace")
        if status >= 400:
            raise self.error(method, path, status, text)
        return text if raw else (json.loads(text) if text else None)

    def get(self, path: str, raw: bool = False):
        return self.call("GET", path, raw=raw)

    def post(self, path: str, body: Any = None):
        return self.call("POST", path, body if body is not None else {})

    def put(self, path: str, body: Any):
        return self.call("PUT", path, body)

    def patch(self, path: str, body: Any):
        return self.call("PATCH", path, body)
