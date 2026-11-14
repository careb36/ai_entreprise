"""The research tools' HTTP adapter: one request, and nothing else.

``research_tools`` decides which hosts may be fetched, follows redirects
itself and records each request as an observation; this module only sends
one request with no credentials and no redirects followed, its body capped,
and returns ``(status, headers, body)``. A transport failure is raised as
``ResearchFetchError`` (a ``ResearchError``), named like the transport's own
exception, so the model and the record read the same text as before.
"""

from __future__ import annotations

from typing import Any

from config import settings

from services.tools.research_registries import ResearchError


class ResearchFetchError(ResearchError):
    """The request could not be sent or its answer not read."""


def default_http(
    method: str, url: str, body: Any = None, timeout: float = 20.0
) -> tuple[int, dict[str, str], bytes]:
    """One HTTP request with no credentials, no redirects followed, the body capped."""
    import requests

    try:
        response = requests.request(
            method,
            url,
            json=body,
            timeout=timeout,
            allow_redirects=False,
            stream=True,
            headers={
                "User-Agent": "la-fabrique-research/1.0",
                "Accept": "text/html, application/json, text/plain;q=0.9, */*;q=0.1",
            },
        )
        data = b""
        for chunk in response.iter_content(65536):
            data += chunk
            if len(data) >= settings.RESEARCH_FETCH_MAX_BYTES:
                break
        response.close()
    except requests.RequestException as exc:
        name = type(exc).__name__
        raise ResearchFetchError(f"{name}: {exc}" if str(exc) else name) from exc
    return (
        response.status_code,
        {k.lower(): v for k, v in response.headers.items()},
        data,
    )
