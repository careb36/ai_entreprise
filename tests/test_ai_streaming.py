"""
Root test runner suite for AI Streaming and Doherty Threshold verification.
Mirror suite for top-level pytest execution.
"""

import json
import time
import pytest
from fastapi import FastAPI
from httpx import ASGITransport, AsyncClient

from backend.app.routers.ai_stream import router as ai_stream_router
from backend.app.services.ai_streaming import AIStreamingEngine, StreamEvent


@pytest.fixture
def app() -> FastAPI:
    fastapi_app = FastAPI(title="AI Streaming Root Test App")
    fastapi_app.include_router(ai_stream_router)
    return fastapi_app


@pytest.mark.asyncio
async def test_root_stream_doherty_threshold():
    """Ensure TTFB < 200ms when streaming from the engine."""
    engine = AIStreamingEngine()
    start_time = time.perf_counter()

    stream_gen = engine.stream_query_execution(
        query="Root level verification test",
        session_id="root_sess_001",
    )
    first_chunk = await stream_gen.__anext__()
    ttfb = (time.perf_counter() - start_time) * 1000.0

    assert ttfb < 200.0
    assert "event: connected" in first_chunk


@pytest.mark.asyncio
async def test_root_sse_endpoint_response(app: FastAPI):
    """Ensure POST endpoint returns compliant SSE stream with Doherty TTFB."""
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://testserver") as client:
        start_time = time.perf_counter()
        resp = await client.post(
            "/api/ai/stream/query",
            json={"query": "Generate pipeline test", "session_id": "root_sess_002"},
        )
        ttfb = (time.perf_counter() - start_time) * 1000.0

        assert resp.status_code == 200
        assert ttfb < 200.0
        assert "event: connected" in resp.text
        assert "event: completed" in resp.text
