"""
Auth router — human user registration and company membership management.

Endpoints:
- ``POST /v1/auth/register``  — create a new platform user account; the
  answer does not say whether the email already had one.
- ``POST /v1/auth/login``     — authenticate; start a browser session in
  cookies, or (``token_delivery: "bearer"``) return a Bearer access token.
- ``POST /v1/auth/refresh``   — rotate the session's refresh token (cookies).
- ``POST /v1/auth/logout``    — revoke the session's family and clear cookies.
- ``GET /v1/auth/csrf``       — the session's CSRF value, for a page that
  cannot read the API host's cookie.
- ``GET /v1/auth/me``         — return the authenticated user's profile.
- ``POST /v1/auth/password``  — the signed-in person changes their password;
  every session ends and this browser gets a new one
  (``docs/design/change-password.md``).
- ``POST /v1/company/invite`` lives in ``routers/invitations.py``: a pending
  invitation the invitee accepts or declines.
- ``DELETE /v1/company/{id}/members/{user_id}`` — remove a member (Owner);
  ends that person's sessions.

Browser sessions (issue #524): ``docs/archive/design/SESSIONS.md``.

Passwords are hashed with bcrypt directly (``app.passwords``) before storage.
Sensitive-route enforcement is gated by AUTH_REQUIRED.
"""

from datetime import timedelta

import auth_tokens
import session_cookies
from auth_deps import get_current_user
from auth_policy import current_user_if_required, is_support_admin, require_company_role
from config import settings
from database import get_session
from fastapi import APIRouter, Depends, HTTPException, Request, Response
from fastapi.responses import JSONResponse
from logging_config import logger
from models import Company, CompanyMember, User
from passwords import (
    burn_verify,
    hash_password,
    needs_rehash,
    rehash_password,
    verify_password,
)
from pydantic import Field
from schemas import STRICT, LoginRequest, RequestModel, UserCreateRequest
from security import limiter
from services.platform import (
    login_throttle,
    password_change,
    sessions,
    sso_flow,
    teams,
)
from services.platform.agent_service import AgentService
from services.platform.oidc import SsoError
from sqlalchemy.exc import IntegrityError
from sqlmodel import Session, select

router = APIRouter(prefix="/v1", tags=["Auth"])


REGISTERED = {
    "status": "accepted",
    "detail": "If this email can be used, the account is ready: sign in with it.",
}


def _register_limit() -> str:
    return settings.REGISTER_RATE_LIMIT


def _login_limit() -> str:
    return settings.LOGIN_RATE_LIMIT


@router.post("/auth/register")
@limiter.limit(_register_limit)
async def register_user(
    request: Request,
    req: UserCreateRequest,
    session: Session = Depends(get_session),  # noqa: B008 - FastAPI dependency marker.
):
    """
    Register a new human user for the platform.

    The answer is the same whether or not the email was already registered,
    and costs the same (a bcrypt hash either way), so this route does not
    tell anyone which addresses have accounts (issue #541,
    WSTG-v42-IDNT-04); a taken address costs the same refused insert. An
    existing account is left untouched. The password must have at least
    ``PASSWORD_MIN_LENGTH`` characters; it is hashed with bcrypt and the raw
    value is never stored.
    Rate-limited per client address (``REGISTER_RATE_LIMIT``).
    """
    password_hash = hash_password(req.password)
    # The insert is attempted for a taken address too, and the unique index
    # refuses it: both paths do the same work (one bcrypt hash, one INSERT,
    # one transaction), so neither the answer nor its timing says which
    # addresses have accounts. The email is already normalised (schemas).
    session.add(
        User(email=req.email, full_name=req.full_name, password_hash=password_hash)
    )
    try:
        session.commit()
    except IntegrityError:
        # Taken (or registered by a concurrent request a moment ago): the same
        # answer, and the existing account is left untouched.
        session.rollback()
    return {**REGISTERED, "email": req.email}


