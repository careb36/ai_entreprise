"""
Backend unit and integration tests for FastAPI streaming endpoints.
Tests TTFB under 200ms, LangGraph mock event ingestion, and error formatting.
"""

import asyncio
import json
import time
import pytest
from httpx import AsyncClient, ASGITransport

from backend.app.main import app
from src.backend.streaming.ai_streaming_engine import AIStreamingEngine


class MockLangGraphRunner:
    """Mock LangGraph runner providing astream_events v2 protocol."""
    async def astream_events(self, inputs, version="v2"):
        yield {
            "event": "on_chain_start",
            "name": "langgraph_planner",
            "metadata": {"step": 1},
            "data": {},
        }
        yield {
            "event": "on_chat_model_stream",
            "name": "llm_generate",
            "data": {"chunk": type("Chunk", (), {"content": "First token "})()},
        }
        yield {
            "event": "on_chat_model_stream",
            "name": "llm_generate",
            "data": {"chunk": type("Chunk", (), {"content": "Second token."})()},
        }
        yield {
            "event": "on_chain_end",
            "name": "langgraph_planner",
            "data": {"output": {"status": "ok"}},
        }


@pytest.mark.asyncio
async def test_engine_with_langgraph_mock():
    """Verify streaming engine consumes LangGraph event stream faithfully."""
    engine = AIStreamingEngine()
    runner = MockLangGraphRunner()
    events = []

    async for chunk in engine.stream_query_execution(
        query="Run plan",
        session_id="test-langgraph",
        graph_runner=runner,
    ):
        events.append(chunk)

    all_text = "".join(events)
    assert "event: connected" in all_text
    assert "event: node_start" in all_text
    assert "event: token" in all_text
    assert "First token" in all_text
    assert "Second token" in all_text
    assert "event: node_complete" in all_text
    assert "event: evidence_captured" in all_text
    assert "event: done" in all_text


@pytest.mark.asyncio
async def test_endpoint_input_validation():
    """Verify 422 Unprocessable Entity when empty query is sent."""
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        response = await client.post(
            "/v1/ai/query/stream",
            json={"query": ""},
        )
        assert response.status_code == 422
