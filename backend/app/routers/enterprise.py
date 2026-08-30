"""
Enterprise router — multi-tenancy governance, quota management, and analytics.

Endpoints:
- ``POST /v1/init_company``                         — bootstrap a new company with agents and credentials.
- ``POST /v1/approve_task``                         — human-in-the-loop approval for AI-generated tasks.
- ``GET  /v1/enterprise/quotas``                    — token usage per business unit.
- ``GET  /v1/analytics/summary``                    — AI Scrum Master sprint health report.
- ``GET  /v1/analytics/orchestration_health``       — 3-KPI weekly trend vs Batch-1 anchor.
- ``POST /v1/discover_team``                        — auto-detect project stack and suggest an AI team.
- ``GET  /v1/enterprise/lineage``                   — time-travel debugging: architectural decisions for a file.
"""

import asyncio
from datetime import date, datetime, timedelta
from typing import List

from fastapi import APIRouter, HTTPException, Depends, Request, Query
from sqlalchemy import text
from sqlmodel import Session

from database import get_session, get_neo4j_driver
from services.discovery_service import DiscoveryService
from services.company_service import CompanyService
from schemas import (
    CompanyInitRequest,
    ApprovalRequest,
    DiscoveryRequest,
    OrchestrationHealthResponse,
    OrchestrationHealthWeek,
    OrchestrationHealthSummary,
    OrchestrationHealthSummaries,
)
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


@router.post("/enterprise/quotas/{bu_id}/reset", tags=["Enterprise"])
async def reset_quota(bu_id: int, session: Session = Depends(get_session)):
    """
    Reset the token usage counter for a business unit to zero.
    Intended for use at the start of a new billing cycle.
    """
    service = CompanyService(session)
    try:
        return service.reset_quota(bu_id)
    except ValueError as exc:
        raise HTTPException(status_code=404, detail=str(exc))


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


# ---------------------------------------------------------------------------
# Orchestration health — 3 KPI weekly trend (Batch-1 impact measurement)
# ---------------------------------------------------------------------------

_ANCHOR_COMMIT = "116138e"
_ANCHOR_DATE = date(2026, 5, 2)


