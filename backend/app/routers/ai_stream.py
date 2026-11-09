"""
FastAPI Server-Sent Events (SSE) Router for AI Endpoints.

Designed specifically to comply with the Doherty Threshold (<400ms UX feedback)
by delivering the initial Server-Sent Event byte within 200ms.

Endpoints:
- POST /v1/ai/query/stream     - Stream AI verification & orchestration responses via SSE.
- GET  /v1/ai/query/stream     - Browser EventSource friendly streaming endpoint.
- POST /v1/generate/stream     - LangGraph generation pipeline streaming endpoint.
"""

from __future__ import annotations

import uuid
from typing import Any, Optional
from fastapi import APIRouter, Depends, Query, Request, status
from fastapi.responses import StreamingResponse
from pydantic import BaseModel, Field

from src.backend.streaming.ai_streaming_engine import AIStreamingEngine

router = APIRouter(prefix="/v1", tags=["AI Streaming"])

streaming_engine = AIStreamingEngine()


class AIStreamQueryRequest(BaseModel):
    """Request payload for streaming AI queries."""
    query: str = Field(..., min_length=1, max_length=10000, description="The prompt or query for AI execution")
    session_id: Optional[str] = Field(None, description="Optional session or sprint correlation ID")
    workflow_type: str = Field("verification_loop", description="LangGraph workflow to run")
    context: dict[str, Any] = Field(default_factory=dict, description="Context variables for the graph")


class GenerateStreamRequest(BaseModel):
    """Request payload for streaming agent artifact generation."""
    prompt: str = Field(..., min_length=1)
    sprint_id: Optional[str] = None
    role: str = "Developer"
    context: dict[str, Any] = Field(default_factory=dict)


def _sse_response_headers() -> dict[str, str]:
    """Headers required for true sub-200ms SSE delivery without proxy buffering."""
    return {
        "Content-Type": "text/event-stream; charset=utf-8",
        "Cache-Control": "no-cache, no-transform",
        "Connection": "keep-alive",
        "X-Accel-Buffering": "no",  # Prevents Nginx/reverse-proxies from buffering chunks
        "Access-Control-Allow-Origin": "*",
    }


@router.post(
    "/ai/query/stream",
    summary="Stream AI Query with Sub-200ms TTFB",
    response_class=StreamingResponse,
    status_code=status.HTTP_200_OK,
    responses={
        200: {
            "description": "Continuous Server-Sent Event stream emitting tokens and LangGraph transitions.",
            "content": {"text/event-stream": {}},
        }
    },
)
async def stream_ai_query_post(request_payload: AIStreamQueryRequest, request: Request) -> StreamingResponse:
    """
    Executes an AI query and streams tokens/steps via SSE.
    Guarantees first byte return <200ms to fulfill the Doherty Threshold.
    """
    session_id = request_payload.session_id or f"sess_{uuid.uuid4().hex[:12]}"
    stream_generator = streaming_engine.stream_query_execution(
        query=request_payload.query,
        session_id=session_id,
        workflow_type=request_payload.workflow_type,
        context=request_payload.context,
    )

    return StreamingResponse(
        stream_generator,
        media_type="text/event-stream",
        headers=_sse_response_headers(),
    )


@router.get(
    "/ai/query/stream",
    summary="EventSource GET AI Query Stream",
    response_class=StreamingResponse,
    status_code=status.HTTP_200_OK,
)
async def stream_ai_query_get(
    query: str = Query(..., min_length=1, description="AI Query string"),
    session_id: Optional[str] = Query(None, description="Optional correlation session id"),
    workflow_type: str = Query("verification_loop", description="Target workflow"),
) -> StreamingResponse:
    """
    Browser native EventSource GET endpoint for immediate SSE streaming.
    """
    sid = session_id or f"sess_{uuid.uuid4().hex[:12]}"
    stream_generator = streaming_engine.stream_query_execution(
        query=query,
        session_id=sid,
        workflow_type=workflow_type,
    )

    return StreamingResponse(
        stream_generator,
        media_type="text/event-stream",
        headers=_sse_response_headers(),
    )


@router.post(
    "/generate/stream",
    summary="Stream LangGraph Artifact Generation",
    response_class=StreamingResponse,
    status_code=status.HTTP_200_OK,
)
async def stream_generate_post(request_payload: GenerateStreamRequest) -> StreamingResponse:
    """
    Streams LangGraph agent generation events with low latency feedback.
    """
    session_id = request_payload.sprint_id or f"sprint_{uuid.uuid4().hex[:8]}"
    stream_generator = streaming_engine.stream_query_execution(
        query=request_payload.prompt,
        session_id=session_id,
        workflow_type=f"agent_gen_{request_payload.role.lower()}",
        context=request_payload.context,
    )

    return StreamingResponse(
        stream_generator,
        media_type="text/event-stream",
        headers=_sse_response_headers(),
    )
