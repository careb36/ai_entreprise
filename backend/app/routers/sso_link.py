"""
Linking an SSO identity from inside a signed-in session (issue #512).

Endpoints:
- ``POST   /v1/auth/sso/link/start``                         — a signed-in member,
  in a browser session (cookie, CSRF checked; no Bearer or API token), starts
  an authorisation at one of their companies' connections; sets the binding
  cookie and answers the provider's URL. The provider's redirect comes back
  to ``/v1/auth/sso/callback`` (``routers/sso.py``), which lands on the
  frontend's ``/account``.
- ``GET    /v1/auth/sso/identities``                         — the caller's own
  identities, the companies they can still link and whether they have a
  password.
- ``DELETE /v1/auth/sso/identities/{identity_id}``           — unlink one's own
  identity, only while another way to sign in remains (409 otherwise).
- ``GET    /v1/companies/{id}/sso/identities``               — Owner: the
  identities through the company's connection.
- ``DELETE /v1/companies/{id}/sso/identities/{identity_id}`` — Owner: unlink a
  member's identity through the company's connection; ends their SSO-started
  sessions.

The changes are rate-limited per client address (``SSO_RATE_LIMIT``) and
audited. Design and threat model: ``docs/archive/design/SSO.md``.
"""

import secrets

import auth_tokens
import session_cookies
from auth_policy import current_user_if_required
from config import settings
from database import get_session
from fastapi import APIRouter, Depends, HTTPException, Request
from fastapi.responses import JSONResponse
from models import User
from schemas import STRICT, RequestModel
from security import limiter
from services.platform import sso_link
from sqlmodel import Session

from routers.sso import set_binding
from routers.sso_admin import _owner

router = APIRouter(prefix="/v1", tags=["SSO"])


def _rate_limit() -> str:
    return settings.SSO_RATE_LIMIT


class LinkStart(RequestModel):
    model_config = STRICT

    company_id: int


def _person(user: User | None) -> User:
    """A signed-in person, not an API token (always, even without AUTH_REQUIRED)."""
    if user is None:
        raise HTTPException(status_code=401, detail="Not authenticated")
    if getattr(user, "_api_token_company_id", None) is not None:
        raise HTTPException(
            status_code=403, detail="SSO identities belong to a person, not a token"
        )
    return user


def _refused(exc: sso_link.LinkRefused) -> HTTPException:
    return HTTPException(status_code=exc.status, detail=exc.code)


@router.post("/auth/sso/link/start")
@limiter.limit(_rate_limit)
def link_start(
    request: Request,
    req: LinkStart,
    session: Session = Depends(get_session),  # noqa: B008 - FastAPI dependency marker.
    user: User | None = Depends(current_user_if_required),  # noqa: B008 - FastAPI dependency marker.
):
    user = _person(user)
    # The state is bound to the browser session: a Bearer token has none.
    raw = request.cookies.get(session_cookies.SESSION_COOKIE)
    session_id = (
        auth_tokens.session_id_of(raw)
        if raw and not request.headers.get("Authorization")
        else None
    )
    if session_id is None:
        raise HTTPException(
            status_code=403, detail="linking needs a signed-in browser session"
        )
    binding = secrets.token_urlsafe(32)
    try:
        url = sso_link.start(session, user, req.company_id, session_id, binding)
    except sso_link.LinkRefused as exc:
        raise _refused(exc) from exc
    response = JSONResponse({"authorization_url": url})
    response.headers["Cache-Control"] = "no-store"
    set_binding(response, binding)
    return response


@router.get("/auth/sso/identities")
def my_identities(
    session: Session = Depends(get_session),  # noqa: B008 - FastAPI dependency marker.
    user: User | None = Depends(current_user_if_required),  # noqa: B008 - FastAPI dependency marker.
):
    return sso_link.mine(session, _person(user))


@router.delete("/auth/sso/identities/{identity_id}")
@limiter.limit(_rate_limit)
def unlink_mine(
    request: Request,
    identity_id: int,
    session: Session = Depends(get_session),  # noqa: B008 - FastAPI dependency marker.
    user: User | None = Depends(current_user_if_required),  # noqa: B008 - FastAPI dependency marker.
):
    try:
        return sso_link.unlink(session, _person(user), identity_id)
    except sso_link.LinkRefused as exc:
        raise _refused(exc) from exc


@router.get("/companies/{company_id}/sso/identities")
def company_identities(
    company_id: int,
    session: Session = Depends(get_session),  # noqa: B008 - FastAPI dependency marker.
    user: User | None = Depends(current_user_if_required),  # noqa: B008 - FastAPI dependency marker.
):
    _owner(session, user, company_id)
    return sso_link.of_company(session, company_id)


@router.delete("/companies/{company_id}/sso/identities/{identity_id}")
@limiter.limit(_rate_limit)
def unlink_member(
    request: Request,
    company_id: int,
    identity_id: int,
    session: Session = Depends(get_session),  # noqa: B008 - FastAPI dependency marker.
    user: User | None = Depends(current_user_if_required),  # noqa: B008 - FastAPI dependency marker.
):
    actor = _owner(session, user, company_id)
    try:
        return sso_link.unlink(session, actor, identity_id, company_id=company_id)
    except sso_link.LinkRefused as exc:
        raise _refused(exc) from exc