def _refuse_locked(email: str) -> None:
    wait = login_throttle.retry_after(email)
    if wait:
        raise HTTPException(
            status_code=429,
            detail="Too many sign-in attempts: try again later.",
            headers={"Retry-After": str(wait)},
        )


@router.post("/auth/login")
@limiter.limit(_login_limit)
async def login(
    request: Request,
    req: LoginRequest,
    response: Response,
    session: Session = Depends(get_session),  # noqa: B008 - FastAPI dependency marker.
):
    """Authenticate a human user: session cookies, or a Bearer access token.

    Limited per client address (``LOGIN_RATE_LIMIT``) and per account, with a
    backoff after repeated failures (``login_throttle``; WSTG-v42-ATHN-03).
    An unknown email, an account without a password and a wrong password get
    the same answer after the same bcrypt work (WSTG-v42-IDNT-04).
    """
    if not auth_tokens.is_auth_configured():
        raise HTTPException(status_code=503, detail="Authentication is not configured")
    _refuse_locked(req.email)

    user = session.exec(select(User).where(User.email == req.email)).first()
    # An SSO-only account has an empty hash: no password verifies against it.
    if user is None or not user.password_hash:
        verified = burn_verify(req.password)
    else:
        verified = verify_password(req.password, user.password_hash)
    if not verified:
        login_throttle.failure(req.email)
        raise HTTPException(status_code=401, detail="Invalid email or password")
    login_throttle.success(req.email)

    # Upgrade a hash written with a lower cost, now that the password is known
    # to be right. Replaces what passlib's deprecated="auto" did.
    if needs_rehash(user.password_hash):
        user.password_hash = rehash_password(req.password)
        session.add(user)
        session.commit()

    # Checked after the password, so the answer says nothing about an
    # account to someone who does not know it (docs/archive/design/SSO.md, T14).
    try:
        sso_flow.check_password_login(session, user)
    except SsoError as exc:
        raise HTTPException(
            status_code=403,
            detail="Your company requires single sign-on: use Sign in with SSO.",
        ) from exc

    if req.token_delivery == "cookie":
        return session_cookies.start(response, session, user, "password")
    # A non-browser client: a short Bearer token, no family, no refresh. It
    # is issued only for a password, never from a session cookie, so a script
    # in the page cannot mint a portable token (docs/archive/design/SESSIONS.md, S1).
    token = auth_tokens.create_access_token(
        user_id=user.id,
        email=user.email,
        expires_delta=timedelta(minutes=settings.ACCESS_TOKEN_EXPIRE_MINUTES),
    )
    response.headers["Cache-Control"] = "no-store"
    return {
        "access_token": token,
        "token_type": "bearer",
        "expires_in": settings.ACCESS_TOKEN_EXPIRE_MINUTES * 60,
    }


def _refresh_limit() -> str:
    return settings.REFRESH_RATE_LIMIT


def _ended(status: int, detail: str) -> JSONResponse:
    """A refusal that also clears the session's cookies."""
    refused = JSONResponse(status_code=status, content={"detail": detail})
    session_cookies.clear(refused)
    return refused


@router.post("/auth/refresh")
@limiter.limit(_refresh_limit)
def refresh_session(
    request: Request,
    response: Response,
    session: Session = Depends(get_session),  # noqa: B008 - FastAPI dependency marker.
):
    """Trade the refresh cookie for a new pair; a reused one ends the family."""
    if not auth_tokens.is_auth_configured():
        raise HTTPException(status_code=503, detail="Authentication is not configured")
    raw = request.cookies.get(session_cookies.REFRESH_COOKIE)
    family = sessions.family_of(session, raw)
    if family is None:
        return _ended(401, "Session expired")
    if not session_cookies.csrf_ok(request, family.csrf_hash):
        raise session_cookies.csrf_refused()
    try:
        issued = sessions.rotate(session, raw)
    except sessions.SessionError as exc:
        if exc.code == "race":
            # Another tab of this browser rotated it a moment ago: its cookies
            # are already in the jar, so the caller retries its request.
            raise HTTPException(
                status_code=409, detail="Session already refreshed"
            ) from exc
        if exc.code == "reuse":
            # A security event: a refresh token was presented twice, so one
            # of the two holders is not the person (RFC 9700 §4.14).
            logger.warning(
                "refresh token reuse: session family %s of user %s revoked",
                family.id,
                family.user_id,
            )
        return _ended(401, "Session expired")
    user = session.get(User, issued.family.user_id)
    if user is None:
        sessions.revoke(session, issued.family, "user_deleted")
        return _ended(401, "Session expired")
    return session_cookies.refreshed(response, user, issued)


