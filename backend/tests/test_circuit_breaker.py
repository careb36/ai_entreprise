"""
Test Suite for Circuit Breaker and Orchestration Integration.

Verifies state transitions (CLOSED -> OPEN -> HALF_OPEN -> CLOSED), threshold mechanics,
and FastAPI endpoint graceful handling of task 'blocked' states when downstream failures recur.
"""

import asyncio
import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from backend.llm.circuit_breaker import (
    CircuitBreaker,
    CircuitBreakerOpenException,
    CircuitState,
)
from backend.orchestration.router import (
    router as orchestration_router,
    set_circuit_breaker,
)

app = FastAPI()
app.include_router(orchestration_router)
client = TestClient(app)


@pytest.fixture
def fresh_breaker():
    breaker = CircuitBreaker(
        name="test_breaker",
        failure_threshold=3,
        recovery_timeout=0.2,  # Short cooldown for test speed
        half_open_success_threshold=1,
    )
    set_circuit_breaker(breaker)
    return breaker


@pytest.mark.asyncio
async def test_circuit_breaker_initial_state_closed(fresh_breaker):
    assert fresh_breaker.state == CircuitState.CLOSED
    status = fresh_breaker.get_status()
    assert status["state"] == "CLOSED"
    assert status["consecutive_failures"] == 0


@pytest.mark.asyncio
async def test_circuit_breaker_successful_execution(fresh_breaker):
    async def sample_func():
        return "ok"

    res = await fresh_breaker.call(sample_func)
    assert res == "ok"
    assert fresh_breaker.state == CircuitState.CLOSED


@pytest.mark.asyncio
async def test_circuit_breaker_trips_to_open_on_consecutive_failures(fresh_breaker):
    async def failing_func():
        raise RuntimeError("LLM failure 502 Timeout")

    # Fail 1
    with pytest.raises(RuntimeError):
        await fresh_breaker.call(failing_func)
    assert fresh_breaker.state == CircuitState.CLOSED

    # Fail 2
    with pytest.raises(RuntimeError):
        await fresh_breaker.call(failing_func)
    assert fresh_breaker.state == CircuitState.CLOSED

    # Fail 3 (reaches threshold of 3)
    with pytest.raises(RuntimeError):
        await fresh_breaker.call(failing_func)
    assert fresh_breaker.state == CircuitState.OPEN


@pytest.mark.asyncio
async def test_circuit_breaker_fast_fails_when_open(fresh_breaker):
    async def failing_func():
        raise RuntimeError("Error")

    # Trip breaker
    for _ in range(3):
        with pytest.raises(RuntimeError):
            await fresh_breaker.call(failing_func)

    assert fresh_breaker.state == CircuitState.OPEN

    # Next call should immediately raise CircuitBreakerOpenException without invoking func
    called = False

    async def dummy():
        nonlocal called
        called = True

    with pytest.raises(CircuitBreakerOpenException) as exc_info:
        await fresh_breaker.call(dummy)

    assert not called
    assert "circuit breaker is OPEN" in str(exc_info.value)


@pytest.mark.asyncio
async def test_circuit_breaker_half_open_recovery(fresh_breaker):
    async def failing_func():
        raise RuntimeError("Error")

    # Trip breaker
    for _ in range(3):
        with pytest.raises(RuntimeError):
            await fresh_breaker.call(failing_func)

    assert fresh_breaker.state == CircuitState.OPEN

    # Wait for recovery timeout
    await asyncio.sleep(0.25)

    assert fresh_breaker.state == CircuitState.HALF_OPEN

    # Successful test call recovers circuit back to CLOSED
    async def success_func():
        return "recovered"

    result = await fresh_breaker.call(success_func)
    assert result == "recovered"
    assert fresh_breaker.state == CircuitState.CLOSED


@pytest.mark.asyncio
async def test_circuit_breaker_half_open_failure_reopens(fresh_breaker):
    async def failing_func():
        raise RuntimeError("Error")

    # Trip breaker
    for _ in range(3):
        with pytest.raises(RuntimeError):
            await fresh_breaker.call(failing_func)

    await asyncio.sleep(0.25)
    assert fresh_breaker.state == CircuitState.HALF_OPEN

    # Failure during HALF_OPEN trips immediately back to OPEN
    with pytest.raises(RuntimeError):
        await fresh_breaker.call(failing_func)

    assert fresh_breaker.state == CircuitState.OPEN


def test_orchestration_endpoint_consecutive_failures_and_task_blocking(fresh_breaker):
    # Send 1st failing request
    resp1 = client.post(
        "/api/v1/orchestration/execute",
        json={"task_id": "task-001", "prompt": "generate code", "simulate_failure": True},
    )
    assert resp1.status_code == 200
    data1 = resp1.json()
    assert data1["status"] == "failed"

    # Send 2nd failing request
    resp2 = client.post(
        "/api/v1/orchestration/execute",
        json={"task_id": "task-002", "prompt": "generate code", "simulate_failure": True},
    )
    assert resp2.status_code == 200
    data2 = resp2.json()
    assert data2["status"] == "failed"

    # Send 3rd failing request (trips circuit breaker)
    resp3 = client.post(
        "/api/v1/orchestration/execute",
        json={"task_id": "task-003", "prompt": "generate code", "simulate_failure": True},
    )
    assert resp3.status_code == 200
    data3 = resp3.json()
    assert data3["status"] == "blocked"
    assert "Circuit Breaker to OPEN" in data3["blocked_reason"]

    # 4th request (circuit OPEN, fast fail & task flagged blocked gracefully)
    resp4 = client.post(
        "/api/v1/orchestration/execute",
        json={"task_id": "task-004", "prompt": "generate code", "simulate_failure": False},
    )
    assert resp4.status_code == 200
    data4 = resp4.json()
    assert data4["status"] == "blocked"
    assert "circuit breaker is OPEN" in data4["blocked_reason"]
    assert data4["circuit_breaker_status"]["state"] == "OPEN"


def test_circuit_breaker_status_and_reset_endpoints(fresh_breaker):
    # Verify GET status
    res = client.get("/api/v1/orchestration/circuit-breaker/status")
    assert res.status_code == 200
    assert res.json()["state"] == "CLOSED"

    # Trip breaker via API
    for i in range(3):
        client.post(
            "/api/v1/orchestration/execute",
            json={"task_id": f"task-reset-{i}", "prompt": "test", "simulate_failure": True},
        )

    res_tripped = client.get("/api/v1/orchestration/circuit-breaker/status")
    assert res_tripped.json()["state"] == "OPEN"

    # Reset breaker via POST
    res_reset = client.post("/api/v1/orchestration/circuit-breaker/reset")
    assert res_reset.status_code == 200
    assert res_reset.json()["status"]["state"] == "CLOSED"

    # Subsequent request succeeds
    res_after = client.post(
        "/api/v1/orchestration/execute",
        json={"task_id": "task-post-reset", "prompt": "hello", "simulate_failure": False},
    )
    assert res_after.json()["status"] == "completed"
