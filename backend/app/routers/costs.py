"""What a run, a task and a sprint cost, in tokens and US cents (Viewer).

``services/run_cost.py`` computes it from the audit log's KPI turn rows and
the model catalogue's prices; only usage the provider reported is priced,
estimated tokens come apart, and an unknown price is ``cost_cents: null``
with the reason. ``GET /v1/sprints`` carries each sprint's cost too.
"""

from auth_policy import current_user_if_required, require_company_role
from database import get_session
from fastapi import APIRouter, Depends, HTTPException, Query
from models import BacklogItem, Sprint, User
from models.agent_run import AgentRun
from services import run_cost
from sqlmodel import Session

router = APIRouter(prefix="/v1", tags=["Costs"])


@router.get("/agent_runs/{run_id}/cost")
def agent_run_cost(
    run_id: int,
    company_id: int = Query(...),
    session: Session = Depends(get_session),  # noqa: B008
    user: User | None = Depends(current_user_if_required),  # noqa: B008
):
    require_company_role(session, user, company_id, "Viewer")
    run = session.get(AgentRun, run_id)
    if run is None or run.company_id != company_id:
        raise HTTPException(status_code=404, detail="Agent run not found")
    return run_cost.run_cost(session, run)


def _task(session: Session, task_id: int) -> tuple[BacklogItem, Sprint]:
    item = session.get(BacklogItem, task_id)
    sprint = session.get(Sprint, item.sprint_id) if item is not None else None
    if item is None or sprint is None:
        raise HTTPException(status_code=404, detail="Task not found")
    return item, sprint


@router.get("/tasks/{task_id}/cost")
def task_cost(
    task_id: int,
    session: Session = Depends(get_session),  # noqa: B008
    user: User | None = Depends(current_user_if_required),  # noqa: B008
):
    _, sprint = _task(session, task_id)
    require_company_role(session, user, sprint.company_id, "Viewer")
    return run_cost.task_cost(session, sprint.company_id, task_id)


@router.get("/sprints/{sprint_id}/cost")
def sprint_cost(
    sprint_id: int,
    session: Session = Depends(get_session),  # noqa: B008
    user: User | None = Depends(current_user_if_required),  # noqa: B008
):
    sprint = session.get(Sprint, sprint_id)
    if sprint is None:
        raise HTTPException(status_code=404, detail="Sprint not found")
    require_company_role(session, user, sprint.company_id, "Viewer")
    totals = run_cost.sprint_costs(session, sprint.company_id, [sprint_id])
    return {
        "sprint_id": sprint_id,
        **totals[sprint_id],
        "tasks": run_cost.sprint_task_costs(session, sprint.company_id, sprint_id),
    }
