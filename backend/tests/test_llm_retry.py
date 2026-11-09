"""
Automated tests for LLM exponential backoff and retry mechanism.

Verifies:
1. Handling and recovery from 502 Bad Gateway timeouts.
2. Handling and recovery from 504 Gateway Timeout errors.
3. Exponential backoff calculation and jitter bounds.
4. Maximum retry limits and failure propagation when retries are exhausted.
5. Non-retryable error handling (400 Bad Request, 401 Unauthorized) without retries.
6. LangGraph / LangChain integration with ResilientChatModelWrapper.
7. Simulated agent node completion after upstream transient failure.
"""

import asyncio
from unittest.mock import MagicMock, call, patch
import pytest
from langchain_core.messages import AIMessage, HumanMessage

from backend.llm.retry import (
    ResilientChatModelWrapper,
    async_with_llm_retry,
    calculate_backoff_delay,
    is_retryable_exception,
    with_llm_retry,
    wrap_model_with_retry,
)


class MockHTTPStatusError(Exception):
    def __init__(self, status_code: int, message: str = "HTTP error"):
        super().__init__(f"{status_code}: {message}")
        self.status_code = status_code


class MockAPIStatusError(Exception):
    def __init__(self, status_code: int, message: str = "API error"):
        super().__init__(f"Error code: {status_code} - {message}")
        self.status_code = status_code


def test_is_retryable_exception():
    assert is_retryable_exception(MockHTTPStatusError(502, "Bad Gateway"))
    assert is_retryable_exception(MockHTTPStatusError(504, "Gateway Timeout"))
    assert is_retryable_exception(MockHTTPStatusError(503, "Service Unavailable"))
    assert is_retryable_exception(MockHTTPStatusError(429, "Rate limit exceeded"))
    assert is_retryable_exception(Exception("LLM call failed: gemini 502 timed out after 110s"))
    assert is_retryable_exception(Exception("504 Gateway Time-out"))
    assert is_retryable_exception(TimeoutError("Connection timed out"))

    # Non-retryable
    assert not is_retryable_exception(MockHTTPStatusError(400, "Bad Request"))
    assert not is_retryable_exception(MockHTTPStatusError(401, "Unauthorized"))
    assert not is_retryable_exception(ValueError("Invalid argument"))


def test_calculate_backoff_delay():
    # Without jitter, strictly exponential
    delay_0 = calculate_backoff_delay(0, initial_delay=1.0, backoff_factor=2.0, jitter=False)
    delay_1 = calculate_backoff_delay(1, initial_delay=1.0, backoff_factor=2.0, jitter=False)
    delay_2 = calculate_backoff_delay(2, initial_delay=1.0, backoff_factor=2.0, jitter=False)

    assert delay_0 == 1.0
    assert delay_1 == 2.0
    assert delay_2 == 4.0

    # Max delay clamp
    delay_large = calculate_backoff_delay(10, initial_delay=1.0, max_delay=10.0, jitter=False)
    assert delay_large == 10.0

    # With jitter bounds
    for attempt in range(4):
        jittered = calculate_backoff_delay(attempt, initial_delay=1.0, backoff_factor=2.0, jitter=True)
        raw = min(DEFAULT_MAX_DELAY if 'DEFAULT_MAX_DELAY' in dir() else 30.0, 1.0 * (2.0 ** attempt))
        assert 0.5 * raw <= jittered <= raw


def test_sync_retry_recovers_from_502():
    attempts = 0

    @with_llm_retry(max_retries=3, initial_delay=0.01, jitter=False)
    def call_gemini():
        nonlocal attempts
        attempts += 1
        if attempts == 1:
            raise MockHTTPStatusError(502, "Bad Gateway: LLM call failed: gemini 502 timed out")
        return "Success on attempt 2"

    result = call_gemini()
    assert result == "Success on attempt 2"
    assert attempts == 2


def test_sync_retry_recovers_from_504():
    attempts = 0

    @with_llm_retry(max_retries=3, initial_delay=0.01, jitter=False)
    def call_proxy():
        nonlocal attempts
        attempts += 1
        if attempts <= 2:
            raise MockAPIStatusError(504, "Gateway Timeout: upstream provider didn't answer")
        return "Recovered after 2 timeouts"

    result = call_proxy()
    assert result == "Recovered after 2 timeouts"
    assert attempts == 3


