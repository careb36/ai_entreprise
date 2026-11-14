"""The pre-QA sandbox's HTTP adapter: its health and validate endpoints.

``sandbox_manager`` starts and tears down the sandbox and decides what its
answers mean; this module only sends the requests and returns the decoded
JSON of a 200 answer (``None`` for any other status). A failure to reach the
sandbox (``urllib``'s ``URLError``, an HTTP error status included) is raised
as ``SandboxUnreachable`` with the same message.
"""

from __future__ import annotations

import json
import urllib.error
import urllib.request
from typing import Any


class SandboxUnreachable(Exception):
    """The sandbox's endpoint could not be reached or answered an HTTP error."""


def _open(request: urllib.request.Request, timeout: float) -> dict[str, Any] | None:
    try:
        with urllib.request.urlopen(request, timeout=timeout) as resp:
            if resp.status == 200:
                return json.loads(resp.read().decode("utf-8"))
            return None
    except urllib.error.URLError as exc:
        raise SandboxUnreachable(str(exc)) from exc


def get_json(url: str, timeout: float) -> dict[str, Any] | None:
    """GET *url*; the JSON of a 200 answer, else ``None``."""
    return _open(urllib.request.Request(url), timeout)


def post_json(
    url: str, payload: dict[str, Any], timeout: float
) -> dict[str, Any] | None:
    """POST *payload* as JSON to *url*; the JSON of a 200 answer, else ``None``."""
    request = urllib.request.Request(
        url,
        data=json.dumps(payload).encode("utf-8"),
        headers={"Content-Type": "application/json"},
        method="POST",
    )
    return _open(request, timeout)
