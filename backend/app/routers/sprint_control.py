"""
Sprint control router — who decides when a sprint's agents work.

Endpoints (Manager):
- ``POST /v1/sprints/{id}/dispatch`` — queue the sprint's ``todo`` tasks again.
- ``POST /v1/sprints/{id}/start``    — start a ``planned`` sprint.
- ``POST /v1/sprints/{id}/pause``    — pause: nothing is dispatched, reviewed or retried.
- ``POST /v1/sprints/{id}/resume``   — resume a paused sprint (also one the worker
  paused for lack of credit) and dispatch its waiting tasks.
- ``POST /v1/sprints/{id}/stop``     — stop for good; no follow-up sprint opens.
- ``GET/PUT /v1/companies/{id}/autonomy`` — read (Viewer) or change the autonomy level.
- ``GET/PUT /v1/companies/{id}/stack_profile`` — read (Viewer) or replace the
  company's overrides of the detected stack profile (``services/stack_profiles.py``).

The states and what they allow are in ``services/loop/sprint_control.py``.
"""

from typing import Any

from auth_policy import current_user_if_required, require_company_role
from config import settings
from database import get_session
from fastapi import APIRouter, Depends, HTTPException, Request
from logging_config import logger
from models import Company, Sprint, User
from pydantic import BaseModel, Field
from services.loop import sprint_control
from services.release import git_setup
from services.tools import research_tools
from services.workspace import sandbox, stack_profiles
from sqlmodel import Session

router = APIRouter(prefix="/v1", tags=["Core"])


@router.post("/sprints/{sprint_id}/dispatch")
async def dispatch_sprint_tasks(
    sprint_id: int,
    session: Session = Depends(get_session),  # noqa: B008 - FastAPI dependency marker.
    user: User | None = Depends(current_user_if_required),  # noqa: B008 - FastAPI dependency marker.
):
    """
    Dispatch all ``todo`` backlog items in a sprint to their assigned agents.

    Creates an ``OutboxEvent`` for each eligible item.  The background worker
    publishes these to NATS as ``task.assign`` messages and the task worker
    picks them up.  Items already dispatched (non-todo status) are skipped.
    """
    sprint = session.get(Sprint, sprint_id)
    if not sprint:
        raise HTTPException(status_code=404, detail="Sprint not found")

    require_company_role(session, user, sprint.company_id, "Manager")
    if sprint_control.is_halted(sprint.status):
        raise HTTPException(status_code=409, detail=f"Sprint is {sprint.status}")
    dispatched = sprint_control.dispatch_todo(session, sprint)
    session.commit()
    if not dispatched:
        return {"dispatched": 0, "message": "No todo items to dispatch"}
    logger.info("Dispatched %d tasks for sprint %d", dispatched, sprint_id)
    return {"dispatched": dispatched, "sprint_id": sprint_id}


def _managed_sprint(session: Session, sprint_id: int, user: User | None) -> Sprint:
    sprint = session.get(Sprint, sprint_id)
    if not sprint:
        raise HTTPException(status_code=404, detail="Sprint not found")
    require_company_role(session, user, sprint.company_id, "Manager")
    return sprint


@router.post("/sprints/{sprint_id}/start")
async def start_planned_sprint(
    sprint_id: int,
    session: Session = Depends(get_session),  # noqa: B008 - FastAPI dependency marker.
    user: User | None = Depends(current_user_if_required),  # noqa: B008 - FastAPI dependency marker.
):
    """Start a ``planned`` sprint: it becomes active and its tasks are dispatched."""
    sprint = _managed_sprint(session, sprint_id, user)
    if sprint.status != sprint_control.PLANNED:
        raise HTTPException(
            status_code=409,
            detail=f"Only a planned sprint can start; this one is {sprint.status}",
        )
    sprint.status = sprint_control.ACTIVE
    session.add(sprint)
    dispatched = sprint_control.dispatch_todo(session, sprint)
    session.commit()
    return {"status": sprint.status, "dispatched": dispatched}