def test_sync_retry_exhaustion_raises():
    attempts = 0

    @with_llm_retry(max_retries=2, initial_delay=0.01, jitter=False)
    def always_502():
        nonlocal attempts
        attempts += 1
        raise MockHTTPStatusError(502, "Bad Gateway")

    with pytest.raises(MockHTTPStatusError) as exc_info:
        always_502()

    assert exc_info.value.status_code == 502
    assert attempts == 3  # initial attempt + 2 retries


def test_non_retryable_fails_immediately():
    attempts = 0

    @with_llm_retry(max_retries=3, initial_delay=0.01, jitter=False)
    def bad_request():
        nonlocal attempts
        attempts += 1
        raise MockHTTPStatusError(400, "Function call missing argument")

    with pytest.raises(MockHTTPStatusError):
        bad_request()

    assert attempts == 1  # Never retried


@pytest.mark.asyncio
async def test_async_retry_recovers_from_502():
    attempts = 0

    @async_with_llm_retry(max_retries=3, initial_delay=0.01, jitter=False)
    async def async_call():
        nonlocal attempts
        attempts += 1
        if attempts == 1:
            raise MockHTTPStatusError(502, "gemini 502 timed out after 110s")
        return AIMessage(content="Agent execution completed successfully")

    result = await async_call()
    assert result.content == "Agent execution completed successfully"
    assert attempts == 2


@pytest.mark.asyncio
async def test_resilient_wrapper_ainvoke():
    mock_model = MagicMock()
    attempts = 0

    async def mock_ainvoke(*args, **kwargs):
        nonlocal attempts
        attempts += 1
        if attempts == 1:
            raise MockHTTPStatusError(502, "502 Bad Gateway")
        return AIMessage(content="Final artifact generated")

    mock_model.ainvoke = mock_ainvoke
    wrapper = wrap_model_with_retry(mock_model, max_retries=3, initial_delay=0.01, jitter=False)

    messages = [HumanMessage(content="Generate verification schema")]
    response = await wrapper.ainvoke(messages)

    assert response.content == "Final artifact generated"
    assert attempts == 2


def test_resilient_wrapper_invoke_and_bind_tools():
    mock_model = MagicMock()
    attempts = 0

    def mock_invoke(*args, **kwargs):
        nonlocal attempts
        attempts += 1
        if attempts == 1:
            raise MockHTTPStatusError(504, "504 Gateway Timeout")
        return AIMessage(content="Tool execution confirmed")

    mock_model.invoke = mock_invoke
    wrapper = ResilientChatModelWrapper(mock_model, max_retries=2, initial_delay=0.01, jitter=False)

    response = wrapper.invoke([HumanMessage(content="Test sync call")])
    assert response.content == "Tool execution confirmed"
    assert attempts == 2

    # Verify tool binding returns a wrapped resilient model
    bound = wrapper.bind_tools([])
    assert isinstance(bound, ResilientChatModelWrapper)


@pytest.mark.asyncio
async def test_simulated_langgraph_agent_node_recovers():
    """Simulates a LangGraph state machine node invoking LLM with transient 502 failure."""
    state = {"task_id": "TASK-102", "deliverable": None, "status": "pending"}

    # Mock the LLM call within a LangGraph node
    call_count = 0

    async def mock_llm_call(prompt: str):
        nonlocal call_count
        call_count += 1
        if call_count == 1:
            raise MockHTTPStatusError(502, "LLM call failed: gemini 502 timed out after 110s")
        return "Verified code deliverable"

    @async_with_llm_retry(max_retries=3, initial_delay=0.01, jitter=False)
    async def langgraph_agent_node(current_state: dict):
        res = await mock_llm_call(f"Process task {current_state['task_id']}")
        return {
            **current_state,
            "deliverable": res,
            "status": "completed",
        }

    updated_state = await langgraph_agent_node(state)
    assert updated_state["status"] == "completed"
    assert updated_state["deliverable"] == "Verified code deliverable"
    assert call_count == 2
