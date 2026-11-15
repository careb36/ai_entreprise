"""The provider connections' HTTP adapter: list a provider's models.

``provider_connections.validate`` decides what the answer means and records
it on the connection; this module only asks ``GET {base_url}/models`` with
the headers it is given and returns the status code and the raw body. A
transport failure is raised as ``ProviderUnreachable``, whose message is the
name of the transport's exception (``ConnectError``). *transport* is an
httpx transport, for the tests.
"""

from __future__ import annotations

from typing import Any

import httpx


class ProviderUnreachable(RuntimeError):
    """The provider could not be reached; the message names why."""


def list_models(
    base_url: str,
    headers: dict[str, str],
    timeout: float,
    transport: Any = None,
) -> tuple[int, bytes]:
    """``GET {base_url}/models``: the status code and the body as bytes."""
    try:
        with httpx.Client(timeout=timeout, transport=transport) as client:
            response = client.get(f"{base_url}/models", headers=headers)
    except httpx.HTTPError as exc:
        raise ProviderUnreachable(type(exc).__name__) from exc
    return response.status_code, response.content