@router.post("/sprints/{sprint_id}/stop")
async def stop_sprint(
    sprint_id: int,
    session: Session = Depends(get_session),  # noqa: B008 - FastAPI dependency marker.
    user: User | None = Depends(current_user_if_required),  # noqa: B008 - FastAPI dependency marker.
):
    """Stop a sprint for good: unfinished tasks are blocked and no follow-up opens."""
    sprint = _managed_sprint(session, sprint_id, user)
    if sprint.status in ("completed", sprint_control.CANCELLED):
        raise HTTPException(
            status_code=409, detail=f"Sprint is already {sprint.status}"
        )
    stopped = sprint_control.stop(session, sprint)
    session.commit()
    return {"status": sprint.status, "stopped_tasks": stopped}


@router.post("/sprints/{sprint_id}/pause")
async def pause_sprint(
    sprint_id: int,
    session: Session = Depends(get_session),  # noqa: B008 - FastAPI dependency marker.
    user: User | None = Depends(current_user_if_required),  # noqa: B008 - FastAPI dependency marker.
):
    """Pause a running sprint: nothing is dispatched, reviewed or retried."""
    sprint = _managed_sprint(session, sprint_id, user)
    if sprint.status == sprint_control.PAUSED:
        return {"status": "already_paused"}
    if not sprint_control.pause(session, sprint):
        raise HTTPException(status_code=409, detail=f"Sprint is {sprint.status}")
    session.commit()
    return {"status": "paused"}


@router.post("/sprints/{sprint_id}/resume")
async def resume_sprint(
    sprint_id: int,
    session: Session = Depends(get_session),  # noqa: B008 - FastAPI dependency marker.
    user: User | None = Depends(current_user_if_required),  # noqa: B008 - FastAPI dependency marker.
):
    """Resume a paused sprint; its waiting tasks are dispatched again."""
    sprint = _managed_sprint(session, sprint_id, user)
    if sprint.status != sprint_control.PAUSED:
        return {"status": sprint.status}
    sprint.status = sprint_control.ACTIVE
    sprint.pause_reason = None
    session.add(sprint)
    dispatched = sprint_control.dispatch_todo(session, sprint)
    session.commit()
    return {"status": "active", "dispatched": dispatched}


class AutonomyRequest(BaseModel):
    autonomy_level: int = Field(ge=1, le=3)


@router.get("/companies/{company_id}/autonomy")
async def get_autonomy(
    company_id: int,
    session: Session = Depends(get_session),  # noqa: B008 - FastAPI dependency marker.
    user: User | None = Depends(current_user_if_required),  # noqa: B008 - FastAPI dependency marker.
):
    """The company's autonomy level (Viewer)."""
    require_company_role(session, user, company_id, "Viewer")
    company = session.get(Company, company_id)
    if company is None:
        raise HTTPException(status_code=404, detail="Company not found")
    return {"company_id": company_id, "autonomy_level": company.autonomy_level}


@router.put("/companies/{company_id}/autonomy")
async def set_autonomy(
    company_id: int,
    req: AutonomyRequest,
    session: Session = Depends(get_session),  # noqa: B008 - FastAPI dependency marker.
    user: User | None = Depends(current_user_if_required),  # noqa: B008 - FastAPI dependency marker.
):
    """Change the company's autonomy level (Manager).

    1: every approved task waits for a person and Sprint 0 never starts on
    its own; 2: only high-risk deliveries wait; 3: also follow-up sprints and
    procedure adoption run on the company's own.
    """
    require_company_role(session, user, company_id, "Manager")
    company = session.get(Company, company_id)
    if company is None:
        raise HTTPException(status_code=404, detail="Company not found")
    company.autonomy_level = req.autonomy_level
    session.add(company)
    session.commit()
    return {"company_id": company_id, "autonomy_level": company.autonomy_level}


class GitWorkflowRequest(BaseModel):
    git_workflow: bool
    # Where a task's branch starts; empty: what the repository has checked out.
    git_base_branch: str | None = Field(default=None, max_length=200)


def _git_payload(company: Company) -> dict:
    return {
        "company_id": company.id,
        "git_workflow": company.git_workflow,
        "git_base_branch": company.git_base_branch,
    }


class ResearchHostsRequest(BaseModel):
    # Hosts the research tools may read beside the defaults; exact host names.
    hosts: list[str] = Field(default_factory=list, max_length=100)


