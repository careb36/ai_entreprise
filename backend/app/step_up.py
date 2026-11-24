"""A fresh re-authentication before a dangerous owner action (``docs/design/owner-actions.md``, L1).

A protected ``land_branch`` sets ``LAND_OWNER=1`` on the daemon's computer.
A stolen session must not be enough for that, so the request that starts it
proves the person again, in the same request:

- an account with a password: the password, checked with bcrypt and the
  per-account backoff of the sign-in (``login_throttle``: a lock answers 429
  before the password is checked, a failure counts towards it);
- an SSO-only account (no password): the session the request comes with must
  be an SSO sign-in less than ``SSO_FRESH_SECONDS`` old (its family's
  ``created_at``; a refresh keeps the family, so it does not make a session
  fresh again). Otherwise: sign in again.

The password is never stored, logged or audited; the audit row records only
which proof was given. Design after GitHub's "sudo mode" (design only).
"""

from __future__ import annotations

from datetime import UTC, datetime

import auth_tokens
import session_cookies
from fastapi import HTTPException, Request
from models import User
from passwords import verify_password
from services.platform import login_throttle, sessions
from sqlmodel import Session

SSO_FRESH_SECONDS = 300
MAX_PASSWORD = 1024


def _refused(code: str, message: str, status: int = 403) -> HTTPException:
    return HTTPException(status_code=status, detail={"code": code, "message": message})


def _session_family(session: Session, request: Request):
    raw = request.cookies.get(session_cookies.SESSION_COOKIE)
    header = request.headers.get("Authorization") or ""
    if header.lower().startswith("bearer "):
        raw = header[7:].strip()
    if not raw:
        return None
    try:
        claims = auth_tokens.decode_access_token(raw)
    except auth_tokens.AuthTokenError:
        return None
    return sessions.active(session, claims.get("sid"))


def verify(session: Session, request: Request, user: User, password: str | None) -> str:
    """The proof given (``password`` or ``sso_recent``), or an HTTP refusal."""
    if user.password_hash:
        if not password:
            raise _refused(
                "step_up_required",
                "This land touches protected paths: enter your password again to confirm it.",
            )
        wait = login_throttle.retry_after(user.email)
        if wait:
            raise HTTPException(
                status_code=429,
                detail={
                    "code": "step_up_locked",
                    "message": "Too many attempts: try again later.",
                },
                headers={"Retry-After": str(wait)},
            )
        if len(password) > MAX_PASSWORD or not verify_password(
            password, user.password_hash
        ):
            login_throttle.failure(user.email)
            raise _refused("step_up_failed", "The password is not right.")
        login_throttle.success(user.email)
        return "password"
    family = _session_family(session, request)
    if family is not None and family.method == "sso":
        created = family.created_at
        if created.tzinfo is None:
            created = created.replace(tzinfo=UTC)
        if (datetime.now(UTC) - created).total_seconds() < SSO_FRESH_SECONDS:
            return "sso_recent"
    raise _refused(
        "step_up_sso_stale",
        "This land touches protected paths: sign in with SSO again, then confirm within five minutes.",
    )
