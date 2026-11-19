"""Enterprise router — multi-tenancy governance, quota management, and analytics.

Endpoints:
- ``POST /v1/init_company``        — bootstrap a new company with agents and credentials.
- ``POST /v1/approve_task``        — retired approval endpoint (410 Gone).
- ``GET  /v1/enterprise/quotas``   — token usage per business unit.
- ``GET  /v1/analytics/summary``   — AI Scrum Master sprint health report.
- ``POST /v1/discover_team``       — auto-detect project stack and suggest an AI team.
- ``GET  /v1/enterprise/lineage``  — time-travel debugging: architectural decisions for a file.
- ``GET  /v1/capabilities``        — public report of which optional backend features are enabled.

The analytics endpoints are in ``routers/enterprise_analytics.py`` and the
agent management in ``routers/enterprise_agents.py``, registered beside this
router in ``main.py``; their names are re-exported here.
"""

import asyncio
from pathlib import Path

from auth_policy import (
    current_user_if_required,
    require_authenticated,
    require_company_role,
    viewer_company_ids,
)
from config import settings
from database import get_session
from fastapi import APIRouter, Depends, HTTPException, Request
from logging_config import logger
from models import BusinessUnit, User
from schemas import (
    CompanyInitRequest,
    DiscoveryRequest,
)
from services.loop import foundation_setup
from services.loop.company_service import CompanyService
from services.planning.discovery_service import DiscoveryService
from services.platform import memory_graph
from services.release import git_setup
from services.workspace.workspace_daemon import (
    DAEMON_UNAVAILABLE_DETAIL,
    DaemonError,
    DaemonUnavailable,
    inspect_project,
    materialized_snapshot,
    without_snapshot,
)
from sqlmodel import Session

from routers.enterprise_agents import (  # noqa: F401 - re-exported
    _ROLE_RESPONSIBILITIES,
    _agent_company,
    _build_template_prompt,
    create_agent,
    delete_agent,
    generate_agent_prompt,
    list_agents,
    update_agent,
)
from routers.enterprise_analytics import (  # noqa: F401 - re-exported
    _ANCHOR_COMMIT,
    _ANCHOR_DATE,
    get_orchestration_health,
    get_project_health,
    quota_usage_history,
)

router = APIRouter(prefix="/v1", tags=["Enterprise"])


@router.post("/init_company", tags=["Core"])
async def init_company(
    req: CompanyInitRequest,
    request: Request,
    session: Session = Depends(get_session),  # noqa: B008 - FastAPI dependency marker.
    user: User | None = Depends(current_user_if_required),  # noqa: B008 - FastAPI dependency marker.
):
    """Bootstrap a new company (CompanyService); its Git workflow and its foundation sprint follow the folder, as the daemon observed it (``services/release/git_setup.py``, ``services/loop/foundation_setup.py``)."""
    require_authenticated(user)
    base = git_setup.clean_base(req.git_base_branch)
    # One inspection of the folder serves both observations — the Git
    # workflow (a repository or not) and the foundation sprint (a repository
    # with code or not). Both are the daemon's; the request's own word only
    # asks for the sprint or declines it.
    observed = await foundation_setup.observe_folder(
        getattr(request.app.state, "nats_client", None), req.project_path
    )
    foundation = foundation_setup.decide(observed, req.foundation_sprint)
    service = CompanyService(session)
    try:
        result = service.init_company(
            company_name=req.company_name,
            agents=req.agents,
            default_scope_size=req.default_scope_size,
            autonomy_level=req.autonomy_level,
            permissions=req.permissions,
            project_path=req.project_path,
            detected_stack=req.detected_stack,
            owner_user_id=user.id if user is not None else None,
            start_now=req.start_now,
            git_workflow=git_setup.workflow_for(
                git_setup.is_repository(
                    observed["project"], (req.project_path or "").strip()
                ),
                req.git_workflow,
            ),
            git_base_branch=base,
            doc_language=req.doc_language,
            plan_foundation=foundation["planned"],
        )
        # The decision and its reason, with the daemon's observation beside
        # it (services/loop/foundation_setup.py).
        result["foundation_sprint"] = foundation
        return result
    except Exception as exc:  # noqa: BLE001 - Translate service failures to a sanitized API error.
        logger.error(f"Company initialization failed: {exc}")
        raise HTTPException(
            status_code=500, detail="Internal server error during initialization"
        )


