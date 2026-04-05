import asyncio
import json
import os
from datetime import datetime
from pathlib import Path
from typing import Dict
from uuid import uuid4

from fastapi import APIRouter, Depends, HTTPException, Query
from fastapi.responses import StreamingResponse
from sqlmodel import Session

from database import get_session
from schemas import GenerateRequest, AuditRequest
from services.agent_service import AgentService
from logging_config import logger

router = APIRouter(prefix="/v1", tags=["Agents"])

# Shared SSE connection map — also used by the NATS listener in main.py
sse_connections: Dict[str, asyncio.Queue] = {}


@router.post("/generate")
async def generate(req: GenerateRequest):
    """Generate a package using LangGraph orchestration (publishes to NATS)."""
    from main import nats_client
    service = AgentService(nats_client=nats_client)
    interaction_id = await service.generate_package(
        company_id=req.company_id,
        story=req.story,
        constraints=req.constraints,
        interaction_id=req.interaction_id
    )
    return {"interaction_id": interaction_id, "package_url": None}


@router.post("/audit")
async def audit(req: AuditRequest, session: Session = Depends(get_session)):
    """Write audit entry to both file and database via AgentService."""
    service = AgentService()
    service.audit_interaction(
        session=session,
        interaction_id=req.interaction_id,
        agent_id=req.agent_id,
        action=req.action,
        details=req.details,
        result=req.result,
        decision_signature=req.decision_signature,
        cost_tokens=req.cost_tokens
    )
    return {"status": "ok"}


@router.get("/memory/search")
async def search_memory(query: str = Query(...), limit: int = 10):
    """Search vector memory."""
    from main import memory_client
    results = memory_client.search_similar(query, limit)
    return {"results": results}


@router.post("/memory/store")
async def store_memory(content: str, metadata: dict = None):
    """Store content in vector memory."""
    from main import memory_client
    memory_client.store(content, metadata)
    return {"status": "stored"}


@router.get("/glassroom/stream")
async def glassroom_stream(sprint_id: str = Query(...)):
    """SSE stream for real-time Glassroom events."""
    queue: asyncio.Queue = asyncio.Queue()
    sse_connections[sprint_id] = queue

    async def event_generator():
        try:
            while True:
                try:
                    data = await asyncio.wait_for(queue.get(), timeout=30)
                    yield f"data: {json.dumps(json.loads(data))}\n\n"
                except asyncio.TimeoutError:
                    yield f"data: {json.dumps({'ping': True})}\n\n"
        finally:
            sse_connections.pop(sprint_id, None)

    return StreamingResponse(event_generator(), media_type="text/event-stream")
