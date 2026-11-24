"""Planning a sprint: the PO's plan from a prompt or an approved
specification, and the team it suggests for the request.

- ``POST /v1/plan_sprint``: the plan, ``suggested_team`` (a subset of the
  company's hired roles, every one of them without a usable answer),
  ``team_reasons`` and ``hired_roles``; with ``team``, only those roles get
  tasks (``services/planning/sprint_team.py``).
- ``POST /v1/plan_self_improvement_sprint``: the factory's own next slice,
  with ``team`` too.
- Both carry ``objective`` (the person's, whole) and ``planner_fallback``:
  when the planner's model did not answer usably, the reason as observed and
  a ``plan_id`` the backend recorded (``services/planning/planner_fallback.py``);
  ``start_sprint`` with that ``plan_id`` records it on the sprint.
- ``already_done_claim`` (what the PO says is already done) and
  ``already_done_observed`` (what the backend observed of its reads), kept
  apart and recorded under the same ``plan_id`` (``already_done``).
- ``GET``/``PUT /v1/companies/{id}/team_selection`` (Viewer / Manager): how a
  sprint's team is chosen, ``suggested`` (the default) or ``automatic``.

Included by ``routers/sprint.py``, which starts the sprint the plan describes.
"""

import asyncio

from auth_policy import current_user_if_required, require_company_role
from database import get_session
from fastapi import APIRouter, Depends, HTTPException, Request
from models import Company, User
from schemas import (
    SCOPE_LIMITS,
    RequestModel,
    SelfImprovementSprintRequest,
    SprintPlanRequest,
)
from services.planning import (
    already_done,
    planner_fallback,
    spec_planning,
    sprint_team,
)
from services.planning.sprint_planner import (
    build_self_improvement_objective,
    plan_execution_sprint,
    project_listing,
)
from services.planning.sprint_planner import (
    plan_self_improvement_sprint as build_self_improvement_plan,
)
from services.tasks import dependencies
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
        # The person's objective, whole (``Sprint.objective`` once started).
        "objective": plan.get("objective"),
        # Not None when the planner's model did not answer usably: the reason
        # as the backend observed it, and the ``plan_id`` of its record.
        "planner_fallback": plan.get("planner_fallback"),
        "plan_id": plan.get("plan_id"),
        # Items of the objective the scope left out of a fallback plan.
        "unplanned_items": plan.get("unplanned_items", []),
        # What the PO says is already done (its claim), apart from what the
        # backend observed of its reads (``services/planning/already_done.py``).
        "already_done_claim": plan.get("already_done_claim"),
        "already_done_observed": plan.get("already_done_observed"),
    }


def _recorded(session: Session, company_id: int, kind: str, plan: dict) -> dict:
    """*plan* with the ``plan_id`` of its records: its fallback, when it fell
    back, and the PO's already-done claim with its observed reads."""
    fallback = planner_fallback.record_plan(
        session, company_id, kind, plan.get("planner_fallback")
    )
    return {
        **plan,
        "plan_id": already_done.record_plan(session, company_id, kind, plan, fallback),
    }


async def _listing(request: Request, discovery) -> str:
    """The project's top levels for the PO, through the workspace daemon: the
    backend does not see the user's files (``sprint_context.project_listing``)."""
    return await project_listing(
        getattr(request.app.state, "nats_client", None),
        discovery.project_path if discovery else None,
    )


@router.post("/plan_sprint")
async def plan_sprint(
    req: SprintPlanRequest,
    request: Request,
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
        # The person's note, whole: their input beside the specification.
        plan["objective"] = req.prompt or None
        plan = _recorded(session, req.company_id, "specification", plan)
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

    # In a thread: the PO may read the project through the daemon, which is
    # reached on this event loop (``planning_reads``).
    listing = await _listing(request, discovery)
    plan = await asyncio.to_thread(
        plan_execution_sprint,
        company_name=company.company_name,
        objective=req.prompt,
        scope_size=scope_size,
        agents=agent_dicts,
        detected_stack=discovery.detected_stack if discovery else None,
        project_path=discovery.project_path if discovery else None,
        team=team,
        file_listing=listing,
    )
    plan = _recorded(session, req.company_id, "execution", plan)
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
    request: Request,
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

    listing = await _listing(request, discovery)
    plan = await asyncio.to_thread(  # In a thread, as plan_sprint's.
        build_self_improvement_plan,
        company_name=company.company_name,
        objective=objective,
        scope_size=scope_size,
        agents=agent_dicts,
        detected_stack=discovery.detected_stack if discovery else None,
        project_path=discovery.project_path if discovery else None,
        team=team,
        file_listing=listing,
    )
    plan = _recorded(session, req.company_id, "self_improvement", plan)

    return _serialize_plan_response(
        plan,
        scope_size,
        f"Sprint - Improve {company.company_name}",
        objective,
        hired,
        sprint_team.selection_of(company),
    )


class TeamSelectionRequest(RequestModel):
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