def _session_family(request: Request, session: Session):
    """The family the request's refresh cookie, or else its access cookie,
    names."""
    family = sessions.family_of(
        session, request.cookies.get(session_cookies.REFRESH_COOKIE)
    )
    if family is not None:
        return family
    sid = auth_tokens.session_id_of(
        request.cookies.get(session_cookies.SESSION_COOKIE) or ""
    )
    return sessions.active(session, sid)


@router.post("/auth/logout", status_code=204)
def logout(
    request: Request,
    session: Session = Depends(get_session),  # noqa: B008 - FastAPI dependency marker.
):
    """Revoke the session's family and clear its cookies."""
    family = _session_family(request, session)
    if family is not None:
        if not session_cookies.csrf_ok(request, family.csrf_hash):
            raise session_cookies.csrf_refused()
        sessions.revoke(session, family, "logout")
    done = Response(status_code=204)
    session_cookies.clear(done)
    return done


@router.get("/auth/csrf")
def csrf_token(request: Request, response: Response):
    """The CSRF value of the session this browser holds, for a page served
    from another host that cannot read the API's cookie. CORS keeps other
    origins from reading the answer."""
    response.headers["Cache-Control"] = "no-store"
    return {"csrf_token": request.cookies.get(session_cookies.CSRF_COOKIE)}


@router.get("/auth/me")
async def get_me(
    user: User = Depends(get_current_user),  # noqa: B008 - FastAPI dependency marker.
    session: Session = Depends(get_session),  # noqa: B008 - FastAPI dependency marker.
):
    """Return the authenticated user's public profile and company memberships."""
    memberships = session.exec(
        select(CompanyMember).where(CompanyMember.user_id == user.id)
    ).all()
    ids = {membership.company_id for membership in memberships}
    # The company's name with each membership, so the app names the company
    # it switches to instead of "Company <id>".
    names = (
        dict(
            session.exec(
                select(Company.id, Company.company_name).where(Company.id.in_(ids))
            ).all()
        )
        if ids
        else {}
    )
    return {
        "user_id": user.id,
        "email": user.email,
        "full_name": user.full_name,
        "support_admin": is_support_admin(user),
        # Whether there is a password to change (an SSO-only account has
        # none), and the rule a new one follows, for the account page.
        "has_password": bool(user.password_hash),
        "password_min_length": int(settings.PASSWORD_MIN_LENGTH),
        "memberships": [
            {
                "company_id": membership.company_id,
                "company_name": names.get(membership.company_id),
                "human_role": membership.human_role,
                # Raised by the member's teams (never to Owner); what the
                # routes check (auth_policy.require_company_role).
                "effective_role": teams.member_role(
                    session, membership.company_id, user.id
                ),
            }
            for membership in memberships
        ],
    }


class PasswordChangeRequest(RequestModel):
    """Payload for ``POST /v1/auth/password``. The new password's rule
    (``check_new_password``) is applied by the route, so its refusal names a
    code the page can translate."""

    model_config = STRICT

    current_password: str = Field(max_length=password_change.MAX_CURRENT)
    new_password: str = Field(max_length=password_change.MAX_CURRENT)


def _cookie_family(request: Request, session: Session):
    """The live family of a request authenticated by the session cookie;
    ``None`` for a Bearer token, which wins whenever present."""
    if request.headers.get("Authorization"):
        return None
    sid = auth_tokens.session_id_of(
        request.cookies.get(session_cookies.SESSION_COOKIE) or ""
    )
    return sessions.active(session, sid)


