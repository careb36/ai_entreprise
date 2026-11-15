"""
Single sign-on router — the public half of OpenID Connect sign-in (issue #512).

Endpoints:
- ``GET  /v1/auth/sso/{key}/start`` — *key* is the connection's slug, an email or
  a domain; sets an HttpOnly binding cookie and redirects to the provider.
- ``GET  /v1/auth/sso/callback``    — the provider's redirect; validates and
  redirects to the frontend's ``/login/sso`` with a one-time handoff code, or
  with an ``error`` code the frontend translates.
- ``POST /v1/auth/sso/exchange``    — trades the handoff code for the access
  token ``/v1/auth/login`` issues, so the rest of the app is unchanged.

Rate-limited per client address (``SSO_RATE_LIMIT``). The design and its
threat model: ``docs/design/SSO.md``.
"""

from datetime import timedelta
from urllib.parse import urlencode

import auth_tokens
from config import settings
from database import get_session
from fastapi import APIRouter, Depends, HTTPException, Request
from fastapi.responses import RedirectResponse
from logging_config import logger
from pydantic import BaseModel, Field
from security import limiter
from services.platform import oidc, sso_flow
from sqlmodel import Session

router = APIRouter(prefix="/v1/auth/sso", tags=["Auth"])

BINDING_COOKIE = "fabrique_sso_binding"
COOKIE_PATH = "/v1/auth/sso"


def _rate_limit() -> str:
    return settings.SSO_RATE_LIMIT


def _frontend(**params: str) -> RedirectResponse:
    base = settings.SSO_FRONTEND_URL.rstrip("/")
    response = RedirectResponse(
        f"{base}/login/sso?{urlencode(params)}", status_code=302
    )
    response.headers["Cache-Control"] = "no-store"
    return response


class ExchangeRequest(BaseModel):
    code: str = Field(min_length=1, max_length=512)


@router.get("/{key}/start")
@limiter.limit(_rate_limit)
def sso_start(
    request: Request,
    key: str,
    session: Session = Depends(get_session),  # noqa: B008 - FastAPI dependency marker.
):
    if not auth_tokens.is_auth_configured():
        return _frontend(error="not_configured")
    try:
        url, binding = sso_flow.start(session, key)
    except oidc.SsoError as exc:
        logger.info("sso start refused: %s", exc.code)
        return _frontend(error=exc.code)
    response = RedirectResponse(url, status_code=302)
    response.headers["Cache-Control"] = "no-store"
    response.set_cookie(
        BINDING_COOKIE,
        binding,
        max_age=int(sso_flow.STATE_TTL.total_seconds()),
        path=COOKIE_PATH,
        httponly=True,
        secure=settings.SSO_CALLBACK_URL.startswith("https://"),
        samesite="lax",
    )
    return response


@router.get("/callback")
@limiter.limit(_rate_limit)
def sso_callback(
    request: Request,
    state: str | None = None,
    code: str | None = None,
    error: str | None = None,
    session: Session = Depends(get_session),  # noqa: B008 - FastAPI dependency marker.
):
    try:
        handoff = sso_flow.complete(
            session,
            state=state,
            code=code,
            error=error,
            binding=request.cookies.get(BINDING_COOKIE),
        )
        response = _frontend(code=handoff)
    except oidc.SsoError as exc:
        logger.info("sso callback refused: %s (%s)", exc.code, exc)
        response = _frontend(error=exc.code)
    response.delete_cookie(BINDING_COOKIE, path=COOKIE_PATH)
    return response


@router.post("/exchange")
@limiter.limit(_rate_limit)
def sso_exchange(
    request: Request,
    req: ExchangeRequest,
    session: Session = Depends(get_session),  # noqa: B008 - FastAPI dependency marker.
):
    if not auth_tokens.is_auth_configured():
        raise HTTPException(status_code=503, detail="Authentication is not configured")
    try:
        user = sso_flow.exchange_handoff(session, req.code)
    except oidc.SsoError as exc:
        raise HTTPException(status_code=401, detail="Invalid or expired code") from exc
    token = auth_tokens.create_access_token(
        user_id=user.id,
        email=user.email,
        expires_delta=timedelta(minutes=settings.ACCESS_TOKEN_EXPIRE_MINUTES),
    )
    return {
        "access_token": token,
        "token_type": "bearer",
        "expires_in": settings.ACCESS_TOKEN_EXPIRE_MINUTES * 60,
    }
