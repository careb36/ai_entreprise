"""
Circuit Breaker Implementation for LLM Services in La Fabrique.

Provides a resilient state machine (CLOSED -> OPEN -> HALF_OPEN -> CLOSED) to prevent cascading
failures and queue hangs when LLM endpoints experience consecutive failures.
"""

import asyncio
from datetime import datetime, timezone
from enum import Enum
from typing import Any, Callable, Dict, Optional
import logging

logger = logging.getLogger(__name__)


class CircuitState(str, Enum):
    CLOSED = "CLOSED"
    OPEN = "OPEN"
    HALF_OPEN = "HALF_OPEN"


class CircuitBreakerOpenException(Exception):
    """Raised when an execution request is blocked because the circuit breaker is OPEN."""

    def __init__(
        self,
        message: str = "Circuit breaker is OPEN. LLM requests are paused.",
        remaining_seconds: float = 0.0,
        consecutive_failures: int = 0,
    ):
        super().__init__(message)
        self.message = message
        self.remaining_seconds = remaining_seconds
        self.consecutive_failures = consecutive_failures


class CircuitBreaker:
    """
    Thread-safe and async-aware Circuit Breaker.

    Attributes:
        name (str): Identifier for the circuit breaker instance.
        failure_threshold (int): Number of consecutive failures to open the circuit.
        recovery_timeout (float): Time in seconds to remain OPEN before attempting HALF_OPEN.
        half_open_success_threshold (int): Successful calls needed in HALF_OPEN to return to CLOSED.
    """

    def __init__(
        self,
        name: str = "llm_service",
        failure_threshold: int = 3,
        recovery_timeout: float = 30.0,
        half_open_success_threshold: int = 1,
    ):
        self.name = name
        self.failure_threshold = failure_threshold
        self.recovery_timeout = recovery_timeout
        self.half_open_success_threshold = half_open_success_threshold

        self._state = CircuitState.CLOSED
        self._consecutive_failures = 0
        self._consecutive_successes = 0
        self._last_failure_time: Optional[float] = None
        self._last_state_change = datetime.now(timezone.utc)
        self._lock = asyncio.Lock()

    @property
    def state(self) -> CircuitState:
        if self._state == CircuitState.OPEN and self._last_failure_time is not None:
            now = datetime.now(timezone.utc).timestamp()
            if now - self._last_failure_time >= self.recovery_timeout:
                return CircuitState.HALF_OPEN
        return self._state

    def get_status(self) -> Dict[str, Any]:
        """Returns diagnostic metrics and current state of the circuit breaker."""
        current_state = self.state
        remaining = 0.0
        if current_state == CircuitState.OPEN and self._last_failure_time is not None:
            now = datetime.now(timezone.utc).timestamp()
            remaining = max(0.0, self.recovery_timeout - (now - self._last_failure_time))

        return {
            "name": self.name,
            "state": current_state.value,
            "consecutive_failures": self._consecutive_failures,
            "failure_threshold": self.failure_threshold,
            "recovery_timeout": self.recovery_timeout,
            "remaining_cooldown_seconds": round(remaining, 2),
            "last_state_change": self._last_state_change.isoformat(),
        }

    def reset(self) -> None:
        """Manually reset the circuit breaker back to CLOSED state."""
        self._state = CircuitState.CLOSED
        self._consecutive_failures = 0
        self._consecutive_successes = 0
        self._last_failure_time = None
        self._last_state_change = datetime.now(timezone.utc)
        logger.info(f"CircuitBreaker '{self.name}' manually reset to CLOSED.")

    async def call(self, func: Callable, *args: Any, **kwargs: Any) -> Any:
        """
        Executes an async or sync function wrapped in circuit breaker protection.
        """
        async with self._lock:
            current_state = self.state

            if current_state == CircuitState.OPEN:
                now = datetime.now(timezone.utc).timestamp()
                remaining = max(
                    0.0,
                    self.recovery_timeout - (self._last_failure_time or (now - self.recovery_timeout)),
                )
                logger.warning(
                    f"CircuitBreaker '{self.name}' is OPEN. Fast-failing request. Cooldown remaining: {remaining:.1f}s"
                )
                raise CircuitBreakerOpenException(
                    message=f"LLM service '{self.name}' circuit breaker is OPEN due to consecutive failures. Request paused.",
                    remaining_seconds=remaining,
                    consecutive_failures=self._consecutive_failures,
                )

            if current_state == CircuitState.HALF_OPEN and self._state != CircuitState.HALF_OPEN:
                self._state = CircuitState.HALF_OPEN
                self._last_state_change = datetime.now(timezone.utc)
                logger.info(f"CircuitBreaker '{self.name}' transitioned to HALF_OPEN. Testing target recovery.")

        try:
            if asyncio.iscoroutinefunction(func):
                result = await func(*args, **kwargs)
            else:
                result = func(*args, **kwargs)

            await self._on_success()
            return result
        except CircuitBreakerOpenException:
            raise
        except Exception as exc:
            await self._on_failure(exc)
            raise exc

    async def _on_success(self) -> None:
        async with self._lock:
            if self._state == CircuitState.HALF_OPEN:
                self._consecutive_successes += 1
                if self._consecutive_successes >= self.half_open_success_threshold:
                    self._state = CircuitState.CLOSED
                    self._consecutive_failures = 0
                    self._consecutive_successes = 0
                    self._last_failure_time = None
                    self._last_state_change = datetime.now(timezone.utc)
                    logger.info(f"CircuitBreaker '{self.name}' successfully recovered to CLOSED state.")
            elif self._state == CircuitState.CLOSED:
                self._consecutive_failures = 0

    async def _on_failure(self, exc: Exception) -> None:
        async with self._lock:
            self._consecutive_failures += 1
            self._last_failure_time = datetime.now(timezone.utc).timestamp()
            logger.warning(
                f"CircuitBreaker '{self.name}' caught failure ({self._consecutive_failures}/{self.failure_threshold}): {exc}"
            )

            if self._consecutive_failures >= self.failure_threshold or self._state == CircuitState.HALF_OPEN:
                self._state = CircuitState.OPEN
                self._last_state_change = datetime.now(timezone.utc)
                logger.error(
                    f"CircuitBreaker '{self.name}' TRIPPED to OPEN state after {self._consecutive_failures} consecutive failure(s)."
                )


# Default global instance for orchestration LLM calls
default_llm_circuit_breaker = CircuitBreaker(
    name="default_llm_orchestrator",
    failure_threshold=3,
    recovery_timeout=30.0,
    half_open_success_threshold=1,
)
