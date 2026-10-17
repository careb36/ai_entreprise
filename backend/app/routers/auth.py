"""
Auth router — human user registration and company membership management.

Endpoints:
- ``POST /v1/auth/register``  — create a new platform user account.
- ``POST /v1/auth/login``     — authenticate and issue an access token.
- ``GET /v1/auth/me``         — return the authenticated user's profile.
- ``POST /v1/company/invite`` — invite an existing user to collaborate on a company.

Passwords are hashed with bcrypt via ``passlib`` before storage.
Sensitive-route enforcement is gated by AUTH_REQUIRED.
"""

from datetime import timedelta

import auth_tokens
from auth_deps import get_current_user
from auth_policy import current_user_if_required, require_company_role
from config import settings
from database import get_session
from fastapi import APIRouter, Depends, HTTPException
from models import CompanyMember, User
from passlib.context import CryptContext
from schemas import InviteMemberRequest, LoginRequest, UserCreateRequest
from sqlmodel import Session, select

router = APIRouter(prefix="/v1", tags=["Auth"])

# bcrypt context — "deprecated='auto'" automatically re-hashes old entries
# when a stronger algorithm becomes the default in future passlib releases.
pwd_context = CryptContext(schemes=["bcrypt"], deprecated="auto")


@router.post("/auth/register")
async def register_user(
    req: UserCreateRequest, session: Session = Depends(get_session)
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
        password_hash=pwd_context.hash(req.password),
    )
    session.add(user)
    session.commit()
    session.refresh(user)
    return {"user_id": user.id, "email": user.email}


@router.post("/auth/login")
async def login(req: LoginRequest, session: Session = Depends(get_session)):
    """Authenticate a human user and return a signed access token."""
    if not auth_tokens.is_auth_configured():
        raise HTTPException(status_code=503, detail="Authentication is not configured")

    user = session.exec(select(User).where(User.email == req.email)).first()
    if user is None or not pwd_context.verify(req.password, user.password_hash):
        raise HTTPException(status_code=401, detail="Invalid email or password")

    expires_delta = timedelta(minutes=settings.ACCESS_TOKEN_EXPIRE_MINUTES)
    token = auth_tokens.create_access_token(
        user_id=user.id,
        email=user.email,
        expires_delta=expires_delta,
    )
    return {
        "access_token": token,
        "token_type": "bearer",
        "expires_in": settings.ACCESS_TOKEN_EXPIRE_MINUTES * 60,
    }


@router.get("/auth/me")
async def get_me(
    user: User = Depends(get_current_user), session: Session = Depends(get_session)
):
    """Return the authenticated user's public profile and company memberships."""
    memberships = session.exec(
        select(CompanyMember).where(CompanyMember.user_id == user.id)
    ).all()
    return {
        "user_id": user.id,
        "email": user.email,
        "full_name": user.full_name,
        "memberships": [
            {"company_id": membership.company_id, "human_role": membership.human_role}
            for membership in memberships
        ],
    }


@router.post("/company/invite")
async def invite_member(
    req: InviteMemberRequest,
    session: Session = Depends(get_session),
    caller: User | None = Depends(current_user_if_required),
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
    member = CompanyMember(
        company_id=req.company_id,
        user_id=user.id,
        human_role=req.human_role,
    )
    session.add(member)
    session.commit()
    return {"status": "success", "message": f"{req.email} invited as {req.human_role}"}
