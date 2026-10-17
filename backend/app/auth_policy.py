"""Opt-in authentication and company membership policy."""

from fastapi import Depends, HTTPException, Request
from sqlmodel import Session, select

from auth_deps import bearer, get_current_user
from config import settings
from database import get_session
from models import CompanyMember, User


ROLE_RANK = {"Viewer": 1, "Manager": 2, "Owner": 3}


async def current_user_if_required(
    request: Request, session: Session = Depends(get_session)
) -> User | None:
    if not settings.AUTH_REQUIRED:
        return None
    return get_current_user(credentials=await bearer(request), session=session)


def require_company_role(
    session: Session, user: User | None, company_id: int, minimum: str
) -> None:
    if user is None:
        return
    member = session.exec(
        select(CompanyMember).where(
            CompanyMember.user_id == user.id,
            CompanyMember.company_id == company_id,
        )
    ).first()
    if member is None:
        raise HTTPException(status_code=403, detail="Not a member of this company")
    if ROLE_RANK.get(member.human_role, 0) < ROLE_RANK[minimum]:
        raise HTTPException(status_code=403, detail=f"Requires {minimum} role")


def require_authenticated(user: User | None) -> None:
    if settings.AUTH_REQUIRED and user is None:
        raise HTTPException(
            status_code=401,
            detail="Not authenticated",
            headers={"WWW-Authenticate": "Bearer"},
        )
