"""API tokens for external callers."""

from auth_policy import ROLE_RANK, current_user_if_required, require_company_role
from database import get_session
from fastapi import APIRouter, Depends, HTTPException
from models import CompanyMember, User
from pydantic import BaseModel, Field
from services.platform import api_tokens
from sqlmodel import Session, select

router = APIRouter(prefix="/v1/auth/tokens", tags=["Company"])


class CreateTokenRequest(BaseModel):
    company_id: int
    name: str = Field(min_length=1, max_length=80)
    scopes: list[str] = []
    role: str = Field(default=api_tokens.DEFAULT_ROLE, pattern="^(Viewer|Manager)$")
    # None: never expires; otherwise 1 to 365 days.
    expires_in_days: int | None = Field(
        default=api_tokens.DEFAULT_DAYS, ge=1, le=api_tokens.MAX_DAYS
    )


def _serialize(t) -> dict:
    return {
        "id": t.id,
        "name": t.name,
        "scopes": t.scopes,
        "role": t.role,
        "created_by": t.created_by,
        "created_at": t.created_at.isoformat() if t.created_at else None,
        "expires_at": t.expires_at.isoformat() if t.expires_at else None,
        "expired": api_tokens.expired(t),
        "revoked_at": t.revoked_at.isoformat() if t.revoked_at else None,
        "last_used_at": t.last_used_at.isoformat() if t.last_used_at else None,
    }


def _own_role(session: Session, user: User | None, company_id: int) -> str | None:
    if user is None:
        return None
    member = session.exec(
        select(CompanyMember).where(
            CompanyMember.user_id == user.id, CompanyMember.company_id == company_id
        )
    ).first()
    return member.human_role if member else None


@router.post("")
async def create_api_token(
    req: CreateTokenRequest,
    session: Session = Depends(get_session),  # noqa: B008
    user: User | None = Depends(current_user_if_required),  # noqa: B008
):
    require_company_role(session, user, req.company_id, "Manager")
    # A token never mints tokens: a leaked one cannot outlive its own revocation.
    if getattr(user, "_api_token_company_id", None) is not None:
        raise HTTPException(
            status_code=403, detail="An API token cannot create API tokens"
        )
    own = _own_role(session, user, req.company_id)
    if own is not None and ROLE_RANK[req.role] > ROLE_RANK.get(own, 0):
        raise HTTPException(
            status_code=403, detail=f"A {own} cannot create a {req.role} token"
        )
    token, raw = api_tokens.create_token(
        session,
        req.company_id,
        req.name,
        getattr(user, "email", None),
        req.scopes,
        role=req.role,
        expires_in_days=req.expires_in_days,
    )
    return {**_serialize(token), "token": raw}


@router.get("")
async def list_api_tokens(
    company_id: int,
    session: Session = Depends(get_session),  # noqa: B008
    user: User | None = Depends(current_user_if_required),  # noqa: B008
):
    require_company_role(session, user, company_id, "Viewer")
    rows = api_tokens.list_tokens(session, company_id)
    return {"tokens": [_serialize(t) for t in rows]}


@router.post("/{token_id}/revoke")
async def revoke_api_token(
    token_id: int,
    company_id: int,
    session: Session = Depends(get_session),  # noqa: B008
    user: User | None = Depends(current_user_if_required),  # noqa: B008
):
    require_company_role(session, user, company_id, "Manager")
    ok = api_tokens.revoke(session, token_id, company_id)
    if not ok:
        raise HTTPException(status_code=404, detail="Token not found")
    return {"revoked": True}
