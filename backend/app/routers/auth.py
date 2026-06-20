"""
Auth router — human user registration, login, and company membership management.

Endpoints:
- ``POST /v1/auth/register``  — create a new platform user account.
- ``POST /v1/auth/login``     — exchange credentials for a JWT access token.
- ``POST /v1/company/invite`` — invite an existing user to collaborate on a company.

Passwords are hashed with bcrypt via ``passlib`` before storage.
JWT access tokens are signed with HS256 and expire after 8 hours by default
(configurable via the ``JWT_ACCESS_TOKEN_EXPIRE_MINUTES`` env variable).
"""

from fastapi import APIRouter, HTTPException, Depends
from fastapi.security import OAuth2PasswordRequestForm
from sqlmodel import Session, select

from database import get_session
from models import User, CompanyMember
from schemas import UserCreateRequest, InviteMemberRequest
from passlib.context import CryptContext
from auth_dependencies import create_access_token, get_current_user, TokenData

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


@router.post("/auth/login")
async def login(
    form_data: OAuth2PasswordRequestForm = Depends(),
    session: Session = Depends(get_session),
):
    """
    Exchange email + password for a JWT access token.

    Uses the standard OAuth2 password-grant form (``application/x-www-form-urlencoded``
    with ``username`` and ``password`` fields) so the token endpoint is compatible
    with OpenAPI's built-in "Authorize" button and the ``OAuth2PasswordBearer``
    scheme used by ``get_current_user``.

    Returns:
        ``access_token`` (JWT string), ``token_type`` ("bearer"), and a summary
        of the authenticated user.
    """
    user = session.exec(select(User).where(User.email == form_data.username)).first()
    if not user or not pwd_context.verify(form_data.password, user.password_hash):
        raise HTTPException(
            status_code=401,
            detail="Incorrect email or password",
            headers={"WWW-Authenticate": "Bearer"},
        )

    # Determine the highest role this user holds across all company memberships.
    _ROLE_RANK = {"Viewer": 1, "Manager": 2, "Owner": 3}
    memberships = session.exec(
        select(CompanyMember).where(CompanyMember.user_id == user.id)
    ).all()
    role = max(
        (m.human_role for m in memberships if m.human_role in _ROLE_RANK),
        key=lambda r: _ROLE_RANK[r],
        default="Viewer",
    )

    token = create_access_token({
        "sub": user.email,
        "user_id": user.id,
        "role": role,
    })
    return {
        "access_token": token,
        "token_type": "bearer",
        "user_id": user.id,
        "email": user.email,
        "role": role,
    }


@router.post("/company/invite")
async def invite_member(
    req: InviteMemberRequest,
    session: Session = Depends(get_session),
    current_user: TokenData = Depends(get_current_user),
):
    """
    Invite a registered user to collaborate on a company.

    The user must already have an account (identified by email).  The
    ``human_role`` determines which governance capabilities the user has:
    ``"Owner"`` > ``"Manager"`` > ``"Viewer"``.

    Requires an authenticated session (any role).
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