@router.get(
    "/analytics/orchestration_health",
    response_model=OrchestrationHealthResponse,
    tags=["Enterprise"],
)
async def get_orchestration_health(
    company_id: int = Query(..., description="Company whose sprint history to analyse"),
    weeks: int = Query(8, ge=1, le=52, description="Number of trailing weeks to include"),
    session: Session = Depends(get_session),
):
    """
    Return three weekly KPIs that measure the impact of the Batch-1
    orchestration improvements (anchor commit ``116138e``, 2026-05-02):

    1. **QA NEEDS_WORK rate** — fraction of QA decisions requesting rework.
    2. **Average Developer tokens** — mean LLM tokens consumed per Developer turn.
    3. **ARCHITECT_CONSULTED frequency** — architect consultation comments per week.

    Each week is tagged ``post_batch1=true`` when it falls on or after
    ``2026-05-02``.  A ``summary`` block aggregates pre/post averages for
    quick comparison.

    Returns ``weeks: []`` gracefully when no data exists (new install).
    ``company_id`` is enforced on every query — no cross-tenant data leakage.
    """
    engine = session.get_bind()

    # Compute the cutoff timestamp once in Python to avoid PostgreSQL interval
    # arithmetic with bound parameters (which is not supported by all drivers).
    cutoff = datetime.utcnow() - timedelta(weeks=weeks)

    # ------------------------------------------------------------------
    # KPI 1 — QA NEEDS_WORK rate  (audit_log, scoped via details JSON)
    # NOTE: company_id scoping relies on the 'company_id' key being present in
    # the audit_log.details JSON blob.  Rows without that key are excluded.
    # ------------------------------------------------------------------
    qa_sql = text("""
        SELECT
            to_char(date_trunc('week', a.created_at), 'IYYY-"W"IW') AS week,
            COALESCE(
                COUNT(*) FILTER (WHERE a.details->>'result_type' = 'NEEDS_WORK') * 1.0
                / NULLIF(COUNT(*), 0),
                0
            )                                                         AS qa_needs_work_rate,
            COUNT(*)                                                   AS task_count
        FROM audit_log a
        WHERE (a.details->>'role') = 'QA'
          AND (a.details->>'company_id')::int = :company_id
          AND a.created_at >= :cutoff
        GROUP BY 1
        ORDER BY 1
    """)

    # ------------------------------------------------------------------
    # KPI 1 fallback — scan backlog_items.comments for 'NEEDS WORK:' prefix
    # (used when audit_log role/company_id is not populated)
    # ------------------------------------------------------------------
    qa_fallback_sql = text("""
        SELECT
            to_char(date_trunc('week', bi.updated_at), 'IYYY-"W"IW') AS week,
            COALESCE(
                COUNT(*) FILTER (
                    WHERE c->>'text' ILIKE 'NEEDS WORK:%'
                ) * 1.0 / NULLIF(COUNT(*), 0),
                0
            )                                                         AS qa_needs_work_rate,
            COUNT(*)                                                   AS task_count
        FROM backlog_items bi
        JOIN sprints s ON s.id = bi.sprint_id
        CROSS JOIN LATERAL jsonb_array_elements(
            CASE
                WHEN bi.comments IS NULL THEN '[]'::jsonb
                WHEN jsonb_typeof(bi.comments::jsonb) = 'array' THEN bi.comments::jsonb
                ELSE '[]'::jsonb
            END
        ) AS c
        WHERE s.company_id = :company_id
          AND bi.updated_at >= :cutoff
        GROUP BY 1
        ORDER BY 1
    """)

    # ------------------------------------------------------------------
    # KPI 2 — Average Developer tokens (audit_log, scoped via details JSON)
    # ------------------------------------------------------------------
    tokens_sql = text("""
        SELECT
            to_char(date_trunc('week', a.created_at), 'IYYY-"W"IW') AS week,
            COALESCE(AVG(a.cost_tokens), 0)                           AS avg_developer_tokens
        FROM audit_log a
        WHERE (a.details->>'role') = 'Developer'
          AND (a.details->>'company_id')::int = :company_id
          AND a.created_at >= :cutoff
        GROUP BY 1
        ORDER BY 1
    """)

    # ------------------------------------------------------------------
    # KPI 3 — ARCHITECT_CONSULTED frequency (backlog_items.comments)
    # ------------------------------------------------------------------
    consult_sql = text("""
        SELECT
            to_char(date_trunc('week', bi.updated_at), 'IYYY-"W"IW') AS week,
            COUNT(*)                                                   AS architect_consult_count
        FROM backlog_items bi
        JOIN sprints s ON s.id = bi.sprint_id
        CROSS JOIN LATERAL jsonb_array_elements(
            CASE
                WHEN bi.comments IS NULL THEN '[]'::jsonb
                WHEN jsonb_typeof(bi.comments::jsonb) = 'array' THEN bi.comments::jsonb
                ELSE '[]'::jsonb
            END
        ) AS c
        WHERE c->>'author' = 'Architect'
          AND (c->>'text') ILIKE 'ARCHITECT_CONSULTED:%'
          AND s.company_id = :company_id
          AND bi.updated_at >= :cutoff
        GROUP BY 1
        ORDER BY 1
    """)

    params = {"company_id": company_id, "cutoff": cutoff}

    try:
        with engine.connect() as conn:
            qa_rows = conn.execute(qa_sql, params).mappings().all()

            # Fall back to comments scan when audit_log has no matching rows
            if not qa_rows:
                qa_rows = conn.execute(qa_fallback_sql, params).mappings().all()

            tokens_rows = conn.execute(tokens_sql, params).mappings().all()
            consult_rows = conn.execute(consult_sql, params).mappings().all()
    except Exception as exc:
        logger.error("orchestration_health query failed: %s", exc)
        raise HTTPException(status_code=500, detail="Analytics query failed.")

    # ------------------------------------------------------------------
    # Merge the three result sets by ISO week key
    # ------------------------------------------------------------------
    qa_by_week: dict = {r["week"]: r for r in qa_rows}
    tokens_by_week: dict = {r["week"]: r for r in tokens_rows}
    consult_by_week: dict = {r["week"]: r for r in consult_rows}

    all_weeks: List[str] = sorted(
        set(qa_by_week) | set(tokens_by_week) | set(consult_by_week)
    )

    week_items: List[OrchestrationHealthWeek] = []
    # ISO week labels are sortable as strings; compute the anchor once outside
    # the loop to avoid redundant computation on every iteration.
    anchor_week_label = _ANCHOR_DATE.strftime("%G-W%V")
    for week_label in all_weeks:
        qa = qa_by_week.get(week_label, {})
        tok = tokens_by_week.get(week_label, {})
        con = consult_by_week.get(week_label, {})

        # Determine whether the week falls on or after the anchor date.
        post = week_label >= anchor_week_label

        week_items.append(
            OrchestrationHealthWeek(
                week=week_label,
                qa_needs_work_rate=float(qa.get("qa_needs_work_rate", 0.0)),
                avg_developer_tokens=float(tok.get("avg_developer_tokens", 0.0)),
                architect_consult_count=int(con.get("architect_consult_count", 0)),
                task_count=int(qa.get("task_count", 0)),
                post_batch1=post,
            )
        )

    # ------------------------------------------------------------------
    # Pre / post summary averages
    # ------------------------------------------------------------------
    def _avg(items: List[OrchestrationHealthWeek], field: str) -> float:
        values = [getattr(w, field) for w in items]
        return sum(values) / len(values) if values else 0.0

    pre_weeks = [w for w in week_items if not w.post_batch1]
    post_weeks = [w for w in week_items if w.post_batch1]

    summary = OrchestrationHealthSummaries(
        pre_batch1=OrchestrationHealthSummary(
            qa_needs_work_rate=_avg(pre_weeks, "qa_needs_work_rate"),
            avg_developer_tokens=_avg(pre_weeks, "avg_developer_tokens"),
        ),
        post_batch1=OrchestrationHealthSummary(
            qa_needs_work_rate=_avg(post_weeks, "qa_needs_work_rate"),
            avg_developer_tokens=_avg(post_weeks, "avg_developer_tokens"),
        ),
    )

    return OrchestrationHealthResponse(
        anchor_commit=_ANCHOR_COMMIT,
        anchor_date=_ANCHOR_DATE.isoformat(),
        weeks=week_items,
        summary=summary,
    )
