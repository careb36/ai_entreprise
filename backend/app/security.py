"""
Security middleware and utilities for the Fabrique Enterprise API.

Provides:
- Rate limiting via slowapi
- The client's address behind a trusted reverse proxy
- Security headers middleware
- CORS configuration
"""

import ipaddress
from functools import lru_cache

from config import settings
from fastapi import Request
from logging_config import logger
from slowapi import Limiter
from slowapi.errors import RateLimitExceeded
from slowapi.util import get_remote_address

# Rate limiter configuration
limiter = Limiter(
    key_func=get_remote_address,
    default_limits=[settings.RATE_LIMIT_DEFAULT],
    storage_uri=settings.RATE_LIMIT_STORAGE_URI,
)


from starlette.middleware.base import BaseHTTPMiddleware
from starlette.responses import JSONResponse

# The interactive documentation FastAPI serves (WSTG-v42-CONF-05): turned off
# by API_DOCS_ENABLED=false, and given a CSP that lets Swagger UI and ReDoc
# load their scripts while it is on.
DOCS_PATHS = frozenset({"/docs", "/docs/oauth2-redirect", "/redoc", "/openapi.json"})

# The API answers JSON (and one event stream); it never renders a page, so
# nothing may load, frame it or submit to it (issue #541; WSTG-CONF-14 and
# CONF-12 at latest@87cf4a4, WSTG-v42-CLNT-09).
API_CSP = (
    "default-src 'none'; frame-ancestors 'none'; base-uri 'none'; form-action 'none'"
)
DOCS_CSP = (
    "default-src 'self'; script-src 'self' https://cdn.jsdelivr.net "
    "'unsafe-inline'; style-src 'self' https://cdn.jsdelivr.net "
    "https://fonts.googleapis.com 'unsafe-inline'; "
    "font-src https://fonts.gstatic.com; "
    "img-src 'self' data: https://fastapi.tiangolo.com; worker-src blob:; "
    "frame-ancestors 'none'"
)
BASELINE_HEADERS = {
    # Prevent MIME type sniffing.
    "X-Content-Type-Options": "nosniff",
    # Clickjacking, for browsers that predate frame-ancestors.
    "X-Frame-Options": "DENY",
    # The legacy XSS auditor is off: it is gone from current browsers and its
    # "block" mode was itself a side channel (OWASP Secure Headers Project).
    "X-XSS-Protection": "0",
    # HTTPS only; browsers ignore it over plain http.
    "Strict-Transport-Security": "max-age=31536000; includeSubDomains",
    # A response of this API never needs to tell anyone where it came from
    # (SECURITY.md §3.3).
    "Referrer-Policy": "no-referrer",
    "Permissions-Policy": "geolocation=(), microphone=(), camera=()",
    # A window this API opens shares no browsing context with its opener.
    "Cross-Origin-Opener-Policy": "same-origin",
}


class SecurityHeadersMiddleware(BaseHTTPMiddleware):
    """Add security headers to all responses.

    Every answer that is not the documentation is ``Cache-Control:
    no-store`` unless its route chose otherwise (the Glassroom stream's
    ``no-cache``): an authenticated answer must not stay in a shared or
    browser cache after logout (WSTG-v42-ATHN-06). The documentation is not
    authenticated and keeps the browser's default caching.
    """

    async def dispatch(self, request: Request, call_next):
        docs = request.url.path in DOCS_PATHS
        if docs and not settings.API_DOCS_ENABLED:
            docs = False
            response = JSONResponse(status_code=404, content={"detail": "Not Found"})
        else:
            response = await call_next(request)
        return apply_security_headers(response, docs=docs)


def apply_security_headers(response, *, docs: bool = False):
    """The baseline on *response*; also called by the handler of unhandled
    errors, whose 500 is built outside the middleware stack."""
    if not settings.SECURITY_HEADERS_ENABLED:
        return response
    for name, value in BASELINE_HEADERS.items():
        response.headers[name] = value
    response.headers["Content-Security-Policy"] = DOCS_CSP if docs else API_CSP
    if not docs and "cache-control" not in response.headers:
        response.headers["Cache-Control"] = "no-store"
    return response


# -- the client's address behind a reverse proxy ---------------------------

IPNetwork = ipaddress.IPv4Network | ipaddress.IPv6Network


