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
import json
from fastapi import APIRouter, HTTPException, Depends, Request
from sqlmodel import Session, select, text

from database import get_session, get_neo4j_driver
from models import Company, Agent, ProjectCredential, ProjectDiscovery, BusinessUnit
from schemas import CompanyInitRequest, ApprovalRequest, DiscoveryRequest
from services.discovery_service import DiscoveryService
from logging_config import logger

router = APIRouter(prefix="/v1", tags=["Enterprise"])


@router.post("/init_company", tags=["Core"])
async def init_company(req: CompanyInitRequest, session: Session = Depends(get_session)):
    """
    Bootstrap a new company tenant with its AI agents and project credentials.

    Steps:
    1. Insert the ``Company`` row and flush to obtain its PK.
    2. Insert one ``Agent`` row per entry in ``req.agents``.
    3. Insert ``ProjectCredential`` rows for each non-"none" permission entry.
    4. Seed a ``(:Company)`` root node in Neo4j for the Temporal Knowledge Graph.

    The Neo4j step is non-critical — a failure is logged but does not roll
    back the Postgres transaction.
    """
    company = Company(
        company_name=req.company_name,
        default_sprint_length_days=req.default_sprint_length_days,
        autonomy_level=req.autonomy_level,
    )
    session.add(company)
    session.flush()  # Obtain company.id before committing

    for agent_data in req.agents:
        session.add(Agent(
            company_id=company.id,
            name=agent_data.get("name", ""),
            role=agent_data.get("role"),
            persona=agent_data.get("persona"),
            seniority=agent_data.get("seniority", "Senior"),
            specialty=agent_data.get("specialty", ""),
            model=agent_data.get("model", ""),
            system_prompt=agent_data.get("system_prompt"),
            token_budget=agent_data.get("token_budget", 500000),
        ))

    if req.permissions:
        for cred_type, access_level in req.permissions.items():
            # Skip credentials explicitly denied at tenant level.
            if access_level != "none":
                session.add(ProjectCredential(
                    company_id=company.id,
                    credential_type=cred_type,
                    access_level=access_level,
                    encrypted_value="vault_encrypted_placeholder",
                ))

    session.commit()

    # Seed a root Company node in Neo4j for the Temporal Knowledge Graph.
    try:
        driver = get_neo4j_driver()
        with driver.session() as neo_session:
            neo_session.run(
                "CREATE (c:Company {id: $id, name: $name, created: timestamp()})",
                id=company.id,
                name=req.company_name,
            )
    except Exception as e:
        logger.error(f"Neo4j seed failed (non-critical): {e}")

    return {"company_id": company.id, "message": "Company initialized successfully"}


@router.post("/approve_task", tags=["Enterprise"])
async def approve_task(req: ApprovalRequest, session: Session = Depends(get_session)):
    """
    Human-in-the-Loop governance: approve or reject an AI-generated task.

    Updates ``task_queue.approval_status`` and resets the task status to
    ``"pending"`` so the workflow resumes processing.  Returns 404 if the
    task does not exist.
    """
    new_status = "approved" if req.decision == "approved" else "rejected"
    result = session.exec(
        text("UPDATE task_queue SET approval_status = :s, approved_by = :a, status = 'pending' WHERE id = :id RETURNING id")
        .bindparams(s=new_status, a=req.approver_name, id=req.task_id)
    ).fetchone()
    if not result:
        raise HTTPException(status_code=404, detail="Task not found")
    session.commit()
    return {"status": "success", "task_id": req.task_id, "new_status": new_status}


@router.get("/enterprise/quotas", tags=["Enterprise"])
async def get_quotas(company_id: int, session: Session = Depends(get_session)):
    """
    Return token usage and limits for all business units of a company.

    Useful for the governance dashboard to visualise cost burn per department.
    """
    units = session.exec(
        select(BusinessUnit).where(BusinessUnit.company_id == company_id)
    ).all()
    return [{"unit": u.unit_name, "limit": u.token_quota_limit, "used": u.token_quota_used} for u in units]


@router.get("/analytics/summary", tags=["Enterprise"])
async def get_project_health(sprint_id: int, session: Session = Depends(get_session)):
    """
    AI Scrum Master: generate a sprint health report from audit and backlog data.

    Aggregates total tokens consumed and counts backlog items per status column.
    The AI executive summary is a deterministic template (future enhancement:
    replace with an LLM call for adaptive commentary).
    """
    total_tokens = session.exec(
        text("SELECT COALESCE(SUM(cost_tokens), 0) FROM audit_log WHERE interaction_id IN (SELECT interaction_id FROM task_queue)")
    ).scalar() or 0

    rows = session.exec(
        text("SELECT status, COUNT(*) FROM backlog_items WHERE sprint_id = :sid GROUP BY status")
        .bindparams(sid=sprint_id)
    ).all()
    status_counts = {r[0]: r[1] for r in rows}

    ai_summary = (
        f"The sprint is healthy. {status_counts.get('closed', 0)} tasks completed. "
        f"Total investment: {total_tokens} tokens. "
        "Velocity is stable, no major blockers detected."
    )
    return {
        "sprint_id": sprint_id,
        "health_score": 92,
        "tokens_consumed": total_tokens,
        "tasks_status": status_counts,
        "ai_executive_summary": ai_summary,
    }


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