def _research_payload(company: Company) -> dict:
    own = [str(h) for h in (company.research_hosts or [])]
    return {
        "company_id": company.id,
        "enabled": settings.RESEARCH_TOOLS_ENABLED,
        "roles": sorted(research_tools.research_roles()),
        "hosts": own,
        "default_hosts": sorted(research_tools.allowed_hosts()),
    }


@router.get("/companies/{company_id}/research")
async def get_research_hosts(
    company_id: int,
    session: Session = Depends(get_session),  # noqa: B008 - FastAPI dependency marker.
    user: User | None = Depends(current_user_if_required),  # noqa: B008 - FastAPI dependency marker.
):
    """The sources the company's research tools may read (Viewer): the
    deployment's defaults and the hosts the company added."""
    require_company_role(session, user, company_id, "Viewer")
    company = session.get(Company, company_id)
    if company is None:
        raise HTTPException(status_code=404, detail="Company not found")
    return _research_payload(company)


@router.put("/companies/{company_id}/research")
async def set_research_hosts(
    company_id: int,
    req: ResearchHostsRequest,
    session: Session = Depends(get_session),  # noqa: B008 - FastAPI dependency marker.
    user: User | None = Depends(current_user_if_required),  # noqa: B008 - FastAPI dependency marker.
):
    """Replace the hosts the company adds to the research tools' sources
    (Manager). Exact host names; nothing removes a default."""
    require_company_role(session, user, company_id, "Manager")
    company = session.get(Company, company_id)
    if company is None:
        raise HTTPException(status_code=404, detail="Company not found")
    hosts: list[str] = []
    for raw in req.hosts:
        host = raw.strip().lower()
        if not research_tools.valid_host(host):
            raise HTTPException(status_code=422, detail=f"not a host name: {raw}")
        if host not in hosts:
            hosts.append(host)
    company.research_hosts = hosts
    session.add(company)
    session.commit()
    return _research_payload(company)


class SandboxRequest(BaseModel):
    # host, container or bwrap; empty: the deployment's DELIVERY_SANDBOX.
    mode: str | None = Field(default=None, max_length=20)
    # Environment variables a sandboxed run may see, by name.
    env: list[str] = Field(default_factory=list, max_length=50)


def _sandbox_payload(company: Company) -> dict:
    request = sandbox.request_for(company)
    return {
        "company_id": company.id,
        "mode": company.delivery_sandbox,
        "effective_mode": request["mode"],
        "default_mode": sandbox.mode_for(None),
        "env": [str(n) for n in (company.sandbox_env or [])],
        "limits": {
            k: request[k]
            for k in ("image", "network", "cpus", "memory_mb", "pids_limit")
        },
    }


@router.get("/companies/{company_id}/sandbox")
async def get_sandbox(
    company_id: int,
    session: Session = Depends(get_session),  # noqa: B008 - FastAPI dependency marker.
    user: User | None = Depends(current_user_if_required),  # noqa: B008 - FastAPI dependency marker.
):
    """Where the company's agent-written code runs (Viewer)."""
    require_company_role(session, user, company_id, "Viewer")
    company = session.get(Company, company_id)
    if company is None:
        raise HTTPException(status_code=404, detail="Company not found")
    return _sandbox_payload(company)


@router.put("/companies/{company_id}/sandbox")
async def set_sandbox(
    company_id: int,
    req: SandboxRequest,
    session: Session = Depends(get_session),  # noqa: B008 - FastAPI dependency marker.
    user: User | None = Depends(current_user_if_required),  # noqa: B008 - FastAPI dependency marker.
):
    """Set where the company's agent-written code runs, and which environment
    variables (by name) a sandboxed run may see (Manager)."""
    require_company_role(session, user, company_id, "Manager")
    company = session.get(Company, company_id)
    if company is None:
        raise HTTPException(status_code=404, detail="Company not found")
    mode = (req.mode or "").strip().lower() or None
    if mode is not None and mode not in sandbox.MODES:
        raise HTTPException(status_code=422, detail=f"not a sandbox mode: {req.mode}")
    names: list[str] = []
    for raw in req.env:
        name = raw.strip()
        if not sandbox.valid_env_name(name):
            raise HTTPException(status_code=422, detail=f"not a variable name: {raw}")
        if name not in names:
            names.append(name)
    company.delivery_sandbox = mode
    company.sandbox_env = names
    session.add(company)
    session.commit()
    return _sandbox_payload(company)


