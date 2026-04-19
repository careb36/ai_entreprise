"""
Enterprise router — multi-tenancy governance, quota management, and analytics.

Endpoints:
- ``POST /v1/init_company``        — bootstrap a new company with agents and credentials.
- ``POST /v1/approve_task``        — human-in-the-loop approval for AI-generated tasks.
- ``GET  /v1/enterprise/quotas``   — token usage per business unit.
- ``GET  /v1/analytics/summary``   — AI Scrum Master sprint health report.
- ``POST /v1/discover_team``       — auto-detect project stack and suggest an AI team.
- ``GET  /v1/enterprise/lineage``  — time-travel debugging: architectural decisions for a file.
"""

import asyncio
from fastapi import APIRouter, HTTPException, Depends, Request
from sqlmodel import Session

from database import get_session, get_neo4j_driver
from services.discovery_service import DiscoveryService
from services.company_service import CompanyService
from schemas import CompanyInitRequest, ApprovalRequest, DiscoveryRequest
from logging_config import logger

router = APIRouter(prefix="/v1", tags=["Enterprise"])


@router.post("/init_company", tags=["Core"])
async def init_company(req: CompanyInitRequest, session: Session = Depends(get_session)):
    """
    Bootstrap a new company tenant with its AI agents and project credentials.
    Delegates to CompanyService for transactional logic and team planning.
    """
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
        )
        return result
    except Exception as exc:
        logger.error(f"Company initialization failed: {exc}")
        raise HTTPException(status_code=500, detail="Internal server error during initialization")


@router.post("/approve_task", tags=["Enterprise"])
async def approve_task(req: ApprovalRequest, session: Session = Depends(get_session)):
    """
    Human-in-the-Loop governance: approve or reject an AI-generated task.
    """
    service = CompanyService(session)
    try:
        return service.approve_task(req.task_id, req.approver_name, req.decision, req.feedback)
    except ValueError as exc:
        raise HTTPException(status_code=404, detail=str(exc))


@router.get("/enterprise/quotas", tags=["Enterprise"])
async def get_quotas(company_id: int, session: Session = Depends(get_session)):
    """
    Return token usage and limits for all business units of a company.
    """
    service = CompanyService(session)
    return service.get_quotas(company_id)


@router.get("/analytics/summary", tags=["Enterprise"])
async def get_project_health(sprint_id: int, session: Session = Depends(get_session)):
    """
    AI Scrum Master: generate a sprint health report from audit and backlog data.
    """
    service = CompanyService(session)
    return service.get_project_health(sprint_id)


@router.get("/models", tags=["Enterprise"])
async def list_models(request: Request):
    """
    Return the live LLM model catalogue from the proxy.

    Proxies the ``/v1/models`` response from the internal LLM proxy so that
    the frontend can build a dynamic model selector without hardcoding model
    names or tiers.  The proxy refreshes its catalogue from the Gemini API
    every hour and classifies models by capability tier.

    Response shape::

        {
          "models": [
            {"id": "gemini-2.5-pro", "provider": "gemini", "tier": "strategic", "description": "..."},
            ...
          ],
          "orchestrator": "gemini-2.5-flash",
          "refreshed_at": 1712345678.0
        }
    """
    llm_client = getattr(request.app.state, "llm_client", None)
    if llm_client is None:
        raise HTTPException(status_code=503, detail="LLM proxy not available")
    try:
        data = await asyncio.to_thread(llm_client.get_available_models)
        if not data:
            raise HTTPException(status_code=502, detail="LLM proxy returned empty catalogue")
        return data
    except HTTPException:
        raise
    except Exception as exc:
        logger.error("Failed to fetch model catalogue: %s", exc)
        raise HTTPException(status_code=502, detail="Could not reach LLM proxy")


@router.post("/discover_team", tags=["Enterprise"])
async def discover_team(req: DiscoveryRequest, request: Request, session: Session = Depends(get_session)):
    """
    Scan a project path to detect its technology stack and suggest an AI team.

    Phase 1 (deterministic): filesystem walk + config-file parsing.
    Phase 2 (AI): LLM proxy reasons about the stack and suggests a team with
    per-role justifications. Falls back to rule-based team if LLM unavailable.

    If ``company_id`` is provided, the result is persisted in the
    ``project_discovery`` table.
    """
    llm_proxy_url = None
    llm_client = getattr(request.app.state, "llm_client", None)
    if llm_client:
        llm_proxy_url = llm_client.base_url
    service = DiscoveryService(session, llm_proxy_url=llm_proxy_url)
    try:
        # Run the synchronous filesystem scan + LLM call in a thread pool so
        # the async event loop stays free while the work runs.
        discovery, detected_stack, suggested_team = await asyncio.to_thread(
            service.discover_project_team,
            company_id=req.company_id,
            project_path=req.project_path,
        )
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc))
    return {
        "discovery_id": discovery.id if discovery else None,
        "detected_stack": detected_stack,
        "suggested_team": suggested_team,
    }


@router.get("/enterprise/lineage", tags=["Enterprise"])
async def get_code_lineage(file_path: str):
    """
    Time-Travel Debugging: retrieve all architectural decisions that modified a file.

    Queries the Neo4j Temporal Knowledge Graph for ``Episodic`` nodes connected
    to a ``Semantic`` node whose ``id`` ends with *file_path* via a
    ``MODIFIED_BY`` relationship.  Returns up to 10 most recent decisions.
    """
    driver = get_neo4j_driver()
    try:
        cypher = """
        MATCH (e:Episodic)-[r:MODIFIED_BY]->(s)
        WHERE s.id ENDS WITH $file_path
        RETURN e.id AS engram_id, e.agent_role AS role, e.summary AS decision, e.created_at AS timestamp
        ORDER BY e.created_at DESC LIMIT 10
        """
        with driver.session() as neo_session:
            result = neo_session.run(cypher, file_path=file_path)
            lineage = [dict(record) for record in result]
        if not lineage:
            return {"file": file_path, "status": "No historical engrams found.", "lineage": []}
        return {"file": file_path, "status": "success", "lineage": lineage}
    except Exception as e:
        logger.error(f"Lineage query failed: {e}")
        raise HTTPException(status_code=500, detail="Neo4j Time Travel query failed.")
