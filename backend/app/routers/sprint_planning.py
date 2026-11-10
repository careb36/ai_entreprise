"""Planning a sprint: the PO's plan from a prompt or an approved
specification, and the team it suggests for the request.

- ``POST /v1/plan_sprint``: the plan, ``suggested_team`` (a subset of the
  company's hired roles, every one of them without a usable answer),
  ``team_reasons`` and ``hired_roles``; with ``team``, only those roles get
  tasks (``services/planning/sprint_team.py``).
- ``POST /v1/plan_self_improvement_sprint``: the factory's own next slice,
  with ``team`` too.
- ``GET``/``PUT /v1/companies/{id}/team_selection`` (Viewer / Manager): how a
  sprint's team is chosen, ``suggested`` (the default) or ``automatic``.

Included by ``routers/sprint.py``, which starts the sprint the plan describes.
"""

import asyncio

from auth_policy import current_user_if_required, require_company_role
from database import get_session
from fastapi import APIRouter, Depends, HTTPException
from models import Company, User
from pydantic import BaseModel
from schemas import SCOPE_LIMITS, SelfImprovementSprintRequest, SprintPlanRequest
from services import dependencies
from services.planning import spec_planning, sprint_team
from services.planning.sprint_planner import (
    build_self_improvement_objective,
    plan_execution_sprint,
)
from services.planning.sprint_planner import (
    plan_self_improvement_sprint as build_self_improvement_plan,
)
from sqlmodel import Session

router = APIRouter()


def _refused(call, *args):
    """Call into ``spec_planning`` or ``dependencies``; a refusal is the response's status."""
    try:
        return call(*args)
    except (
        spec_planning.PlanRefused,
        dependencies.DependencyError,
        sprint_team.TeamRefused,
    ) as exc:
        raise HTTPException(status_code=exc.status, detail=exc.detail) from exc


def _specification(session: Session, company_id: int, spec_id: int):
    return _refused(spec_planning.approved_specification, session, company_id, spec_id)


def _serialize_plan_response(
    plan: dict,
    scope_size: str,
    fallback_name: str,
    fallback_goal: str,
    hired: list,
    selection: str = sprint_team.SUGGESTED,
):
    return {
        # Whether the form asks the Manager to confirm the team or shows it.
        "team_selection": selection,
        # The team the PO suggests (every hired role without a usable answer)
        # and the roles the Manager may choose from (services/planning/sprint_team.py).
        "suggested_team": plan.get("suggested_team") or hired,
        "team_reasons": plan.get("team_reasons") or {},
        "hired_roles": hired,
        "sprint_name": plan.get("sprint_name", fallback_name),
        "goal": plan.get("goal", fallback_goal),
        "scope_size": scope_size,
        "tasks": plan.get("tasks", []),
        # Criteria of the specification no task covers (planned from one).
        "uncovered": plan.get("uncovered", []),
        "specification_id": plan.get("specification_id"),
    }


@router.post("/plan_sprint")
async def plan_sprint(
    req: SprintPlanRequest,
    session: Session = Depends(get_session),  # noqa: B008 - FastAPI dependency marker.
    user: User | None = Depends(current_user_if_required),  # noqa: B008 - FastAPI dependency marker.
):
    """Turn a strategic prompt into a scoped sprint backlog before sprint creation."""
    require_company_role(session, user, req.company_id, "Manager")
    scope_size = req.scope_size.upper()
    if scope_size not in SCOPE_LIMITS:
        raise HTTPException(
            status_code=422, detail="Invalid scope_size. Valid values: S, M, L"
        )
    company, agent_dicts, discovery = _refused(
        spec_planning.planning_context_of, session, req.company_id
    )
    hired = sprint_team.roles_of(agent_dicts)
    team = _refused(sprint_team.check_team, req.team, hired)
    if req.specification_id is not None:
        spec = _specification(session, req.company_id, req.specification_id)
        # What the PO is asked is read here, with the request's session; only
        # the call to the proxy runs in a thread (a session is not shared).
        request = spec_planning.prepare_plan(
            session,
            spec,
            scope_size,
            SCOPE_LIMITS[scope_size],
            agent_dicts,
            req.prompt,
            team,
        )
        plan = await asyncio.to_thread(spec_planning.plan_from, request)
        plan["specification_id"] = spec.id
        return _serialize_plan_response(
            plan,
            scope_size,
            f"Sprint - {spec.title[:40]}",
            spec.summary or spec.title,
            hired,
            sprint_team.selection_of(company),
        )
    if not req.prompt.strip():
        raise HTTPException(status_code=422, detail="Prompt is required")

    plan = plan_execution_sprint(
        company_name=company.company_name,
        objective=req.prompt,
        scope_size=scope_size,
        agents=agent_dicts,
        detected_stack=discovery.detected_stack if discovery else None,
        project_path=discovery.project_path if discovery else None,
        team=team,
    )
    return _serialize_plan_response(
        plan,
        scope_size,
        f"Sprint - {company.company_name}",
        req.prompt.strip(),
        hired,
        sprint_team.selection_of(company),
    )


