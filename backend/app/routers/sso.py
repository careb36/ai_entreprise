"""
Single sign-on router — the public half of OpenID Connect sign-in (issue #512).

Endpoints:
- ``GET  /v1/auth/sso/{key}/start`` — *key* is the connection's slug, an email or
  a domain; sets an HttpOnly binding cookie and redirects to the provider.
- ``GET  /v1/auth/sso/callback``    — the provider's redirect; validates and
  redirects to the frontend's ``/login/sso`` with a one-time handoff code, or
  with an ``error`` code the frontend translates. A link begun inside a
  signed-in session (``routers/sso_link.py``) lands on ``/account`` instead,
  with ``sso_link`` or ``sso_link_error``.
- ``POST /v1/auth/sso/exchange``    — trades the handoff code for the same
  browser session ``/v1/auth/login`` starts (HttpOnly cookies, issue #524).

``start`` and ``exchange`` refuse a request whose ``Authorization`` header
carries an API token (401): a token is for scripts and never signs a person in.

Rate-limited per client address (``SSO_RATE_LIMIT``). The design and its
threat model: ``docs/archive/design/SSO.md``.
"""

from urllib.parse import urlencode

import auth_tokens
import session_cookies
from config import settings
from database import get_session
from fastapi import APIRouter, Depends, HTTPException, Request, Response
from fastapi.responses import RedirectResponse
from logging_config import logger
from pydantic import Field
from schemas import STRICT, RequestModel
from security import limiter
from services.platform import api_tokens, oidc, sso_flow, sso_link
from sqlmodel import Session

router = APIRouter(prefix="/v1/auth/sso", tags=["Auth"])

BINDING_COOKIE = "fabrique_sso_binding"
COOKIE_PATH = "/v1/auth/sso"
LINK_RETURN_PATH = "/account"
API_TOKEN_REFUSED = "API tokens cannot be used to sign in with SSO"


def _refuse_api_token(request: Request) -> None:
    if api_tokens.bearer_is_token(request.headers.get("authorization")):
        raise HTTPException(status_code=401, detail=API_TOKEN_REFUSED)


def _rate_limit() -> str:
    return settings.SSO_RATE_LIMIT


def _frontend(path: str = "/login/sso", **params: str) -> RedirectResponse:
    base = settings.SSO_FRONTEND_URL.rstrip("/")
    response = RedirectResponse(f"{base}{path}?{urlencode(params)}", status_code=302)
    response.headers["Cache-Control"] = "no-store"
    return response


def set_binding(response: Response, binding: str) -> None:
    """The HttpOnly cookie that ties a flow's state to this browser. Lax, so
    the provider's top-level redirect back to the callback carries it."""
    response.set_cookie(
        BINDING_COOKIE,
        binding,
        max_age=int(sso_flow.STATE_TTL.total_seconds()),
        path=COOKIE_PATH,
        httponly=True,
        secure=settings.SSO_CALLBACK_URL.startswith("https://"),
        samesite="lax",
    )


class ExchangeRequest(RequestModel):
    model_config = STRICT

    code: str = Field(min_length=1, max_length=512)


@router.get("/{key}/start")
@limiter.limit(_rate_limit)
def sso_start(
    request: Request,
    key: str,
    session: Session = Depends(get_session),  # noqa: B008 - FastAPI dependency marker.
):
    _refuse_api_token(request)
    if not auth_tokens.is_auth_configured():
        return _frontend(error="not_configured")
    try:
        url, binding = sso_flow.start(session, key)
    except oidc.SsoError as exc:
        logger.info("sso start refused: %s", exc.code)
        return _frontend(error=exc.code)
    response = RedirectResponse(url, status_code=302)
    response.headers["Cache-Control"] = "no-store"
    set_binding(response, binding)
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
    binding = request.cookies.get(BINDING_COOKIE)
    try:
        row = sso_flow.take_state(session, state)
    except oidc.SsoError as exc:
        logger.info("sso callback refused: %s (%s)", exc.code, exc)
        row, response = None, _frontend(error=exc.code)
    if row is not None and row.kind == "link":
        try:
            outcome = sso_link.complete(
                session, row, code=code, error=error, binding=binding
            )
            response = _frontend(LINK_RETURN_PATH, sso_link=outcome)
        except oidc.SsoError as exc:
            logger.info("sso link refused: %s (%s)", exc.code, exc)
            response = _frontend(LINK_RETURN_PATH, sso_link_error=exc.code)
    elif row is not None:
        try:
            handoff = sso_flow.finish_login(
                session, row, code=code, error=error, binding=binding
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
    response: Response,
    req: ExchangeRequest,
    session: Session = Depends(get_session),  # noqa: B008 - FastAPI dependency marker.
):
    _refuse_api_token(request)
    if not auth_tokens.is_auth_configured():
        raise HTTPException(status_code=503, detail="Authentication is not configured")
    try:
        user = sso_flow.exchange_handoff(session, req.code)
    except oidc.SsoError as exc:
        raise HTTPException(status_code=401, detail="Invalid or expired code") from exc
    # The browser's session, in HttpOnly cookies as after a password login;
    # no token in the body (issue #524, docs/archive/design/SESSIONS.md).
    return session_cookies.start(response, session, user, "sso")
