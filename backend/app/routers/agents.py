"""
Agents router — LangGraph orchestration, memory, and Glassroom SSE endpoints.

Endpoints:
- ``POST /v1/generate``        — trigger a LangGraph workflow run via NATS.
- ``POST /v1/audit``           — write an agent decision to the audit log.
- ``GET  /v1/memory/search``   — vector-similarity search over pgvector + Neo4j.
- ``POST /v1/memory/store``    — persist content and its embedding.
- ``GET  /v1/glassroom/stream``— SSE stream of real-time agent events for a sprint.

The ``sse_connections`` dict is a module-level singleton shared with the NATS
listener in ``main.py``.  When a NATS message arrives on ``glassroom.<sprint_id>``,
the listener pushes the payload into the corresponding ``asyncio.Queue`` which
this router's SSE generator reads and forwards to the connected browser.
"""

import asyncio
import json
import os
from collections import defaultdict
from datetime import datetime
from pathlib import Path
from typing import AsyncIterator, Awaitable, Callable, Dict, List, Optional
from uuid import uuid4

from fastapi import APIRouter, Depends, HTTPException, Query, Request
from fastapi.responses import StreamingResponse
from sqlmodel import Session, select

from database import get_session, engine
from audit_signing import verify_audit_row
from models import AuditLog
from schemas import GenerateRequest, AuditRequest
from services.agent_service import AgentService
from logging_config import logger

router = APIRouter(prefix="/v1", tags=["Agents"])

# Shared SSE connection map — keys are sprint IDs, values are active subscriber queues.
# Also consumed by the NATS listener in main.py to forward Glassroom events.
sse_connections: Dict[str, List[asyncio.Queue[str]]] = {}
recent_sse_events: Dict[str, List[dict]] = defaultdict(list)
MAX_RECENT_SSE_EVENTS = 50

HistoryLoader = Callable[[], Awaitable[list[dict]]]


def register_sse_connection(sprint_id: str, queue: asyncio.Queue[str]) -> None:
    subscribers = sse_connections.setdefault(sprint_id, [])
    subscribers.append(queue)


def unregister_sse_connection(sprint_id: str, queue: asyncio.Queue[str]) -> None:
    subscribers = sse_connections.get(sprint_id)
    if not subscribers:
        return
    sse_connections[sprint_id] = [subscriber for subscriber in subscribers if subscriber is not queue]
    if not sse_connections[sprint_id]:
        sse_connections.pop(sprint_id, None)


async def publish_sse_event(sprint_id: str, payload: str) -> None:
    try:
        parsed = json.loads(payload)
    except (json.JSONDecodeError, TypeError):
        parsed = None

    if isinstance(parsed, dict):
        recent_sse_events[sprint_id].append(parsed)
        if len(recent_sse_events[sprint_id]) > MAX_RECENT_SSE_EVENTS:
            recent_sse_events[sprint_id] = recent_sse_events[sprint_id][-MAX_RECENT_SSE_EVENTS:]

    for subscriber in list(sse_connections.get(sprint_id, [])):
        await subscriber.put(payload)


def _merge_sse_history(db_history: list[dict], recent_history: list[dict]) -> list[dict]:
    merged: list[dict] = []
    seen: set[tuple[str, str, str, str, str, str]] = set()
    combined = [*db_history, *recent_history]

    latest_queue_index: dict[str, int] = {}
    has_follow_up_for_task: set[str] = set()

    for index, event in enumerate(combined):
        if not isinstance(event, dict):
            continue
        task_key = str(event.get("task_id") or event.get("task") or "")
        semantic_type = str(event.get("semantic_type") or "")
        if not task_key:
            continue
        if semantic_type == "task.queued":
            latest_queue_index[task_key] = index
            continue
        if semantic_type in {
            "task.claimed",
            "task.progress",
            "task.review_started",
            "task.review_handoff",
            "task.agent_complete",
            "task.approved",
            "task.blocked",
            "task.recovered",
            "task.history_comment",
            "task.status_snapshot",
            "task.retry_queued",
            "task.rework_scheduled",
        }:
            has_follow_up_for_task.add(task_key)

    for index, event in enumerate(combined):
        if not isinstance(event, dict):
            continue
        task_key = str(event.get("task_id") or event.get("task") or "")
        semantic_type = str(event.get("semantic_type") or "")

        if semantic_type == "task.queued" and task_key:
            if latest_queue_index.get(task_key) != index:
                continue
            if task_key in has_follow_up_for_task:
                continue

        key = (
            str(event.get("type", "")),
            str(event.get("agent", "")),
            str(event.get("role", "")),
            str(event.get("task", "")),
            str(event.get("message", "")),
            str(event.get("timestamp", "")),
        )
        if key in seen:
            continue
        seen.add(key)
        merged.append(event)

    return merged