class StackProfileRequest(BaseModel):
    # install, test, lint, start, e2e (an empty string turns the step off),
    # port, probe_path and stack; a field left out keeps the detected value.
    profile: dict[str, Any] = Field(default_factory=dict)


def _stack_profile_payload(company: Company) -> dict:
    return {
        "company_id": company.id,
        "overrides": dict(company.stack_profile or {}),
        "effective_without_detection": stack_profiles.resolve(
            None, company.stack_profile
        ),
        "stacks": [
            {k: v for k, v in p.items() if k != "markers"}
            for p in stack_profiles.PROFILES
        ],
        "installed_manifests": dict(company.dependency_installs or {}),
    }


@router.get("/companies/{company_id}/stack_profile")
async def get_stack_profile(
    company_id: int,
    session: Session = Depends(get_session),  # noqa: B008 - FastAPI dependency marker.
    user: User | None = Depends(current_user_if_required),  # noqa: B008 - FastAPI dependency marker.
):
    """The company's overrides of the detected stack profile, the stacks the
    detection knows, and the manifests its last installs saw (Viewer)."""
    require_company_role(session, user, company_id, "Viewer")
    company = session.get(Company, company_id)
    if company is None:
        raise HTTPException(status_code=404, detail="Company not found")
    return _stack_profile_payload(company)


@router.put("/companies/{company_id}/stack_profile")
async def set_stack_profile(
    company_id: int,
    req: StackProfileRequest,
    session: Session = Depends(get_session),  # noqa: B008 - FastAPI dependency marker.
    user: User | None = Depends(current_user_if_required),  # noqa: B008 - FastAPI dependency marker.
):
    """Replace the company's overrides of the detected stack profile (Manager)."""
    require_company_role(session, user, company_id, "Manager")
    company = session.get(Company, company_id)
    if company is None:
        raise HTTPException(status_code=404, detail="Company not found")
    try:
        overrides = stack_profiles.validate_overrides(req.profile)
    except ValueError as e:
        raise HTTPException(status_code=422, detail=str(e)) from e
    company.stack_profile = overrides or None
    session.add(company)
    session.commit()
    return _stack_profile_payload(company)


@router.get("/companies/{company_id}/git")
async def get_git_workflow(
    company_id: int,
    request: Request,
    check: bool = False,
    session: Session = Depends(get_session),  # noqa: B008 - FastAPI dependency marker.
    user: User | None = Depends(current_user_if_required),  # noqa: B008 - FastAPI dependency marker.
):
    """Whether the company's deliveries are commits on a branch per task (Viewer).

    With ``check``, also ``base_status``: whether the folder is a repository
    and how its base branch stands against its remote, as the daemon observed
    it now (``services/release/git_setup.py``), or ``state: unknown`` with the reason.
    """
    require_company_role(session, user, company_id, "Viewer")
    company = session.get(Company, company_id)
    if company is None:
        raise HTTPException(status_code=404, detail="Company not found")
    if not check:
        return _git_payload(company)
    status = await git_setup.base_status(request, session, company)
    return _git_payload(company) | {"base_status": status}


@router.put("/companies/{company_id}/git")
async def set_git_workflow(
    company_id: int,
    req: GitWorkflowRequest,
    session: Session = Depends(get_session),  # noqa: B008 - FastAPI dependency marker.
    user: User | None = Depends(current_user_if_required),  # noqa: B008 - FastAPI dependency marker.
):
    """Turn the Git workflow on or off, and name its base branch (Manager).

    On: each task's deliveries are commits on ``fabrique/task-<id>``, in a
    worktree, never in the person's checkout (``services/workspace/git_workflow.py``).
    """
    require_company_role(session, user, company_id, "Manager")
    company = session.get(Company, company_id)
    if company is None:
        raise HTTPException(status_code=404, detail="Company not found")
    base = git_setup.clean_base(req.git_base_branch)
    company.git_workflow = req.git_workflow
    company.git_base_branch = base
    session.add(company)
    session.commit()
    return _git_payload(company)
