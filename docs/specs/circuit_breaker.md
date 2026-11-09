# Orchestration Circuit Breaker Specification

## Overview
The Circuit Breaker pattern protects the La Fabrique FastAPI orchestration layer from queue hanging and unhandled cascading failures when downstream LLM services experience repeated timeouts or 5xx errors.

## State Machine
1. **CLOSED**: Normal operations. Downstream calls proceed. Consecutive failure counter is reset on success.
2. **OPEN**: Tripped when `consecutive_failures >= failure_threshold` (default: 3). Downstream requests fast-fail immediately without executing downstream calls. Orchestration endpoints flag tasks gracefully as `status="blocked"`.
3. **HALF_OPEN**: Activated after `recovery_timeout` (default: 30s) elapses. A trial request tests downstream service recovery.
   - If successful: transitions back to **CLOSED**.
   - If failure: transitions immediately back to **OPEN**.

## API Endpoints
- `POST /api/v1/orchestration/execute`: Executes orchestration tasks guarded by circuit breaker. Returns `status: "blocked"` when circuit is OPEN.
- `GET /api/v1/orchestration/circuit-breaker/status`: Monitoring endpoint returning state, failure counts, and remaining cooldown.
- `POST /api/v1/orchestration/circuit-breaker/reset`: Administrative reset trigger.
