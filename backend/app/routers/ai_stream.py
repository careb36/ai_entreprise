"""
AI Streaming Router for Server-Sent Events (SSE).

Implements responsive AI query and workflow execution endpoints satisfying
the Doherty Threshold (<200ms Time-To-First-Byte) via Server-Sent Events.

Adheres strictly to:
- STD-AUD-1: Distinction between claims and observations in streamed event frames.
- STD-SEC-1: No sensitive credential or secret leakage in logs or stream messages.
"""

from __future__ import annotations

import uuid
from typing import Any, Optional
from fastapi import APIRouter, Depends, Query, Request, status
from fastapi.responses import StreamingResponse
from pydantic import BaseModel, Field

from backend.app.services.ai_streaming import AIStreamingEngine, ai_streaming_engine

router = APIRouter(prefix="/api/ai", tags=["AI Streaming"])


class StreamQueryRequest(BaseModel):
    """Request payload for initiating an AI streaming execution."""
    query: str = Field(..., min_length=1, description="AI user query or prompt")
    session_id: Optional[str] = Field(None, description="Client session ID or generated UUID")
    workflow_type: str = Field("orchestration", description="Target LangGraph workflow pipeline")
    context: dict[str, Any] = Field(default_factory=dict, description="Context parameters for the graph")


class StreamQueryStatusResponse(BaseModel):
    """Metadata response regarding streaming capability."""
    endpoint: str
    protocol: str
    doherty_threshold_target_ms: float
    std_aud_1_compliant: bool
    status: str


@router.get("/stream/health", response_model=StreamQueryStatusResponse)
async def get_stream_health() -> StreamQueryStatusResponse:
    """Return health and latency compliance targets for the streaming engine."""
    return StreamQueryStatusResponse(
        endpoint="/api/ai/stream/query",
        protocol="Server-Sent Events (SSE)",
        doherty_threshold_target_ms=200.0,
        std_aud_1_compliant=True,
        status="ready",
    )


@router.post("/stream/query", status_code=status.HTTP_200_OK)
async def stream_ai_query(
    request_data: StreamQueryRequest,
    engine: AIStreamingEngine = Depends(lambda: ai_streaming_engine),
) -> StreamingResponse:
    """
    Stream AI responses using Server-Sent Events (SSE).
    Guarantees first byte emitted in <200ms to honor the Doherty Threshold.
    """
    session_id = request_data.session_id or f"sess_{uuid.uuid4().hex[:12]}"

    return StreamingResponse(
        engine.stream_query_execution(
            query=request_data.query,
            session_id=session_id,
            workflow_type=request_data.workflow_type,
            context=request_data.context,
        ),
        media_type="text/event-stream",
        headers={
            "Cache-Control": "no-cache, no-transform",
            "Connection": "keep-alive",
            "X-Accel-Buffering": "no",
            "Content-Type": "text/event-stream",
        },
    )


@router.get("/stream/workflow", status_code=status.HTTP_200_OK)
async def stream_workflow_get(
    query: str = Query(..., min_length=1, description="AI workflow prompt"),
    session_id: Optional[str] = Query(None, description="Client session identifier"),
    workflow_type: str = Query("orchestration", description="Workflow type"),
    engine: AIStreamingEngine = Depends(lambda: ai_streaming_engine),
) -> StreamingResponse:
    """
    GET endpoint for browser EventSource API compatibility.
    Streams LangGraph verification and synthesis progress.
    """
    active_session_id = session_id or f"sess_{uuid.uuid4().hex[:12]}"

    return StreamingResponse(
        engine.stream_query_execution(
            query=query,
            session_id=active_session_id,
            workflow_type=workflow_type,
            context={},
        ),
        media_type="text/event-stream",
        headers={
            "Cache-Control": "no-cache, no-transform",
            "Connection": "keep-alive",
            "X-Accel-Buffering": "no",
            "Content-Type": "text/event-stream",
        },
    )
