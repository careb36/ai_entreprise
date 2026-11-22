"""FastAPI dependencies for resolving authenticated human users and API tokens.

Two transports (``docs/archive/design/SESSIONS.md``):

- ``Authorization: Bearer`` — an API token or a Bearer access token. It wins
  whenever present, even beside a session cookie, and is never subject to the
  CSRF check: a header is not sent by the browser on its own.
- The ``fabrique_session`` cookie — a browser session. Its family must still
  be live, and an unsafe method must carry the CSRF header
  (``session_cookies.csrf_ok``).
"""

import auth_tokens
import session_cookies
from database import get_session
from fastapi import Depends, HTTPException, Request
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer
from models import User
from services.platform import api_tokens, sessions
from sqlmodel import Session

bearer = HTTPBearer(auto_error=False)

_UNAUTHORIZED = {"WWW-Authenticate": "Bearer"}


def _unauthorized(detail: str = "Invalid or expired token") -> HTTPException:
    return HTTPException(status_code=401, detail=detail, headers=_UNAUTHORIZED)


def _jwt_user(session: Session, raw: str) -> tuple[User, dict] | None:
    """The user of a valid access token, with its claims; ``None`` otherwise.
    A token naming a session family is good only while the family is live."""
    try:
        claims = auth_tokens.decode_access_token(raw)
        user_id = int(claims["sub"])
    except (auth_tokens.AuthTokenError, KeyError, TypeError, ValueError):
        return None
    if "sid" in claims and sessions.active(session, claims.get("sid")) is None:
        return None
    user = session.get(User, user_id)
    return (user, claims) if user is not None else None


def _from_bearer(session: Session, raw: str) -> User:
    resolved = _jwt_user(session, raw)
    if resolved is not None:
        return resolved[0]
    token = api_tokens.verify(session, raw)
    if token is not None:
        synthetic = User(
            id=-token.id,
            email=f"api-token:{token.id}",
            full_name=token.name,
            password_hash="",
        )
        synthetic._api_token_company_id = token.company_id  # type: ignore[attr-defined]
        synthetic._api_token_role = token.role or "Manager"  # type: ignore[attr-defined]
        return synthetic
    raise _unauthorized()


def _from_cookie(request: Request, session: Session, raw: str) -> User:
    resolved = _jwt_user(session, raw)
    if resolved is None or "sid" not in resolved[1]:
        raise _unauthorized()
    user, claims = resolved
    if request.method not in session_cookies.SAFE_METHODS and not (
        session_cookies.csrf_ok(request, claims.get("csrf"))
    ):
        raise session_cookies.csrf_refused()
    return user


def has_credentials(request: Request) -> bool:
    return bool(
        request.headers.get("Authorization")
        or request.cookies.get(session_cookies.SESSION_COOKIE)
    )


def get_current_user(
    request: Request,
    credentials: HTTPAuthorizationCredentials | None = Depends(bearer),  # noqa: B008
    session: Session = Depends(get_session),  # noqa: B008
) -> User:
    """Resolve the caller: a Bearer token first, else the session cookie."""
    if credentials is not None:
        return _from_bearer(session, credentials.credentials)
    cookie = request.cookies.get(session_cookies.SESSION_COOKIE)
    if cookie:
        return _from_cookie(request, session, cookie)
    raise _unauthorized("Not authenticated")