@lru_cache(maxsize=8)
def parse_trusted_proxies(value: str) -> tuple[IPNetwork, ...]:
    """The networks of ``TRUSTED_PROXY_IPS`` (addresses or CIDR networks,
    comma-separated); empty trusts nobody. A malformed entry raises
    ``ValueError`` naming the setting: a typo must not quietly trust less, or
    more, than meant."""
    networks = []
    for entry in (part.strip() for part in value.split(",")):
        if not entry:
            continue
        try:
            networks.append(ipaddress.ip_network(entry, strict=False))
        except ValueError as exc:
            raise ValueError(
                f"TRUSTED_PROXY_IPS has {entry!r}, which is not an IP address "
                "or network"
            ) from exc
    return tuple(networks)


def _address(value: str):
    try:
        return ipaddress.ip_address(value.strip())
    except ValueError:
        return None


def _trusted(value: str, networks: tuple[IPNetwork, ...]) -> bool:
    address = _address(value)
    return address is not None and any(address in n for n in networks)


def forwarded_client(
    peer: str, forwarded_for: str | None, networks: tuple[IPNetwork, ...]
) -> str:
    """The client's address: *peer* (the connection's), unless *peer* is a
    trusted proxy; then the right-most address of *forwarded_for* that is not
    itself a trusted proxy. Each proxy appends the address it was reached
    from, so everything left of that is the client's own claim and is never
    believed. A malformed entry stops the walk at the last trusted hop (a
    shared bucket, never one the client can choose)."""
    if not networks or not _trusted(peer, networks) or not forwarded_for:
        return peer
    client = peer
    for hop in reversed(forwarded_for.split(",")):
        address = _address(hop)
        if address is None:
            return client
        client = str(address)
        if not any(address in n for n in networks):
            return client
    return client


class TrustedProxyMiddleware:
    """Puts the real client's address in ``scope["client"]`` when the request
    came through a proxy listed in ``TRUSTED_PROXY_IPS``, so the rate limits
    (``get_remote_address``) and the logs see the client, not the proxy. The
    header is ignored otherwise. Pure ASGI, so it runs before the routes and
    their limiter decorators."""

    def __init__(self, app):
        self.app = app

    async def __call__(self, scope, receive, send):
        if scope["type"] in ("http", "websocket") and scope.get("client"):
            networks = parse_trusted_proxies(settings.TRUSTED_PROXY_IPS)
            if networks:
                host, port = scope["client"]
                header = None
                for name, value in scope.get("headers") or ():
                    if name == b"x-forwarded-for":
                        decoded = value.decode("latin-1")
                        # Several headers are one list, in order (RFC 9110 §5.3).
                        header = decoded if header is None else f"{header},{decoded}"
                client = forwarded_client(host, header, networks)
                if client != host:
                    scope = {**scope, "client": (client, port)}
        await self.app(scope, receive, send)


def setup_security(app):
    """Configure security middleware and rate limiting on the app."""
    from slowapi import _rate_limit_exceeded_handler

    # Add rate limiter to app state
    app.state.limiter = limiter

    # Add rate limit exceeded handler
    app.add_exception_handler(RateLimitExceeded, _rate_limit_exceeded_handler)

    # Add security headers middleware
    app.add_middleware(SecurityHeadersMiddleware)
    # Added last, so it runs first: every later layer sees the real client.
    parse_trusted_proxies(settings.TRUSTED_PROXY_IPS)  # refuse a malformed one
    app.add_middleware(TrustedProxyMiddleware)

    logger.info("Security middleware configured (rate limiting + security headers)")


def get_cors_origins():
    """Get allowed CORS origins from settings."""
    origins = [o.strip() for o in settings.CORS_ORIGINS.split(",") if o.strip()]
    return ["*"] if "*" in origins else origins


def cors_allows_credentials(origins: list[str]) -> bool:
    """Credentials (the session cookies) only with explicit origins.

    With ``*`` and credentials Starlette echoes whatever origin asks, so any
    site could read authenticated answers; ``*`` therefore turns them off
    (issue #524, docs/design/SESSIONS.md, S6).
    """
    if "*" in origins:
        logger.warning(
            "CORS_ORIGINS is '*': cross-origin requests carry no cookies, so a "
            "browser on another origin cannot sign in. List the frontend's origins."
        )
        return False
    return True


def check_rate_limit(request: Request):
    """Check if request is within rate limit. Returns True if allowed."""
    # This is a placeholder for custom rate limit logic if needed
    return True
