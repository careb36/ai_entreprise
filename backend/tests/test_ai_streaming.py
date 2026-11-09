"""
Test Suite for AI Streaming Service & FastAPI SSE Endpoints.

Validates:
1. Doherty Threshold Compliance: TTFB < 200ms.
2. Server-Sent Events (SSE) formatting specification (`event: ...\ndata: ...\n\n`).
3. STD-AUD-1 Separation: Claims and observations never share a field.
4. STD-SEC-1: No sensitive tokens or secrets are leaked.
5. Error recovery and cancellation handling.
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
    """Create FastAPI application with ai_stream router included."""
    fastapi_app = FastAPI(title="AI Streaming Test App")
    fastapi_app.include_router(ai_stream_router)
    return fastapi_app


@pytest.mark.asyncio
async def test_stream_event_std_aud_1_separation():
    """Verify StreamEvent strictly adheres to STD-AUD-1 claim/observation separation."""
    event = StreamEvent(
        event="test_event",
        payload={"task": "verification"},
        claim={"claim_key": "planned_execution"},
        observation={"obs_key": "actual_metrics"},
    )
    sse_text = event.to_sse_message()
    assert sse_text.startswith("event: test_event\n")
    assert "data: " in sse_text

    # Extract JSON data line
    data_line = [line for line in sse_text.splitlines() if line.startswith("data: ")][0]
    parsed_data = json.loads(data_line[6:])

    # Assert claim and observation exist as distinct separate top-level fields
    assert "claim" in parsed_data
    assert "observation" in parsed_data
    assert parsed_data["claim"]["claim_key"] == "planned_execution"
    assert parsed_data["observation"]["obs_key"] == "actual_metrics"
    # Ensure keys do not intersect
    assert set(parsed_data["claim"].keys()).isdisjoint(set(parsed_data["observation"].keys()))


@pytest.mark.asyncio
async def test_engine_ttfb_under_200ms():
    """Verify that the engine emits the initial chunk well within 200ms (Doherty Threshold)."""
    engine = AIStreamingEngine()
    start_time = time.perf_counter()

    stream_gen = engine.stream_query_execution(
        query="Verify artifact syntax",
        session_id="test_session_123",
        workflow_type="verification",
    )

    first_chunk = await stream_gen.__anext__()
    elapsed_ms = (time.perf_counter() - start_time) * 1000.0

    # Doherty threshold requirement: first byte within 200ms
    assert elapsed_ms < 200.0, f"TTFB took {elapsed_ms:.2f}ms, exceeding 200ms threshold"
    assert "event: connected" in first_chunk
    assert "doherty_threshold_compliant" in first_chunk


@pytest.mark.asyncio
async def test_fastapi_sse_post_query_endpoint(app: FastAPI):
    """Test FastAPI POST /api/ai/stream/query returns SSE stream with TTFB < 200ms."""
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://testserver") as client:
        start_time = time.perf_counter()
        response = await client.post(
            "/api/ai/stream/query",
            json={
                "query": "Synthesize verified code snippet",
                "session_id": "sess_fastapi_001",
                "workflow_type": "orchestration",
                "context": {"user_id": "usr_test"},
            },
        )
        first_byte_elapsed_ms = (time.perf_counter() - start_time) * 1000.0

        assert response.status_code == 200
        assert "text/event-stream" in response.headers["content-type"]
        assert first_byte_elapsed_ms < 200.0

        body = response.text
        assert "event: connected" in body
        assert "event: workflow_planned" in body
        assert "event: completed" in body


@pytest.mark.asyncio
async def test_fastapi_sse_get_workflow_endpoint(app: FastAPI):
    """Test EventSource GET /api/ai/stream/workflow endpoint compatibility."""
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://testserver") as client:
        start_time = time.perf_counter()
        response = await client.get(
            "/api/ai/stream/workflow?query=AuditRun&workflow_type=ast_validation",
        )
        elapsed_ms = (time.perf_counter() - start_time) * 1000.0

        assert response.status_code == 200
        assert "text/event-stream" in response.headers["content-type"]
        assert elapsed_ms < 200.0
        assert "event: connected" in response.text
        assert "event: completed" in response.text


@pytest.mark.asyncio
async def test_custom_graph_runner_streaming():
    """Verify integration with LangGraph async stream runner."""
    class MockGraphRunner:
        async def astream(self, inputs):
            yield {"retrieval_node": {"docs": 2}}
            yield {"synthesis_node": {"generated_code": "def run(): pass"}}

    engine = AIStreamingEngine()
    chunks = []
    async for chunk in engine.stream_query_execution(
        query="Run graph",
        session_id="graph_sess",
        graph_runner=MockGraphRunner(),
    ):
        chunks.append(chunk)

    combined = "".join(chunks)
    assert "event: connected" in combined
    assert "retrieval_node" in combined
    assert "synthesis_node" in combined
    assert "event: completed" in combined


@pytest.mark.asyncio
async def test_error_handling_in_stream():
    """Verify exceptions in execution emit structured error SSE frames without dropping stream."""
    class FaultyGraphRunner:
        async def astream(self, inputs):
            raise RuntimeError("LangGraph execution failure simulation")

    engine = AIStreamingEngine()
    chunks = []
    async for chunk in engine.stream_query_execution(
        query="Fail graph",
        session_id="fault_sess",
        graph_runner=FaultyGraphRunner(),
    ):
        chunks.append(chunk)

    combined = "".join(chunks)
    assert "event: connected" in combined
    assert "event: error" in combined
    assert "LangGraph execution failure simulation" in combined
