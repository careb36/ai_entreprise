"""Run secrets router."""

from auth_policy import current_user_if_required, require_company_role
from database import get_session
from fastapi import APIRouter, Depends, HTTPException
from models import User
from pydantic import BaseModel, Field
from services import run_secrets
from sqlmodel import Session

router = APIRouter(prefix="/v1/run-secrets", tags=["Company"])


class CreateSecretRequest(BaseModel):
    company_id: int
    task_id: int
    name: str = Field(min_length=1, max_length=80)
    value: str = Field(min_length=1)


@router.post("")
async def create_run_secret(
    req: CreateSecretRequest,
    session: Session = Depends(get_session),  # noqa: B008
    user: User | None = Depends(current_user_if_required),  # noqa: B008
):
    require_company_role(session, user, req.company_id, "Manager")
    secret = run_secrets.create(
        session, req.company_id, req.task_id, req.name, req.value
    )
    return {"id": secret.id, "name": secret.name, "task_id": secret.task_id}


@router.get("")
async def list_run_secrets(
    task_id: int,
    session: Session = Depends(get_session),  # noqa: B008
    user: User | None = Depends(current_user_if_required),  # noqa: B008
):
    rows = run_secrets.list_for_task(session, task_id)
    # company check via first row
    company_id = rows[0].company_id if rows else None
    if company_id is not None:
        require_company_role(session, user, company_id, "Viewer")
    return {
        "secrets": [{"id": s.id, "name": s.name, "task_id": s.task_id} for s in rows]
    }


@router.delete("/{secret_id}")
async def delete_run_secret(
    secret_id: int,
    company_id: int,
    session: Session = Depends(get_session),  # noqa: B008
    user: User | None = Depends(current_user_if_required),  # noqa: B008
):
    require_company_role(session, user, company_id, "Manager")
    ok = run_secrets.delete(session, secret_id, company_id)
    if not ok:
        raise HTTPException(status_code=404, detail="Secret not found")
    return {"deleted": True}
