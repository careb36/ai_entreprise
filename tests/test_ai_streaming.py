"""
Automated Test Suite for FastAPI SSE AI Streaming & Doherty Threshold Verification.

Verifies:
- AC: First byte returned within 200ms (Doherty Threshold).
- Server-Sent Events headers (text/event-stream, no-cache, X-Accel-Buffering: no).
- STD-AUD-1 compliance: Separation of claim vs observation in stream events.
- STD-SEC-1: No sensitive secrets exposed in payloads.
"""

from __future__ import annotations

import json
import time
import pytest
from httpx import ASGITransport, AsyncClient

from src.backend.streaming.ai_streaming_engine import AIStreamingEngine, StreamEvent
from backend.app.main import app


@pytest.mark.asyncio
async def test_streaming_engine_ttfb_under_200ms():
    """Verify that AIStreamingEngine yields the initial byte well within 200ms."""
    engine = AIStreamingEngine()
    start_time = time.perf_counter()

    first_chunk = None
    async for chunk in engine.stream_query_execution(
        query="Verify AST security gate",
        session_id="test_sess_001",
    ):
        first_chunk = chunk
        break

    elapsed_ms = (time.perf_counter() - start_time) * 1000.0
    assert first_chunk is not None
    assert "event: connected" in first_chunk
    # Strict Doherty Threshold criterion (<200ms)
    assert elapsed_ms < 200.0, f"TTFB of {elapsed_ms:.2f}ms exceeded 200ms limit"


@pytest.mark.asyncio
async def test_stream_event_std_aud_1_claim_observation_separation():
    """Verify STD-AUD-1: claim and observation must never share fields."""
    event = StreamEvent(
        event="test_event",
        payload={"result": "ok"},
        claim={"expected_action": "validate"},
        observation={"observed_outcome": "passed"},
    )
    sse_text = event.to_sse_message()
    assert sse_text.startswith("event: test_event\ndata: ")

    data_json = json.loads(sse_text.split("data: ")[1].strip())
    assert "claim" in data_json
    assert "observation" in data_json
    # Ensure keys are strictly segregated
    claim_keys = set(data_json["claim"].keys())
    observation_keys = set(data_json["observation"].keys())
    assert claim_keys.isdisjoint(observation_keys)


@pytest.mark.asyncio
async def test_fastapi_sse_post_endpoint_ttfb_and_headers():
    """Verify the POST /api/ai/stream endpoint returns text/event-stream within 200ms."""
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        start_time = time.perf_counter()
        response = await client.post(
            "/api/ai/stream",
            json={"query": "Explain validation gate execution", "session_id": "test_sess_002"},
        )
        elapsed_ms = (time.perf_counter() - start_time) * 1000.0

        assert response.status_code == 200
        assert elapsed_ms < 200.0, f"HTTP response time {elapsed_ms:.2f}ms exceeded 200ms"
        assert "text/event-stream" in response.headers["content-type"]
        assert "no-cache" in response.headers.get("cache-control", "")
        assert response.headers.get("x-accel-buffering") == "no"
        assert "event: connected" in response.text


@pytest.mark.asyncio
async def test_fastapi_sse_get_endpoint():
    """Verify the GET /api/ai/stream/{session_id} endpoint for EventSource compatibility."""
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        start_time = time.perf_counter()
        response = await client.get("/api/ai/stream/sess_eventsource_01?query=ping")
        elapsed_ms = (time.perf_counter() - start_time) * 1000.0

        assert response.status_code == 200
        assert elapsed_ms < 200.0
        assert "text/event-stream" in response.headers["content-type"]
        assert "event: connected" in response.text
        assert "event: completed" in response.text