@router.post("/approve_task", tags=["Enterprise"])
async def approve_task():
    raise HTTPException(
        status_code=410,
        detail="POST /v1/approve_task was removed; use POST /v1/agent_runs/{id}/approve (issue #42)",
    )


@router.get("/enterprise/quotas", tags=["Enterprise"])
async def get_quotas(
    company_id: int,
    session: Session = Depends(get_session),  # noqa: B008 - FastAPI dependency marker.
    user: User | None = Depends(current_user_if_required),  # noqa: B008 - FastAPI dependency marker.
):
    """Return token usage and limits for all business units of a company."""
    require_company_role(session, user, company_id, "Viewer")
    service = CompanyService(session)
    return service.get_quotas(company_id)


@router.post("/enterprise/quotas/{bu_id}/reset", tags=["Enterprise"])
async def reset_quota(
    bu_id: int,
    session: Session = Depends(get_session),  # noqa: B008 - FastAPI dependency marker.
    user: User | None = Depends(current_user_if_required),  # noqa: B008 - FastAPI dependency marker.
):
    """Reset a business unit's token usage to zero, at the start of a billing cycle."""
    if user is not None:
        unit = session.get(BusinessUnit, bu_id)
        if not unit:
            raise HTTPException(
                status_code=404, detail=f"Business Unit {bu_id} not found"
            )
        require_company_role(session, user, unit.company_id, "Manager")
    service = CompanyService(session)
    try:
        return service.reset_quota(bu_id)
    except ValueError as exc:
        raise HTTPException(status_code=404, detail=str(exc))


@router.get("/capabilities", tags=["Enterprise"])
async def get_capabilities(request: Request) -> dict[str, bool | str | None]:
    """
    Report which optional backend features are available.

    Public like ``/health``: it exposes configuration flags, never company
    data, so the frontend can hide navigation for disabled features.
    """
    return {
        "agent_graph": bool(settings.AGENT_GRAPH_ENABLED),
        # Memory search on pgvector, and the knowledge graph apart (issue #328).
        "memory": getattr(request.app.state, "memory_client", None) is not None,
        "memory_graph": bool(getattr(request.app.state, "memory_graph", False))
        and memory_graph.graph_enabled(),
        "github": bool(settings.GITHUB_TOKEN),
        "auth_required": bool(settings.AUTH_REQUIRED),
        "support_agent": bool(settings.SUPPORT_AGENT_ENABLED),
        # Where GitHub can reach this backend; null when not configured.
        "public_api_url": (settings.PUBLIC_API_URL or "").rstrip("/") or None,
    }


@router.get("/models", tags=["Enterprise"])
async def list_models(request: Request):
    """
    Return the live LLM model catalogue from the proxy.

    Proxies the ``/v1/models`` response from the internal LLM proxy so that
    the frontend can build a dynamic model selector without hardcoding model
    names or tiers.  The proxy refreshes each configured provider's
    catalogue (DeepSeek by default; Gemini, Qwen and Nebius when their keys
    are set) every hour and classifies models by capability tier.

    Response shape::

        {
          "models": [
            {"id": "deepseek-v4-pro", "provider": "deepseek", "tier": "strategic", "description": "..."},
            ...
          ],
          "orchestrator": "deepseek-flash",
          "refreshed_at": 1712345678.0
        }
    """
    llm_client = getattr(request.app.state, "llm_client", None)
    if llm_client is None:
        raise HTTPException(status_code=503, detail="LLM proxy not available")
    try:
        data = await asyncio.to_thread(llm_client.get_available_models)
        if not data:
            raise HTTPException(
                status_code=502, detail="LLM proxy returned empty catalogue"
            )
        return data
    except HTTPException:
        raise
    except Exception as exc:  # noqa: BLE001 - Translate upstream catalogue failures to an API error.
        logger.error("Failed to fetch model catalogue: %s", exc)
        raise HTTPException(status_code=502, detail="Could not reach LLM proxy")


