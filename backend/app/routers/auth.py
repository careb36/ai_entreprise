"""
Auth router — human user registration and company membership management.

Endpoints:
- ``POST /v1/auth/register``  — create a new platform user account.
- ``POST /v1/auth/login``     — authenticate; start a browser session in
  cookies, or (``token_delivery: "bearer"``) return a Bearer access token.
- ``POST /v1/auth/refresh``   — rotate the session's refresh token (cookies).
- ``POST /v1/auth/logout``    — revoke the session's family and clear cookies.
- ``GET /v1/auth/csrf``       — the session's CSRF value, for a page that
  cannot read the API host's cookie.
- ``GET /v1/auth/me``         — return the authenticated user's profile.
- ``POST /v1/company/invite`` — invite an existing user to collaborate on a company.
- ``DELETE /v1/company/{id}/members/{user_id}`` — remove a member (Owner);
  ends that person's sessions.

Browser sessions (issue #524): ``docs/design/SESSIONS.md``.

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
from models import CompanyMember, User
from passwords import hash_password, needs_rehash, rehash_password, verify_password
from schemas import InviteMemberRequest, LoginRequest, UserCreateRequest
from security import limiter
from services.platform import sessions, sso_flow, teams
from services.platform.agent_service import AgentService
from services.platform.oidc import SsoError
from sqlmodel import Session, select

router = APIRouter(prefix="/v1", tags=["Auth"])


@router.post("/auth/register")
async def register_user(
    req: UserCreateRequest,
    session: Session = Depends(get_session),  # noqa: B008 - FastAPI dependency marker.
):
    """
    Register a new human user for the platform.

    Checks for a duplicate email before inserting.  The plain-text password
    is hashed with bcrypt and the raw value is never stored.

    Returns the new user's ``user_id`` and ``email``.
    """
    existing = session.exec(select(User).where(User.email == req.email)).first()
    if existing:
        raise HTTPException(status_code=400, detail="Email already registered.")
    user = User(
        email=req.email,
        full_name=req.full_name,
        password_hash=hash_password(req.password),
    )
    session.add(user)
    session.commit()
    session.refresh(user)
    return {"user_id": user.id, "email": user.email}


@router.post("/auth/login")
async def login(
    req: LoginRequest,
    response: Response,
    session: Session = Depends(get_session),  # noqa: B008 - FastAPI dependency marker.
):
    """Authenticate a human user: session cookies, or a Bearer access token."""
    if not auth_tokens.is_auth_configured():
        raise HTTPException(status_code=503, detail="Authentication is not configured")

    user = session.exec(select(User).where(User.email == req.email)).first()
    # An SSO-only account has an empty hash: no password verifies against it.
    if (
        user is None
        or not user.password_hash
        or not verify_password(req.password, user.password_hash)
    ):
        raise HTTPException(status_code=401, detail="Invalid email or password")

    # Upgrade a hash written with a lower cost, now that the password is known
    # to be right. Replaces what passlib's deprecated="auto" did.
    if needs_rehash(user.password_hash):
        user.password_hash = rehash_password(req.password)
        session.add(user)
        session.commit()

    # Checked after the password, so the answer says nothing about an
    # account to someone who does not know it (docs/design/SSO.md, T14).
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
    # in the page cannot mint a portable token (docs/design/SESSIONS.md, S1).
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
    return {
        "user_id": user.id,
        "email": user.email,
        "full_name": user.full_name,
        "support_admin": is_support_admin(user),
        "memberships": [
            {
                "company_id": membership.company_id,
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


@router.post("/company/invite")
async def invite_member(
    req: InviteMemberRequest,
    session: Session = Depends(get_session),  # noqa: B008 - FastAPI dependency marker.
    caller: User | None = Depends(current_user_if_required),  # noqa: B008 - FastAPI dependency marker.
):
    """
    Invite a registered user to collaborate on a company.

    The user must already have an account (identified by email).  The
    ``human_role`` determines which governance capabilities the user has:
    ``"Owner"`` > ``"Manager"`` > ``"Viewer"``.
    """
    require_company_role(session, caller, req.company_id, "Owner")
    user = session.exec(select(User).where(User.email == req.email)).first()
    if not user:
        raise HTTPException(status_code=404, detail="User not found")
    existing = session.exec(
        select(CompanyMember).where(
            CompanyMember.company_id == req.company_id,
            CompanyMember.user_id == user.id,
        )
    ).first()
    if existing:
        raise HTTPException(
            status_code=409,
            detail=f"{req.email} is already a member as {existing.human_role}",
        )
    member = CompanyMember(
        company_id=req.company_id,
        user_id=user.id,
        human_role=req.human_role,
    )
    session.add(member)
    session.commit()
    return {"status": "success", "message": f"{req.email} invited as {req.human_role}"}


@router.delete("/company/{company_id}/members/{user_id}", status_code=204)
async def remove_member(
    company_id: int,
    user_id: int,
    session: Session = Depends(get_session),  # noqa: B008 - FastAPI dependency marker.
    caller: User | None = Depends(current_user_if_required),  # noqa: B008 - FastAPI dependency marker.
):
    """Remove a member from a company (Owner). The company's last Owner stays.

    The person's browser sessions end at once, in every company, since a
    session is not company-scoped (``docs/design/SESSIONS.md``, S5).
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
    left_teams = teams.drop_memberships(session, company_id, user_id)
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
