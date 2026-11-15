"""The research tools' HTTP adapter: one request, and nothing else.

``research_tools`` decides which hosts may be fetched, follows redirects
itself and records each request as an observation; this module only sends
one request with no credentials and no redirects followed, its body capped,
and returns ``(status, headers, body)``. Unless *allow_private* (a host the
deployment itself named in ``RESEARCH_ALLOWED_HOSTS``), the host is resolved
once, refused unless every address is public, and the request goes to that
address with the host in ``Host`` and TLS SNI (``egress.pin``), so neither a
company's host list nor a DNS answer that changes between the check and the
connection reaches the backend's own network. A transport failure or a
refused address is raised as ``ResearchFetchError`` (a ``ResearchError``),
named like the transport's own exception.

``TRANSPORT`` is an httpx transport, for the tests.
"""

from __future__ import annotations

from typing import Any

from config import settings

from services.platform import egress
from services.tools.research_registries import ResearchError

TRANSPORT: Any = None


class ResearchFetchError(ResearchError):
    """The request could not be sent or its answer not read."""


def default_http(
    method: str,
    url: str,
    body: Any = None,
    timeout: float = 20.0,
    allow_private: bool = False,
) -> tuple[int, dict[str, str], bytes]:
    """One HTTP request with no credentials, no redirects followed, the body capped."""
    import httpx

    try:
        target = egress.pin(url, allow_private=allow_private)
    except egress.EgressRefused as exc:
        raise ResearchFetchError(
            "EgressRefused: the host resolves to a private or reserved address"
        ) from exc
    headers = {
        "User-Agent": "la-fabrique-research/1.0",
        "Accept": "text/html, application/json, text/plain;q=0.9, */*;q=0.1",
        **target.headers(),
    }
    try:
        with (
            httpx.Client(
                timeout=timeout, follow_redirects=False, transport=TRANSPORT
            ) as client,
            client.stream(
                method,
                target.connect_url,
                json=body,
                headers=headers,
                extensions=target.extensions(),
            ) as response,
        ):
            data = b""
            for chunk in response.iter_bytes(65536):
                data += chunk
                if len(data) >= settings.RESEARCH_FETCH_MAX_BYTES:
                    break
            status = response.status_code
            answer_headers = {k.lower(): v for k, v in response.headers.items()}
    except httpx.HTTPError as exc:
        name = type(exc).__name__
        raise ResearchFetchError(f"{name}: {exc}" if str(exc) else name) from exc
    return status, answer_headers, data
