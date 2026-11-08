"""Run secrets router."""

import re

from auth_policy import current_user_if_required, require_company_role
from database import get_session
from fastapi import APIRouter, Depends, HTTPException
from models import BacklogItem, Sprint, User
from pydantic import BaseModel, Field
from services import run_secrets
from sqlmodel import Session

router = APIRouter(prefix="/v1/run-secrets", tags=["Company"])

_NAME_RE = re.compile(r"^[A-Za-z_][A-Za-z0-9_]*$")


class CreateSecretRequest(BaseModel):
    company_id: int
    task_id: int
    name: str = Field(min_length=1, max_length=80)
    value: str = Field(min_length=1)


def _task_company(session: Session, task_id: int) -> int | None:
    item = session.get(BacklogItem, task_id)
    if item is None:
        return None
    sprint = session.get(Sprint, item.sprint_id)
    if sprint is None:
        return None
    return sprint.company_id


@router.post("")
async def create_run_secret(
    req: CreateSecretRequest,
    session: Session = Depends(get_session),  # noqa: B008
    user: User | None = Depends(current_user_if_required),  # noqa: B008
):
    if not _NAME_RE.match(req.name):
        raise HTTPException(status_code=422, detail="invalid secret name")
    require_company_role(session, user, req.company_id, "Manager")
    task_company = _task_company(session, req.task_id)
    if task_company is None or task_company != req.company_id:
        raise HTTPException(status_code=404, detail="Task not found")
    try:
        secret = run_secrets.create(
            session, req.company_id, req.task_id, req.name, req.value
        )
    except run_secrets.RunSecretError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    return {"id": secret.id, "name": secret.name, "task_id": secret.task_id}


@router.get("")
async def list_run_secrets(
    task_id: int,
    session: Session = Depends(get_session),  # noqa: B008
    user: User | None = Depends(current_user_if_required),  # noqa: B008
):
    company_id = _task_company(session, task_id)
    if company_id is None:
        raise HTTPException(status_code=404, detail="Task not found")
    require_company_role(session, user, company_id, "Viewer")
    rows = run_secrets.list_for_task(session, task_id, company_id)
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
