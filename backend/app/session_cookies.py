"""The browser session's cookies and its CSRF check (issue #524).

Three cookies, all ``SameSite=Strict`` and ``Secure`` unless
``SESSION_COOKIE_SECURE`` is off for plain-http development:

- ``fabrique_session`` — the access token (a JWT with ``sid`` and ``csrf``),
  ``HttpOnly``, ``Path=/``, for ``ACCESS_TOKEN_EXPIRE_MINUTES``.
- ``fabrique_refresh`` — the refresh token, ``HttpOnly``, ``Path=/v1/auth``
  (only the refresh and logout routes ever see it), until the family's
  absolute expiry.
- ``fabrique_csrf``    — the CSRF token, readable by the page, ``Path=/``.

An unsafe request authenticated by the cookie must send ``X-Fabrique-CSRF``
equal to the ``fabrique_csrf`` cookie and to the digest signed into the
access token (or stored on the family, for refresh and logout): a signed
double submit, so a cookie planted by a sibling host does not pass. The
design and its threat model: ``docs/archive/design/SESSIONS.md``.
"""

from __future__ import annotations

import hmac
from datetime import timedelta

import auth_tokens
from config import settings
from fastapi import HTTPException, Request, Response
from models import SessionFamily, User
from services.platform import sessions
from sqlmodel import Session

SESSION_COOKIE = "fabrique_session"
REFRESH_COOKIE = "fabrique_refresh"
CSRF_COOKIE = "fabrique_csrf"
CSRF_HEADER = "X-Fabrique-CSRF"
REFRESH_PATH = "/v1/auth"
SAFE_METHODS = frozenset({"GET", "HEAD", "OPTIONS"})


def _access_seconds() -> int:
    return settings.ACCESS_TOKEN_EXPIRE_MINUTES * 60


def _remaining_seconds(family: SessionFamily) -> int:
    left = family.expires_at - sessions._now()
    return max(0, int(left.total_seconds()))


def _set(response: Response, name: str, value: str, max_age: int, **kw) -> None:
    response.set_cookie(
        name,
        value,
        max_age=max_age,
        secure=settings.SESSION_COOKIE_SECURE,
        samesite="strict",
        **kw,
    )


def _access_token(user: User, family: SessionFamily) -> str:
    return auth_tokens.create_access_token(
        user_id=user.id,
        email=user.email,
        expires_delta=timedelta(minutes=settings.ACCESS_TOKEN_EXPIRE_MINUTES),
        session_id=family.public_id,
        csrf_hash=family.csrf_hash,
    )


def _write(
    response: Response,
    user: User,
    issued: sessions.Issued,
) -> dict:
    family = issued.family
    remaining = _remaining_seconds(family)
    _set(
        response,
        SESSION_COOKIE,
        _access_token(user, family),
        min(_access_seconds(), remaining),
        httponly=True,
        path="/",
    )
    _set(
        response,
        REFRESH_COOKIE,
        issued.refresh_token,
        remaining,
        httponly=True,
        path=REFRESH_PATH,
    )
    body = {
        "token_type": "cookie",
        "expires_in": min(_access_seconds(), remaining),
        "session_expires_in": remaining,
    }
    if issued.csrf_token is not None:
        _set(response, CSRF_COOKIE, issued.csrf_token, remaining, path="/")
        body["csrf_token"] = issued.csrf_token
    response.headers["Cache-Control"] = "no-store"
    return body


def start(response: Response, db: Session, user: User, method: str) -> dict:
    """Start a family for *user*, set its cookies; the body for the browser
    (no token in it: only lifetimes and the CSRF value the page may read)."""
    return _write(response, user, sessions.start(db, user.id, method))


def refreshed(response: Response, user: User, issued: sessions.Issued) -> dict:
    return _write(response, user, issued)


def clear(response: Response) -> None:
    secure = settings.SESSION_COOKIE_SECURE
    for name, path, httponly in (
        (SESSION_COOKIE, "/", True),
        (REFRESH_COOKIE, REFRESH_PATH, True),
        (CSRF_COOKIE, "/", False),
    ):
        response.delete_cookie(
            name, path=path, secure=secure, httponly=httponly, samesite="strict"
        )
    response.headers["Cache-Control"] = "no-store"


def csrf_ok(request: Request, expected_digest: str | None) -> bool:
    """The header equals the cookie, and its digest equals *expected_digest*."""
    header = request.headers.get(CSRF_HEADER) or ""
    cookie = request.cookies.get(CSRF_COOKIE) or ""
    if not header or not cookie or not hmac.compare_digest(header, cookie):
        return False
    return sessions.csrf_matches(expected_digest or "", header)


def csrf_refused() -> HTTPException:
    """The 403 for a missing or wrong CSRF header. The header it carries tells
    a page that could not read the cookie to ask ``GET /v1/auth/csrf`` once
    and resend; nothing was done."""
    return HTTPException(
        status_code=403,
        detail="CSRF token missing or invalid",
        headers={CSRF_HEADER: "required"},
    )


def warn_if_insecure() -> str | None:
    """A startup warning when cookies would travel over plain HTTP beyond
    localhost; ``None`` when there is nothing to say."""
    if settings.SESSION_COOKIE_SECURE:
        return None
    return (
        "SESSION_COOKIE_SECURE is off: session cookies are sent over plain HTTP. "
        "Use it only for development on http://localhost."
    )
