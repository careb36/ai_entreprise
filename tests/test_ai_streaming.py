"""
Test suite for FastAPI SSE streaming endpoints and LangGraph streaming engine.
Verifies Doherty Threshold compliance (TTFB < 200ms) and STD-AUD-1 evidence capture.
"""

import asyncio
import json
import time
import pytest
from httpx import AsyncClient, ASGITransport

from backend.app.main import app
from src.backend.streaming.ai_streaming_engine import AIStreamingEngine, StreamEvent


@pytest.mark.asyncio
async def test_streaming_engine_ttfb_under_200ms():
    """Verify that the streaming engine delivers the first chunk within 200ms."""
    engine = AIStreamingEngine()
    start_time = time.perf_counter()

    stream = engine.stream_query_execution(
        query="Verify AST security policies",
        session_id="test-session-123",
    )

    # Receive the first event
    first_chunk = await stream.__anext__()
    elapsed_ms = (time.perf_counter() - start_time) * 1000.0

    # Doherty threshold criteria: TTFB must be < 200ms
    assert elapsed_ms < 200.0, f"TTFB was {elapsed_ms}ms, exceeding 200ms limit"
    assert "event: connected" in first_chunk
    assert "test-session-123" in first_chunk


@pytest.mark.asyncio
async def test_stream_event_std_aud_1_separation():
    """Verify that claims and observations do not share a field per STD-AUD-1."""
    event = StreamEvent(
        event="evidence_captured",
        payload={"result": "valid"},
        claim={"claimed_status": "success"},
        observation={"observed_hash": "sha256:abc1234"},
    )
    sse_text = event.to_sse_message()
    assert sse_text.startswith("event: evidence_captured\n")

    lines = sse_text.strip().split("\n")
    data_line = [l for l in lines if l.startswith("data: ")][0]
    payload = json.loads(data_line[6:])

    assert "claim" in payload
    assert "observation" in payload
    assert payload["claim"]["claimed_status"] == "success"
    assert payload["observation"]["observed_hash"] == "sha256:abc1234"
    # Ensure they are distinct dictionaries
    assert set(payload["claim"].keys()).isdisjoint(set(payload["observation"].keys()))


@pytest.mark.asyncio
async def test_fastapi_post_ai_query_stream_endpoint():
    """Verify POST /v1/ai/query/stream returns SSE with proper headers and rapid TTFB."""
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        start_time = time.perf_counter()
        response = await client.post(
            "/v1/ai/query/stream",
            json={
                "query": "Generate validation gate node",
                "session_id": "sess-post-test",
                "workflow_type": "verification_loop",
            },
        )
        elapsed_ms = (time.perf_counter() - start_time) * 1000.0

        assert response.status_code == 200
        assert "text/event-stream" in response.headers["content-type"]
        assert response.headers.get("x-accel-buffering") == "no"
        assert response.headers.get("cache-control") == "no-cache, no-transform"

        body = response.text
        assert "event: connected" in body
        assert "event: workflow_started" in body
        assert "event: token" in body
        assert "event: evidence_captured" in body
        assert "event: done" in body
        assert elapsed_ms < 500.0  # Entire mock execution completes swiftly


@pytest.mark.asyncio
async def test_fastapi_get_ai_query_stream_eventsource():
    """Verify GET /v1/ai/query/stream EventSource browser compatibility."""
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        response = await client.get(
            "/v1/ai/query/stream?query=CheckSecurityAudit&session_id=sess-get-test"
        )
        assert response.status_code == 200
        assert "text/event-stream" in response.headers["content-type"]
        assert "sess-get-test" in response.text
        assert "event: connected" in response.text


@pytest.mark.asyncio
async def test_fastapi_generate_stream_endpoint():
    """Verify POST /v1/generate/stream pipeline stream endpoint."""
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        response = await client.post(
            "/v1/generate/stream",
            json={
                "prompt": "Implement AST node verification",
                "sprint_id": "sprint-42",
                "role": "Backend",
            },
        )
        assert response.status_code == 200
        assert "text/event-stream" in response.headers["content-type"]
        assert "event: connected" in response.text
        assert "sprint-42" in response.text