@router.post("/plan_self_improvement_sprint")
async def plan_self_improvement_sprint(
    req: SelfImprovementSprintRequest,
    session: Session = Depends(get_session),  # noqa: B008 - FastAPI dependency marker.
    user: User | None = Depends(current_user_if_required),  # noqa: B008 - FastAPI dependency marker.
):
    """Create a bounded autonomous-improvement plan from the repository's own context."""
    require_company_role(session, user, req.company_id, "Manager")
    scope_size = req.scope_size.upper()
    if scope_size not in SCOPE_LIMITS:
        raise HTTPException(
            status_code=422, detail="Invalid scope_size. Valid values: S, M, L"
        )

    company, agent_dicts, discovery = _refused(
        spec_planning.planning_context_of, session, req.company_id
    )
    hired = sprint_team.roles_of(agent_dicts)
    team = _refused(sprint_team.check_team, req.team, hired)
    objective = build_self_improvement_objective(
        company_name=company.company_name,
        detected_stack=discovery.detected_stack if discovery else None,
    )

    plan = build_self_improvement_plan(
        company_name=company.company_name,
        objective=objective,
        scope_size=scope_size,
        agents=agent_dicts,
        detected_stack=discovery.detected_stack if discovery else None,
        project_path=discovery.project_path if discovery else None,
        team=team,
    )

    return _serialize_plan_response(
        plan,
        scope_size,
        f"Sprint - Improve {company.company_name}",
        objective,
        hired,
        sprint_team.selection_of(company),
    )


class TeamSelectionRequest(BaseModel):
    team_selection: str


def _company(session: Session, company_id: int) -> Company:
    company = session.get(Company, company_id)
    if company is None:
        raise HTTPException(status_code=404, detail="Company not found")
    return company


@router.get("/companies/{company_id}/team_selection")
async def get_team_selection(
    company_id: int,
    session: Session = Depends(get_session),  # noqa: B008 - FastAPI dependency marker.
    user: User | None = Depends(current_user_if_required),  # noqa: B008 - FastAPI dependency marker.
):
    """How the company's sprint teams are chosen (Viewer)."""
    require_company_role(session, user, company_id, "Viewer")
    company = _company(session, company_id)
    return {
        "company_id": company_id,
        "team_selection": sprint_team.selection_of(company),
    }


@router.put("/companies/{company_id}/team_selection")
async def set_team_selection(
    company_id: int,
    req: TeamSelectionRequest,
    session: Session = Depends(get_session),  # noqa: B008 - FastAPI dependency marker.
    user: User | None = Depends(current_user_if_required),  # noqa: B008 - FastAPI dependency marker.
):
    """Choose how sprint teams are chosen (Manager): ``suggested`` (the PO
    suggests, a Manager confirms) or ``automatic`` (the PO's suggestion)."""
    require_company_role(session, user, company_id, "Manager")
    if req.team_selection not in sprint_team.TEAM_SELECTIONS:
        raise HTTPException(
            status_code=422,
            detail=f"team_selection is one of: {', '.join(sprint_team.TEAM_SELECTIONS)}",
        )
    company = _company(session, company_id)
    company.team_selection = req.team_selection
    session.add(company)
    session.commit()
    return {"company_id": company_id, "team_selection": company.team_selection}