@router.post("/discover_team", tags=["Enterprise"])
async def discover_team(
    req: DiscoveryRequest,
    request: Request,
    session: Session = Depends(get_session),  # noqa: B008 - FastAPI dependency marker.
    user: User | None = Depends(current_user_if_required),  # noqa: B008 - FastAPI dependency marker.
):
    """
    Scan a project path to detect its technology stack and suggest an AI team.

    Phase 1 (deterministic): filesystem walk + config-file parsing.
    Phase 2 (AI): LLM proxy reasons about the stack and suggests a team with
    per-role justifications. Falls back to rule-based team if LLM unavailable.

    If ``company_id`` is provided, the result is persisted in the
    ``project_discovery`` table, and it becomes the folder that company's
    agents write to, so that needs a Manager of the company.
    """
    require_authenticated(user)
    if req.company_id is not None:
        require_company_role(session, user, req.company_id, "Manager")
    llm_proxy_url = None
    llm_client = getattr(request.app.state, "llm_client", None)
    if llm_client:
        llm_proxy_url = llm_client.base_url
    service = DiscoveryService(session, llm_proxy_url=llm_proxy_url)

    # The project is on the user's machine: the workspace daemon there checks
    # the folder and sends a snapshot of its files, which is scanned here.
    project = None
    daemon_missing = False
    try:
        project = await inspect_project(
            getattr(request.app.state, "nats_client", None),
            req.project_path,
            snapshot=True,
        )
    except DaemonUnavailable:
        daemon_missing = True
    except DaemonError as exc:
        raise HTTPException(
            status_code=422 if exc.status in {400, 404} else exc.status,
            detail=exc.message,
        )

    if project is not None and not project.get("is_dir"):
        host = (project.get("host") or {}).get("hostname") or "the workspace machine"
        raise HTTPException(
            status_code=422,
            detail=f"Folder not found on {host}: {project.get('path')}",
        )

    def _scan_snapshot():
        with materialized_snapshot(project.get("snapshot") or {}) as mirror:
            return service.discover_project_team(
                company_id=req.company_id,
                project_path=mirror,
                stored_path=project["path"],
                project_name=project.get("name"),
            )

    try:
        # In a thread pool, so the event loop stays free during the scan + LLM call.
        if project is not None:
            discovery, detected_stack, suggested_team = await asyncio.to_thread(
                _scan_snapshot
            )
            project_path = project["path"]
        else:
            # No daemon: a folder this server can see (e.g. a Linux host
            # mounted at the same path) is scanned directly.
            discovery, detected_stack, suggested_team = await asyncio.to_thread(
                service.discover_project_team,
                company_id=req.company_id,
                project_path=req.project_path,
            )
            project_path = str(Path(req.project_path).expanduser().resolve())
    except ValueError as exc:
        if daemon_missing:
            raise HTTPException(status_code=503, detail=DAEMON_UNAVAILABLE_DETAIL)
        raise HTTPException(status_code=422, detail=str(exc))
    if req.company_id is not None:  # the folder it writes to: its workflow follows it
        git_setup.record(session, req.company_id, project, project_path)
    return {
        "discovery_id": discovery.id if discovery else None,
        "detected_stack": detected_stack,
        "suggested_team": suggested_team,
        "project_path": project_path,
        "project": without_snapshot(project) if project is not None else None,
    }


@router.get("/enterprise/lineage", tags=["Enterprise"])
async def get_code_lineage(
    file_path: str,
    company_id: int | None = None,
    session: Session = Depends(get_session),  # noqa: B008 - FastAPI dependency marker.
    user: User | None = Depends(current_user_if_required),  # noqa: B008 - FastAPI dependency marker.
):
    """
    Time-Travel Debugging: retrieve all architectural decisions that modified a file.

    Queries the Neo4j Temporal Knowledge Graph for ``Episodic`` nodes connected
    to a ``Semantic`` node whose ``id`` ends with *file_path* via a
    ``MODIFIED_BY`` relationship.  Returns up to 10 most recent decisions.
    Without the graph (``NEO4J_ENABLED=false``) the answer is ``recorded:
    false`` with the reason (``services/memory_graph.lineage``).
    """
    require_authenticated(user)
    if company_id is not None:
        require_company_role(session, user, company_id, "Viewer")
    company_ids = viewer_company_ids(session, user) if user is not None else []
    return memory_graph.lineage(
        file_path, company_id, company_ids, scoped=user is not None
    )
