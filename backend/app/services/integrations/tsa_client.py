"""The RFC 3161 timestamping authority's HTTP adapter: one POST, and nothing else.

Issue #542, ``docs/archive/design/AUDIT_ANCHORS.md``. ``anchoring.deliver_tsa`` builds
the ``TimeStampReq`` (``services/platform/rfc3161.py``), checks the URL and
checks the token; this module only posts the request (``application/
timestamp-query``, RFC 3161 section 3.4), without following redirects, and
returns the answer's bytes, at most ``rfc3161.MAX_TOKEN_BYTES``. Any failure is
raised as ``TsaRequestError``, whose message names the transport's exception
or the status code.
"""

from __future__ import annotations

from typing import Any

from services.platform import rfc3161
from services.platform.egress import Target

TRANSPORT: Any = None


class TsaRequestError(RuntimeError):
    """The request could not be sent, or the authority did not answer with a response."""


async def request(target: Target | str, body: bytes, timeout: float) -> bytes:
    """POST the ``TimeStampReq`` *body* to *target*; the ``TimeStampResp`` bytes.

    Sent to the address the caller pinned (``egress.pin``, #541 INJT-19), with
    the original host in ``Host`` and TLS SNI."""
    import httpx

    if isinstance(target, str):
        target = Target.unpinned(target)

    limit = rfc3161.MAX_TOKEN_BYTES
    headers = {
        "Content-Type": "application/timestamp-query",
        "Accept": "application/timestamp-reply",
        "User-Agent": "La-Fabrique-Audit-Anchor",
    }
    try:
        async with (
            httpx.AsyncClient(
                timeout=timeout, follow_redirects=False, transport=TRANSPORT
            ) as client,
            client.stream(
                "POST",
                target.connect_url,
                content=body,
                headers={**headers, **target.headers()},
                extensions=target.extensions(),
            ) as response,
        ):
            if not 200 <= response.status_code < 300:
                raise TsaRequestError(f"HTTP {response.status_code}")
            received = bytearray()
            async for chunk in response.aiter_bytes():
                received.extend(chunk)
                if len(received) > limit:
                    raise TsaRequestError("the answer is larger than a token may be")
            return bytes(received)
    except TsaRequestError:
        raise
    except Exception as exc:  # Every transport failure, named and re-raised.
        raise TsaRequestError(f"{type(exc).__name__}: {exc}") from exc