async def generate_glassroom_sse(
    queue: asyncio.Queue[str],
    sprint_id: str,
    nats_available: bool,
    history_loader: HistoryLoader,
) -> AsyncIterator[str]:
    try:
        yield f"data: {json.dumps({'type': 'connected', 'semantic_type': 'stream.connected', 'source': 'sse', 'semantic_version': 1, 'event_id': str(uuid4()), 'sprint_id': sprint_id})}\n\n"

        if not nats_available:
            yield f"data: {json.dumps({'type': 'error', 'semantic_type': 'stream.unavailable', 'source': 'sse', 'semantic_version': 1, 'event_id': str(uuid4()), 'agent': 'System', 'role': 'System', 'message': '⚠️ NATS message broker is not connected. Agents cannot run. Start NATS and restart the backend.', 'timestamp': datetime.utcnow().isoformat()})}\n\n"

        history = _merge_sse_history(await history_loader(), recent_sse_events.get(sprint_id, []))
        for evt in history:
            yield f"data: {json.dumps(evt)}\n\n"

        while True:
            try:
                data = await asyncio.wait_for(queue.get(), timeout=15)
                try:
                    parsed = json.loads(data)
                except (json.JSONDecodeError, TypeError):
                    parsed = {"type": "message", "message": str(data)}
                yield f"data: {json.dumps(parsed)}\n\n"
            except asyncio.TimeoutError:
                yield f"data: {json.dumps({'ping': True})}\n\n"
    finally:
        unregister_sse_connection(sprint_id, queue)


@router.post("/generate")
async def generate(req: GenerateRequest, request: Request, session: Session = Depends(get_session)):
    """
    Trigger a LangGraph agent workflow run.
    """
    service = AgentService(nats_client=request.app.state.nats_client)
    interaction_id = await service.generate_package(
        company_id=req.company_id,
        business_unit_id=req.business_unit_id,
        story=req.story,
        constraints=req.constraints,
        interaction_id=req.interaction_id,
        session=session
    )
    return {"interaction_id": interaction_id, "package_url": None}


@router.post("/audit")
async def audit(req: AuditRequest, session: Session = Depends(get_session)):
    """
    Write an agent decision entry to the audit log.

    Writes to both the filesystem JSONL file and the ``audit_log`` database
    table via ``AgentService.audit_interaction()``.
    """
    if req.decision_signature is not None:
        logger.warning(
            "decision_signature supplied by caller is ignored; signatures are computed server-side"
        )

    service = AgentService()
    service.audit_interaction(
        session=session,
        interaction_id=req.interaction_id,
        agent_id=req.agent_id,
        action=req.action,
        details=req.details,
        result=req.result,
        decision_signature=None,
        cost_tokens=req.cost_tokens
    )
    return {"status": "ok"}


@router.get("/audit/verify")
async def verify_audit(interaction_id: str = Query(...), session: Session = Depends(get_session)):
    """Verify every audit row associated with an interaction."""
    rows = session.exec(
        select(AuditLog)
        .where(AuditLog.interaction_id == interaction_id)
        .order_by(AuditLog.id)
    ).all()
    if not rows:
        raise HTTPException(status_code=404, detail="No audit rows found for interaction_id.")

    summary = {"valid": 0, "invalid": 0, "unsigned": 0, "legacy": 0}
    verified_rows = []
    for row in rows:
        verification = verify_audit_row(row)
        summary[verification["status"]] += 1
        verified_rows.append(
            {
                "id": row.id,
                "action": row.action,
                "status": verification["status"],
                "mode": verification["mode"],
            }
        )

    return {
        "interaction_id": interaction_id,
        "rows": verified_rows,
        "summary": summary,
    }


@router.get("/memory/search")
async def search_memory(request: Request, query: str = Query(...), limit: int = 10):
    """
    Search the vector memory store for content similar to *query*.

    Uses pgvector cosine similarity on pre-computed embeddings.
    Returns up to *limit* results sorted by descending similarity.
    """
    client = request.app.state.memory_client
    if not client:
        raise HTTPException(status_code=503, detail="Memory service unavailable (Neo4j not connected).")
    results = client.search_similar(query, limit)
    return {"results": results}


