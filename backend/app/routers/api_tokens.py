"""API tokens for external callers."""

from auth_policy import current_user_if_required, require_company_role
from database import get_session
from fastapi import APIRouter, Depends, HTTPException
from models import User
from pydantic import BaseModel, Field
from services import api_tokens
from sqlmodel import Session

router = APIRouter(prefix="/v1/auth/tokens", tags=["Company"])


class CreateTokenRequest(BaseModel):
    company_id: int
    name: str = Field(min_length=1, max_length=80)
    scopes: list[str] = []


@router.post("")
async def create_api_token(
    req: CreateTokenRequest,
    session: Session = Depends(get_session),  # noqa: B008
    user: User | None = Depends(current_user_if_required),  # noqa: B008
):
    require_company_role(session, user, req.company_id, "Manager")
    token, raw = api_tokens.create_token(
        session, req.company_id, req.name, getattr(user, "email", None), req.scopes
    )
    return {
        "id": token.id,
        "name": token.name,
        "scopes": token.scopes,
        "created_at": token.created_at.isoformat() if token.created_at else None,
        "token": raw,
    }


@router.get("")
async def list_api_tokens(
    company_id: int,
    session: Session = Depends(get_session),  # noqa: B008
    user: User | None = Depends(current_user_if_required),  # noqa: B008
):
    require_company_role(session, user, company_id, "Viewer")
    rows = api_tokens.list_tokens(session, company_id)
    return {
        "tokens": [
            {
                "id": t.id,
                "name": t.name,
                "scopes": t.scopes,
                "created_at": t.created_at.isoformat() if t.created_at else None,
                "revoked_at": t.revoked_at.isoformat() if t.revoked_at else None,
                "last_used_at": t.last_used_at.isoformat() if t.last_used_at else None,
            }
            for t in rows
        ]
    }


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
