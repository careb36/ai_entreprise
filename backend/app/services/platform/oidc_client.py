"""The identity providers' HTTP adapter (single sign-on, ``docs/design/SSO.md``).

``oidc`` decides what an answer means; this module only asks: ``GET`` a JSON
document (discovery, JWKS) and ``POST`` a form (the token endpoint), and
returns the parsed JSON object. It never follows a redirect and refuses a
body over ``MAX_BYTES``, so an issuer cannot steer the backend to another
host or flood it. A transport failure is ``OidcUnreachable`` (its message is
the transport exception's name), an answer that is not a 2xx JSON object is
``OidcHttpError`` (status and a short reason, never the body). Unless
*allow_private* (``SSO_ALLOW_PRIVATE_ISSUERS``), the host is resolved once,
refused unless public, and the request goes to that address with the host in
``Host`` and TLS SNI (``egress.pin``; the refusal is ``egress.EgressRefused``),
so a DNS answer that changes between the check and the connection cannot
steer it inward. *transport* is an httpx transport, for the tests.
"""

from __future__ import annotations

import json
from typing import Any

import httpx

from services.platform import egress

MAX_BYTES = 1_048_576


class OidcUnreachable(RuntimeError):
    """The provider could not be reached; the message names why."""


class OidcHttpError(RuntimeError):
    """The provider answered, but not with a usable JSON object."""

    def __init__(self, status: int, reason: str):
        super().__init__(f"HTTP {status}: {reason}")
        self.status = status
        self.reason = reason


def _parse(status: int, content: bytes) -> dict[str, Any]:
    try:
        body = json.loads(content or b"null")
    except ValueError:
        body = None
    if not 200 <= status < 300:
        # The token endpoint's error code is safe to report (RFC 6749 §5.2);
        # nothing else of the body is.
        code = body.get("error") if isinstance(body, dict) else None
        reason = str(code)[:64] if isinstance(code, str) else "error response"
        raise OidcHttpError(status, reason)
    if not isinstance(body, dict):
        raise OidcHttpError(status, "not a JSON object")
    return body


def _send(
    method: str,
    url: str,
    timeout: float,
    transport: Any,
    allow_private: bool = False,
    **kwargs: Any,
) -> dict[str, Any]:
    """Send the request and read at most ``MAX_BYTES`` of the answer."""
    target = egress.pin(url, allow_private=allow_private)
    headers = {"Accept": "application/json", **target.headers()}
    try:
        with (
            httpx.Client(
                timeout=timeout, transport=transport, follow_redirects=False
            ) as client,
            client.stream(
                method,
                target.connect_url,
                headers=headers,
                extensions=target.extensions(),
                **kwargs,
            ) as response,
        ):
            content = b""
            for chunk in response.iter_bytes():
                content += chunk
                if len(content) > MAX_BYTES:
                    raise OidcHttpError(response.status_code, "response too large")
            status = response.status_code
    except httpx.HTTPError as exc:
        raise OidcUnreachable(type(exc).__name__) from exc
    return _parse(status, content)


def get_json(
    url: str, timeout: float, transport: Any = None, allow_private: bool = False
) -> dict[str, Any]:
    """``GET url``: the JSON object it answers."""
    return _send("GET", url, timeout, transport, allow_private)


def post_form(
    url: str,
    data: dict[str, str],
    timeout: float,
    auth: tuple[str, str] | None = None,
    transport: Any = None,
    allow_private: bool = False,
) -> dict[str, Any]:
    """``POST url`` with a form body (and HTTP Basic *auth*): the JSON object."""
    return _send("POST", url, timeout, transport, allow_private, data=data, auth=auth)
