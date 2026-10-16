from datetime import datetime
from typing import Optional

from fastapi import APIRouter, Depends, HTTPException, Query
from fastapi.encoders import jsonable_encoder
from fastapi.responses import JSONResponse
from sqlmodel import Session, select

from database import get_session
from logging_config import logger
from models.agent_run import AgentRun
from schemas import AgentRunCreate
from services.agent_run_service import ArtifactTooLargeError, ingest_agent_run

router = APIRouter(prefix="/v1/agent_runs", tags=["Agent Runs"])


@router.post("")
def create_agent_run(payload: AgentRunCreate, session: Session = Depends(get_session)):
    if "artifact_digest" in (payload.model_extra or {}):
        logger.warning(
            "artifact_digest supplied by caller is ignored; digests are computed server-side (company_id=%s external_ref=%s)",
            payload.company_id,
            payload.external_ref,
        )
    try:
        run, created = ingest_agent_run(session, payload)
    except ArtifactTooLargeError as exc:
        raise HTTPException(status_code=413, detail=str(exc)) from exc

    response = jsonable_encoder(run)
    response["created"] = created
    return JSONResponse(status_code=201 if created else 200, content=response)


@router.get("")
def list_agent_runs(
    company_id: int = Query(...),
    producer: Optional[str] = None,
    status: Optional[str] = None,
    since: Optional[datetime] = None,
    until: Optional[datetime] = None,
    limit: int = Query(50, ge=1, le=500),
    session: Session = Depends(get_session),
):
    statement = select(AgentRun).where(AgentRun.company_id == company_id)
    if producer is not None:
        statement = statement.where(AgentRun.producer == producer)
    if status is not None:
        statement = statement.where(AgentRun.status == status)
    if since is not None:
        statement = statement.where(AgentRun.ingested_at >= since)
    if until is not None:
        statement = statement.where(AgentRun.ingested_at <= until)

    runs = session.exec(
        statement.order_by(AgentRun.ingested_at.desc(), AgentRun.id.desc()).limit(limit)
    ).all()
    return {"agent_runs": runs}


@router.get("/{run_id}")
def get_agent_run(
    run_id: int,
    company_id: int = Query(...),
    session: Session = Depends(get_session),
):
    run = session.exec(
        select(AgentRun).where(
            AgentRun.id == run_id,
            AgentRun.company_id == company_id,
        )
    ).first()
    if run is None:
        raise HTTPException(status_code=404, detail="Agent run not found")
    return run
