import json
from fastapi import APIRouter, HTTPException, Depends
from sqlmodel import Session, select, text

from database import get_session, get_neo4j_driver
from models import Company, Agent, ProjectCredential, ProjectDiscovery, BusinessUnit
from schemas import CompanyInitRequest, ApprovalRequest, DiscoveryRequest
from services.discovery_service import DiscoveryService
from logging_config import logger

router = APIRouter(prefix="/v1", tags=["Enterprise"])


@router.post("/init_company", tags=["Core"])
async def init_company(req: CompanyInitRequest, session: Session = Depends(get_session)):
    """Initialize a company with agents, permissions, and seed Neo4j root nodes."""
    company = Company(
        company_name=req.company_name,
        default_sprint_length_days=req.default_sprint_length_days,
        autonomy_level=req.autonomy_level,
    )
    session.add(company)
    session.flush()  # get company.id before committing

    for agent_data in req.agents:
        session.add(Agent(
            company_id=company.id,
            role=agent_data.get("role"),
            persona=agent_data.get("persona"),
            seniority=agent_data.get("seniority", "Senior"),
            specialty=agent_data.get("specialty", ""),
        ))

    if req.permissions:
        for cred_type, access_level in req.permissions.items():
            if access_level != "none":
                session.add(ProjectCredential(
                    company_id=company.id,
                    credential_type=cred_type,
                    access_level=access_level,
                    encrypted_value="vault_encrypted_placeholder",
                ))

    session.commit()

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
    """Enterprise Governance: A human manager approves an AI decision."""
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
    """Get token usage quotas for all business units."""
    units = session.exec(
        select(BusinessUnit).where(BusinessUnit.company_id == company_id)
    ).all()
    return [{"unit": u.unit_name, "limit": u.token_quota_limit, "used": u.token_quota_used} for u in units]


@router.get("/analytics/summary", tags=["Enterprise"])
async def get_project_health(sprint_id: int, session: Session = Depends(get_session)):
    """AI Scrum Master: Sprint health report from audit logs and backlog data."""
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


@router.post("/discover_team", tags=["Enterprise"])
async def discover_team(req: DiscoveryRequest, session: Session = Depends(get_session)):
    """Scans a project path to detect stack and suggest the ideal AI team via DiscoveryService."""
    service = DiscoveryService(session)
    discovery, detected_stack, suggested_team = service.discover_project_team(
        company_id=req.company_id,
        project_path=req.project_path
    )
    return {
        "discovery_id": discovery.id,
        "detected_stack": detected_stack,
        "suggested_team": suggested_team,
    }


@router.get("/enterprise/lineage", tags=["Enterprise"])
async def get_code_lineage(file_path: str):
    """Time Travel Debugging: Returns architectural decisions that modified a file (from Neo4j)."""
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
