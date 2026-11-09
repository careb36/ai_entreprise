"""
FastAPI Orchestration Router with Circuit Breaker Protection.

Handles task execution dispatches to LLM services, intercepting consecutive downstream failures
and gracefully flagging tasks as 'blocked' rather than hanging queue processing.
"""

from typing import Any, Dict, Optional
from fastapi import APIRouter, HTTPException, status
from pydantic import BaseModel, Field

from llm.circuit_breaker import (
    CircuitBreaker,
    CircuitBreakerOpenException,
    default_llm_circuit_breaker,
)

router = APIRouter(prefix="/api/v1/orchestration", tags=["orchestration"])


class TaskExecutionRequest(BaseModel):
    task_id: str = Field(..., description="Unique task identifier")
    prompt: str = Field(..., description="Prompt or payload for LLM execution")
    parameters: Optional[Dict[str, Any]] = Field(default_factory=dict)
    simulate_failure: bool = Field(
        default=False, description="Flag for testing/simulating LLM failure"
    )


class TaskExecutionResponse(BaseModel):
    task_id: str
    status: str = Field(..., description="Task state: 'completed', 'failed', or 'blocked'")
    result: Optional[str] = None
    error_message: Optional[str] = None
    blocked_reason: Optional[str] = None
    circuit_breaker_status: Dict[str, Any]


# Injectable breaker instance for router endpoints
_active_circuit_breaker: CircuitBreaker = default_llm_circuit_breaker


def get_circuit_breaker() -> CircuitBreaker:
    return _active_circuit_breaker


def set_circuit_breaker(breaker: CircuitBreaker) -> None:
    global _active_circuit_breaker
    _active_circuit_breaker = breaker


async def _mock_llm_service_call(request: TaskExecutionRequest) -> str:
    """Simulates call to LLM service provider."""
    if request.simulate_failure:
        raise RuntimeError(f"Downstream LLM provider HTTP 502 Gateway Timeout for task {request.task_id}")
    return f"Execution completed for task {request.task_id}: Generated response for '{request.prompt}'"


@router.post("/execute", response_model=TaskExecutionResponse, status_code=status.HTTP_200_OK)
async def execute_task(request: TaskExecutionRequest) -> TaskExecutionResponse:
    """
    Executes an orchestration task via LLM service guarded by a Circuit Breaker.
    
    If the service encounters consecutive failures and the circuit trips OPEN,
    the request pauses gracefully, returning a status of 'blocked' with details.
    """
    breaker = get_circuit_breaker()

    try:
        result = await breaker.call(_mock_llm_service_call, request)
        return TaskExecutionResponse(
            task_id=request.task_id,
            status="completed",
            result=result,
            circuit_breaker_status=breaker.get_status(),
        )
    except CircuitBreakerOpenException as cbe:
        # Gracefully handle circuit breaker open state: do not hang queue, flag as blocked
        return TaskExecutionResponse(
            task_id=request.task_id,
            status="blocked",
            blocked_reason=cbe.message,
            circuit_breaker_status=breaker.get_status(),
        )
    except Exception as exc:
        cb_status = breaker.get_status()
        if cb_status["state"] == "OPEN":
            return TaskExecutionResponse(
                task_id=request.task_id,
                status="blocked",
                blocked_reason=f"Task execution failed and tripped Circuit Breaker to OPEN: {str(exc)}",
                circuit_breaker_status=cb_status,
            )
        
        return TaskExecutionResponse(
            task_id=request.task_id,
            status="failed",
            error_message=f"LLM call failed: {str(exc)}",
            circuit_breaker_status=cb_status,
        )


@router.get("/circuit-breaker/status", status_code=status.HTTP_200_OK)
async def get_circuit_breaker_status() -> Dict[str, Any]:
    """Returns the operational status and metrics of the orchestration circuit breaker."""
    breaker = get_circuit_breaker()
    return breaker.get_status()


@router.post("/circuit-breaker/reset", status_code=status.HTTP_200_OK)
async def reset_circuit_breaker() -> Dict[str, Any]:
    """Manually resets the circuit breaker state to CLOSED."""
    breaker = get_circuit_breaker()
    breaker.reset()
    return {"message": "Circuit breaker reset successfully", "status": breaker.get_status()}