@router.post("/auth/password")
@limiter.limit(_login_limit)
def change_password(
    request: Request,
    req: PasswordChangeRequest,
    response: Response,
    session: Session = Depends(get_session),  # noqa: B008 - FastAPI dependency marker.
    user: User | None = Depends(current_user_if_required),  # noqa: B008 - FastAPI dependency marker.
):
    """The signed-in person changes their own password.

    The current password is checked under the sign-in's per-account backoff;
    the new one follows ``check_new_password`` and must differ. Every session
    family of the person is revoked, this one included; a browser session
    gets a new family in the answer's cookies, so only this browser stays
    signed in. Audited as ``password_changed``, with no password or hash.
    An API token, or an owner action acting for the person, is refused.
    Anonymous is 401 even without ``AUTH_REQUIRED``: there is no one whose
    password could change. The body is validated first (mass assignment).
    """
    if user is None:
        raise HTTPException(status_code=401, detail="Not authenticated")
    if (
        getattr(user, "_api_token_company_id", None) is not None
        or getattr(user, "_owner_action_run_id", None) is not None
    ):
        raise HTTPException(
            status_code=403,
            detail={
                "code": "person_only",
                "message": "Only a signed-in person can change a password.",
            },
        )
    family = _cookie_family(request, session)
    method = family.method if family is not None else None
    try:
        changed = password_change.change_own(
            session,
            user,
            req.current_password,
            req.new_password,
            reissue=method is not None,
        )
    except password_change.PasswordChangeRefused as exc:
        headers = {"Retry-After": str(exc.retry_after)} if exc.retry_after else None
        raise HTTPException(
            status_code=exc.status,
            detail={"code": exc.code, "message": exc.message},
            headers=headers,
        ) from exc
    body = {"status": "changed", "sessions_ended": changed.sessions_ended}
    if method is not None:
        body.update(session_cookies.start(response, session, user, method))
    response.headers["Cache-Control"] = "no-store"
    return body


@router.delete("/company/{company_id}/members/{user_id}", status_code=204)
async def remove_member(
    company_id: int,
    user_id: int,
    session: Session = Depends(get_session),  # noqa: B008 - FastAPI dependency marker.
    caller: User | None = Depends(current_user_if_required),  # noqa: B008 - FastAPI dependency marker.
):
    """Remove a member from a company (Owner). The company's last Owner stays.

    The person's browser sessions end at once, in every company, since a
    session is not company-scoped (``docs/archive/design/SESSIONS.md``, S5).
    """
    require_company_role(session, caller, company_id, "Owner")
    member = session.exec(
        select(CompanyMember).where(
            CompanyMember.company_id == company_id,
            CompanyMember.user_id == user_id,
        )
    ).first()
    if member is None:
        raise HTTPException(status_code=404, detail="Not a member of this company")
    if member.human_role == "Owner":
        owners = session.exec(
            select(CompanyMember).where(
                CompanyMember.company_id == company_id,
                CompanyMember.human_role == "Owner",
            )
        ).all()
        if len(owners) <= 1:
            raise HTTPException(
                status_code=409, detail="The company's last Owner cannot be removed"
            )
    role = member.human_role
    session.delete(member)
    left_teams = teams.drop_memberships(
        session,
        company_id,
        user_id,
        by=f"human:{caller.email}" if caller else "human:anonymous",
        actor_id=caller.id if caller else None,
    )
    session.commit()
    ended = sessions.revoke_users(session, [user_id], "member_removed")
    AgentService().audit_interaction(
        session=session,
        company_id=company_id,
        interaction_id=f"members:{company_id}",
        agent_id=f"human:{caller.email}" if caller else "human:anonymous",
        action="member_removed",
        details={"company_id": company_id, "user_id": user_id},
        result={"human_role": role, "sessions_ended": ended, "teams_left": left_teams},
        decision_signature=None,
        cost_tokens=0,
    )
    session.commit()
