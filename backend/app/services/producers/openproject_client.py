"""The OpenProject HTTP adapter: create one work package.

``openproject_tools.create_work_package`` decides what to send and falls back
to a simulated ticket when OpenProject cannot take it; this module only
posts the work package and returns OpenProject's JSON answer. A failure is
raised as one of three named errors, each with the transport's message:
``OpenProjectUnreachable`` (the connection failed), ``OpenProjectRequestError``
(any other failure to send or read the request) and ``OpenProjectStatusError``
(an HTTP error status).
"""

from __future__ import annotations

from typing import Any

import httpx

TIMEOUT_SECONDS = 10.0


class OpenProjectError(Exception):
    """OpenProject did not create the work package."""


class OpenProjectUnreachable(OpenProjectError):
    """The connection to OpenProject failed."""


class OpenProjectRequestError(OpenProjectError):
    """The request could not be sent or its answer not read."""


class OpenProjectStatusError(OpenProjectError):
    """OpenProject answered an HTTP error status."""


async def post_work_package(
    base_url: str, headers: dict[str, str], body: dict[str, Any]
) -> Any:
    """``POST {base_url}/api/v3/work_packages``: OpenProject's JSON answer."""
    try:
        async with httpx.AsyncClient(timeout=TIMEOUT_SECONDS) as client:
            response = await client.post(
                f"{base_url}/api/v3/work_packages", headers=headers, json=body
            )
            response.raise_for_status()
    except httpx.ConnectError as exc:
        raise OpenProjectUnreachable(str(exc)) from exc
    except httpx.RequestError as exc:
        raise OpenProjectRequestError(str(exc)) from exc
    except httpx.HTTPStatusError as exc:
        raise OpenProjectStatusError(str(exc)) from exc
    return response.json()
