from fastapi import APIRouter, HTTPException, Depends
from sqlmodel import Session, select, text

from database import get_session
from models import User, CompanyMember
from schemas import UserCreateRequest, InviteMemberRequest
from passlib.context import CryptContext

router = APIRouter(prefix="/v1", tags=["Auth"])
pwd_context = CryptContext(schemes=["bcrypt"], deprecated="auto")


@router.post("/auth/register")
async def register_user(req: UserCreateRequest, session: Session = Depends(get_session)):
    """Register a new human user for the platform."""
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
    """Invite a human user to collaborate on a company/project."""
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
