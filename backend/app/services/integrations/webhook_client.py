"""The notification webhook's HTTP adapter: one signed POST, and nothing else.

``webhooks.deliver`` builds and signs the body, checks the URL, resolves it
once (``egress.pin``) and records the answer; this module only posts it to
the address that was checked, with the original host in ``Host`` and in TLS
SNI, without following redirects, and returns the status code. A transport
failure is raised as ``WebhookPostError``, whose message names the
transport's own exception (``ConnectError: ...``), the text the hook records
as its last error. ``post_json`` sends the backend's redacted error report
(``exception_handlers``) to the operator's ``ERROR_REPORT_URL``.

``TRANSPORT`` is an httpx transport, for the tests.
"""

from __future__ import annotations

from typing import Any

from services.platform.egress import Target

TRANSPORT: Any = None


class WebhookPostError(RuntimeError):
    """The POST could not be sent or its answer not read."""


async def post(
    target: Target | str, body: bytes, headers: dict[str, str], timeout: float
) -> int:
    """POST *body* to *target*; the status code of the answer."""
    import httpx

    if isinstance(target, str):
        target = Target.unpinned(target)
    try:
        async with httpx.AsyncClient(
            timeout=timeout, follow_redirects=False, transport=TRANSPORT
        ) as client:
            response = await client.post(
                target.connect_url,
                content=body,
                headers={**headers, **target.headers()},
                extensions=target.extensions(),
            )
    except Exception as exc:  # Every transport failure, named and re-raised.
        raise WebhookPostError(f"{type(exc).__name__}: {exc}") from exc
    return response.status_code


async def post_json(url: str, body: bytes, timeout: float) -> int:
    """POST a JSON *body* to an operator-configured *url*; the status code."""
    return await post(
        url,
        body,
        {"Content-Type": "application/json", "User-Agent": "La-Fabrique-Errors"},
        timeout,
    )
