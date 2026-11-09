"""
Console producers router: which terminal coding agent produces which role's tasks.

- ``GET /v1/companies/{id}/console_producers`` (Viewer): the company's
  ``{role: cli}``, the CLIs and roles it may name, whether the Git workflow
  is on (a console producer needs it) and each CLI's cooldown.
- ``PUT /v1/companies/{id}/console_producers`` (Manager): replace them; an
  empty value turns a role back to its model. Refused (422) for an unknown
  role or CLI, or while the Git workflow is off. Accepted with a warning
  while the company's sandbox is ``host`` or has its network off: no CLI runs
  there (``console_clis.sandbox_refusal``), the model produces the tasks.

The run itself is ``services/console_producer.py``.
"""

from datetime import UTC, datetime
from typing import Any

from auth_policy import current_user_if_required, require_company_role
from database import get_session
from fastapi import APIRouter, Depends, HTTPException
from models import AssistantRuntimeState, Company, User
from pydantic import BaseModel, Field
from services import console_assistants, console_clis, sandbox
from sqlmodel import Session, select

router = APIRouter(prefix="/v1", tags=["Core"])


class ConsoleProducersRequest(BaseModel):
    producers: dict[str, Any] = Field(default_factory=dict)


def _cooldowns(session: Session, company_id: int) -> dict[str, str | None]:
    """When each CLI may run again after a quota stop or a missing binary."""
    now = datetime.now(UTC)
    rows = session.exec(
        select(AssistantRuntimeState).where(
            AssistantRuntimeState.company_id == company_id,
            AssistantRuntimeState.assistant_key.in_(list(console_clis.CLIS)),
        )
    ).all()
    found = {}
    for row in rows:
        deadline = console_assistants.normalize_utc(row.cooldown_until)
        if deadline and deadline > now:
            found[row.assistant_key] = deadline.isoformat()
    return {cli: found.get(cli) for cli in console_clis.CLIS}


def _payload(session: Session, company: Company) -> dict:
    cooldowns = _cooldowns(session, company.id)
    box = sandbox.request_for(company)
    refusal = console_clis.sandbox_refusal(box)
    return {
        "company_id": company.id,
        "producers": dict(company.console_producers or {}),
        "roles": list(console_clis.ROLES),
        "clis": [
            {
                "id": cli,
                "label": spec["label"],
                "command": console_clis.command_prefix(cli),
                "cooldown_until": cooldowns[cli],
            }
            for cli, spec in console_clis.CLIS.items()
        ],
        "git_workflow": bool(company.git_workflow),
        "sandbox": {"mode": box["mode"], "network": bool(box.get("network"))},
        # Accepted, but no CLI runs while the sandbox refuses it: the model
        # produces the tasks and the board says why.
        "warnings": [refusal] if refusal and company.console_producers else [],
    }


def _company(session: Session, company_id: int) -> Company:
    company = session.get(Company, company_id)
    if company is None:
        raise HTTPException(status_code=404, detail="Company not found")
    return company


@router.get("/companies/{company_id}/console_producers")
async def get_console_producers(
    company_id: int,
    session: Session = Depends(get_session),  # noqa: B008 - FastAPI dependency marker.
    user: User | None = Depends(current_user_if_required),  # noqa: B008 - FastAPI dependency marker.
):
    """Which CLI produces which role's tasks (Viewer)."""
    require_company_role(session, user, company_id, "Viewer")
    return _payload(session, _company(session, company_id))


@router.put("/companies/{company_id}/console_producers")
async def set_console_producers(
    company_id: int,
    req: ConsoleProducersRequest,
    session: Session = Depends(get_session),  # noqa: B008 - FastAPI dependency marker.
    user: User | None = Depends(current_user_if_required),  # noqa: B008 - FastAPI dependency marker.
):
    """Name the CLI that produces a role's tasks instead of the model (Manager)."""
    require_company_role(session, user, company_id, "Manager")
    company = _company(session, company_id)
    try:
        chosen = console_clis.validate(req.producers, bool(company.git_workflow))
    except (TypeError, ValueError) as e:
        raise HTTPException(status_code=422, detail=str(e)) from e
    company.console_producers = chosen or None
    session.add(company)
    session.commit()
    return _payload(session, company)
