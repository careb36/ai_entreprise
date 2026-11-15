"""The notification webhook's HTTP adapter: one signed POST, and nothing else.

``webhooks.deliver`` builds and signs the body, checks the URL and records
the answer; this module only posts it, without following redirects, and
returns the status code. A transport failure is raised as
``WebhookPostError``, whose message names the transport's own exception
(``ConnectError: ...``), the text the hook records as its last error.
"""

from __future__ import annotations


class WebhookPostError(RuntimeError):
    """The POST could not be sent or its answer not read."""


async def post(url: str, body: bytes, headers: dict[str, str], timeout: float) -> int:
    """POST *body* to *url*; the status code of the answer."""
    import httpx

    try:
        async with httpx.AsyncClient(timeout=timeout, follow_redirects=False) as client:
            response = await client.post(url, content=body, headers=headers)
    except Exception as exc:  # Every transport failure, named and re-raised.
        raise WebhookPostError(f"{type(exc).__name__}: {exc}") from exc
    return response.status_code


async def post_for_receipt(
    url: str, body: bytes, headers: dict[str, str], timeout: float, max_bytes: int
) -> tuple[int, bytes]:
    """POST *body* to *url*; the status code and at most *max_bytes* of the answer.

    For the audit anchors (issue #542), whose endpoint may answer with a
    receipt: the answer is read up to the limit and the rest dropped.
    """
    import httpx

    try:
        async with (
            httpx.AsyncClient(timeout=timeout, follow_redirects=False) as client,
            client.stream("POST", url, content=body, headers=headers) as response,
        ):
            received = bytearray()
            async for chunk in response.aiter_bytes():
                received.extend(chunk[: max_bytes - len(received)])
                if len(received) >= max_bytes:
                    break
            return response.status_code, bytes(received)
    except Exception as exc:  # Every transport failure, named and re-raised.
        raise WebhookPostError(f"{type(exc).__name__}: {exc}") from exc
