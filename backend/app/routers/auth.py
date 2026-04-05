"""
Auth router — human user registration and company membership management.

Endpoints:
- ``POST /v1/auth/register``  — create a new platform user account.
- ``POST /v1/company/invite`` — invite an existing user to collaborate on a company.

Passwords are hashed with bcrypt via ``passlib`` before storage.
JWT-based session authentication is planned for a future phase.
"""

from fastapi import APIRouter, HTTPException, Depends
from sqlmodel import Session, select, text

from database import get_session
from models import User, CompanyMember
from schemas import UserCreateRequest, InviteMemberRequest
from passlib.context import CryptContext

router = APIRouter(prefix="/v1", tags=["Auth"])

# bcrypt context — "deprecated='auto'" automatically re-hashes old entries
# when a stronger algorithm becomes the default in future passlib releases.
pwd_context = CryptContext(schemes=["bcrypt"], deprecated="auto")


@router.post("/auth/register")
async def register_user(req: UserCreateRequest, session: Session = Depends(get_session)):
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


@router.post("/company/invite")
async def invite_member(req: InviteMemberRequest, session: Session = Depends(get_session)):
    """
    Invite a registered user to collaborate on a company.

    The user must already have an account (identified by email).  The
    ``human_role`` determines which governance capabilities the user has:
    ``"Owner"`` > ``"Manager"`` > ``"Viewer"``.
    """
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