@router.post("/memory/store")
async def store_memory(request: Request, content: str, metadata: Optional[dict] = None):
    """
    Compute and store an embedding for *content* in pgvector and Neo4j.

    Embeddings are generated by the LLM Proxy (``text-embedding-004``).
    *metadata* is an arbitrary JSON dict stored alongside the embedding for
    later retrieval context.
    """
    client = request.app.state.memory_client
    if not client:
        raise HTTPException(status_code=503, detail="Memory service unavailable (Neo4j not connected).")
    client.store(content, metadata)
    return {"status": "stored"}


@router.get("/glassroom/stream")
async def glassroom_stream(sprint_id: str = Query(...), request: Request = None):
    """
    Server-Sent Events (SSE) stream for real-time Glassroom monitoring.

    On connect, replays past agent activity from backlog item comments so the
    user sees the full conversation history.  Then switches to live streaming
    via NATS.  A keepalive ping is sent every 15 seconds.

    If NATS is unavailable, a red system error event is emitted immediately so
    the user sees a clear explanation instead of silent inactivity.
    """
    queue: asyncio.Queue[str] = asyncio.Queue()
    register_sse_connection(sprint_id, queue)

    def _load_history() -> list[dict]:
        """Load past agent comments from the DB as GlassRoom messages."""
        from sqlmodel import Session, select, text
        from models import BacklogItem
        events = []
        try:
            with Session(engine) as session:
                items = session.exec(
                    select(BacklogItem)
                    .where(BacklogItem.sprint_id == int(sprint_id))
                    .order_by(BacklogItem.id)
                ).all()
                for item in items:
                    comments = item.comments or []
                    if isinstance(comments, str):
                        try:
                            comments = json.loads(comments)
                        except (json.JSONDecodeError, TypeError):
                            comments = []
                    for c in comments:
                        text = c.get("text") or ""
                        if text.startswith("External assistant context used for this task:"):
                            events.append({
                                "type": "status",
                                "semantic_type": "assistant.history_context",
                                "source": "history",
                                "semantic_version": 1,
                                "event_id": str(uuid4()),
                                "agent": "System",
                                "role": "System",
                                "task_id": item.id,
                                "task": item.title,
                                "message": "External assistant context was used for this task.",
                                "preview": text[:300],
                                "timestamp": c.get("created_at", ""),
                                "status": item.status,
                            })
                            continue
                        events.append({
                            "type": "agent_complete",
                            "semantic_type": "task.history_comment",
                            "source": "history",
                            "semantic_version": 1,
                            "event_id": str(uuid4()),
                            "agent": c.get("author", item.assignee or "Agent"),
                            "role": item.assignee or "Agent",
                            "task_id": item.id,
                            "task_status": item.status,
                            "task": item.title,
                            "message": f"✅ {c.get('author', 'Agent')} delivered: {item.title}",
                            "preview": text[:300],
                            "timestamp": c.get("created_at", ""),
                            "status": item.status,
                        })
                    # Show current status for items without comments too
                    if not comments and item.status not in ("todo",):
                        events.append({
                            "type": "status",
                            "semantic_type": "task.status_snapshot",
                            "source": "history",
                            "semantic_version": 1,
                            "event_id": str(uuid4()),
                            "agent": item.assignee or "System",
                            "role": item.assignee or "System",
                            "task_id": item.id,
                            "task_status": item.status,
                            "task": item.title,
                            "message": f"📋 {item.title} → {item.status}",
                            "timestamp": (item.updated_at or item.created_at or "").isoformat() if hasattr(item.updated_at or item.created_at, "isoformat") else "",
                            "status": item.status,
                        })
        except Exception:
            pass
        return events

    # Detect whether NATS (and therefore agents) is available.
    nats_available = bool(request and getattr(request.app.state, "nats_client", None))

    return StreamingResponse(
        generate_glassroom_sse(
            queue=queue,
            sprint_id=sprint_id,
            nats_available=nats_available,
            history_loader=lambda: asyncio.to_thread(_load_history),
        ),
        media_type="text/event-stream",
        headers={
            "Cache-Control": "no-cache",
            "X-Accel-Buffering": "no",
        },
    )
