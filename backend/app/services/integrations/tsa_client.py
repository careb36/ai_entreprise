"""The RFC 3161 timestamping authority's HTTP adapter: one POST, and nothing else.

Issue #542, ``docs/design/AUDIT_ANCHORS.md``. ``anchoring.deliver_tsa`` builds
the ``TimeStampReq`` (``services/platform/rfc3161.py``), checks the URL and
checks the token; this module only posts the request (``application/
timestamp-query``, RFC 3161 section 3.4), without following redirects, and
returns the answer's bytes, at most ``rfc3161.MAX_TOKEN_BYTES``. Any failure is
raised as ``TsaRequestError``, whose message names the transport's exception
or the status code.
"""

from __future__ import annotations

from services.platform import rfc3161


class TsaRequestError(RuntimeError):
    """The request could not be sent, or the authority did not answer with a response."""


async def request(url: str, body: bytes, timeout: float) -> bytes:
    """POST the ``TimeStampReq`` *body* to *url*; the ``TimeStampResp`` bytes."""
    import httpx

    limit = rfc3161.MAX_TOKEN_BYTES
    headers = {
        "Content-Type": "application/timestamp-query",
        "Accept": "application/timestamp-reply",
        "User-Agent": "La-Fabrique-Audit-Anchor",
    }
    try:
        async with (
            httpx.AsyncClient(timeout=timeout, follow_redirects=False) as client,
            client.stream("POST", url, content=body, headers=headers) as response,
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
