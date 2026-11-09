"""
Resilient retry mechanism and exponential backoff for LangGraph and LangChain LLM calls.

Handles transient upstream LLM proxy timeouts and failures (502 Bad Gateway,
504 Gateway Timeout, 503 Service Unavailable, 429 Rate Limits, connection timeouts).
"""

import asyncio
import logging
import os
import random
import time
from functools import wraps
from typing import Any, Callable, Coroutine, Dict, List, Optional, Set, Tuple, Type, TypeVar, Union

from langchain_core.messages import BaseMessage
from langchain_core.runnables import Runnable, RunnableConfig, RunnableSerializable

logger = logging.getLogger(__name__)

# Default configuration parameters (configurable via environment variables)
DEFAULT_MAX_RETRIES = int(os.getenv("LLM_MAX_RETRIES", "3"))
DEFAULT_INITIAL_DELAY = float(os.getenv("LLM_RETRY_INITIAL_DELAY", "1.0"))
DEFAULT_MAX_DELAY = float(os.getenv("LLM_RETRY_MAX_DELAY", "30.0"))
DEFAULT_BACKOFF_FACTOR = float(os.getenv("LLM_RETRY_BACKOFF_FACTOR", "2.0"))
DEFAULT_JITTER = os.getenv("LLM_RETRY_JITTER", "true").lower() in ("true", "1", "yes")

# HTTP status codes indicating transient upstream degradation
RETRYABLE_STATUS_CODES: Set[int] = {429, 500, 502, 503, 504}

# Substrings in exception representations indicative of upstream proxy timeouts
RETRYABLE_SUBSTRINGS: Tuple[str, ...] = (
    "502",
    "504",
    "503",
    "bad gateway",
    "gateway timeout",
    "service unavailable",
    "timed out",
    "timeout",
    "connection reset",
    "remote disconnected",
    "connection error",
    "rate limit",
)

T = TypeVar("T")


def is_retryable_exception(exc: BaseException) -> bool:
    """
    Determine if an exception represents a transient failure eligible for retry.

    Inspects HTTP status codes on OpenAI and HTTPX exceptions, socket timeouts,
    and matching diagnostic substrings.
    """
    # Check for HTTP status code attribute (e.g. OpenAI APIStatusError, httpx.HTTPStatusError)
    status_code = getattr(exc, "status_code", None)
    if status_code is None:
        response = getattr(exc, "response", None)
        if response is not None:
            status_code = getattr(response, "status_code", None)

    if status_code is not None and int(status_code) in RETRYABLE_STATUS_CODES:
        return True

    # Check exception type names
    exc_type_name = type(exc).__name__
    retryable_types = {
        "APIConnectionError",
        "APITimeoutError",
        "RateLimitError",
        "InternalServerError",
        "BadGatewayError",
        "GatewayTimeoutError",
        "TimeoutException",
        "ReadTimeout",
        "ConnectTimeout",
        "NetworkError",
        "RemoteDisconnected",
        "ConnectionResetError",
        "TimeoutError",
    }
    if exc_type_name in retryable_types:
        return True

    # Inspect message representation
    msg = str(exc).lower()
    return any(sub in msg for sub in RETRYABLE_SUBSTRINGS)


def calculate_backoff_delay(
    attempt: int,
    initial_delay: float = DEFAULT_INITIAL_DELAY,
    max_delay: float = DEFAULT_MAX_DELAY,
    backoff_factor: float = DEFAULT_BACKOFF_FACTOR,
    jitter: bool = DEFAULT_JITTER,
) -> float:
    """
    Calculate backoff delay for the given attempt using exponential backoff with jitter.

    attempt: 0-indexed attempt count (0 is first failure, before retry 1).
    """
    delay = min(max_delay, initial_delay * (backoff_factor ** attempt))
    if jitter:
        # Full jitter between 50% and 100% of calculated delay
        delay = random.uniform(0.5 * delay, delay)
    return delay


def with_llm_retry(
    func: Optional[Callable[..., T]] = None,
    *,
    max_retries: int = DEFAULT_MAX_RETRIES,
    initial_delay: float = DEFAULT_INITIAL_DELAY,
    max_delay: float = DEFAULT_MAX_DELAY,
    backoff_factor: float = DEFAULT_BACKOFF_FACTOR,
    jitter: bool = DEFAULT_JITTER,
) -> Union[Callable[..., T], Callable[[Callable[..., T]], Callable[..., T]]]:
    """
    Decorator for synchronous functions executing LLM calls with exponential backoff.
    """
    def decorator(fn: Callable[..., T]) -> Callable[..., T]:
        @wraps(fn)
        def wrapper(*args: Any, **kwargs: Any) -> T:
            last_exception: Optional[BaseException] = None
            for attempt in range(max_retries + 1):
                try:
                    return fn(*args, **kwargs)
                except Exception as exc:
                    last_exception = exc
                    if attempt >= max_retries or not is_retryable_exception(exc):
                        logger.error(
                            "LLM call failed definitively (attempt %d/%d, retryable=%s): %s",
                            attempt + 1,
                            max_retries + 1,
                            is_retryable_exception(exc),
                            exc,
                        )
                        raise

                    delay = calculate_backoff_delay(
                        attempt=attempt,
                        initial_delay=initial_delay,
                        max_delay=max_delay,
                        backoff_factor=backoff_factor,
                        jitter=jitter,
                    )
                    logger.warning(
                        "LLM call encountered transient failure (%s: %s). "
                        "Retrying in %.2fs (attempt %d of %d)...",
                        type(exc).__name__,
                        exc,
                        delay,
                        attempt + 1,
                        max_retries,
                    )
                    time.sleep(delay)

            if last_exception:
                raise last_exception
            raise RuntimeError("Exhausted retries without exception capture")

        return wrapper

    if func is not None:
        return decorator(func)
    return decorator


def async_with_llm_retry(
    func: Optional[Callable[..., Coroutine[Any, Any, T]]] = None,
    *,
    max_retries: int = DEFAULT_MAX_RETRIES,
    initial_delay: float = DEFAULT_INITIAL_DELAY,
    max_delay: float = DEFAULT_MAX_DELAY,
    backoff_factor: float = DEFAULT_BACKOFF_FACTOR,
    jitter: bool = DEFAULT_JITTER,
) -> Union[
    Callable[..., Coroutine[Any, Any, T]],
    Callable[[Callable[..., Coroutine[Any, Any, T]]], Callable[..., Coroutine[Any, Any, T]]],
]:
    """
    Decorator for asynchronous coroutines executing LLM calls with exponential backoff.
    """
    def decorator(fn: Callable[..., Coroutine[Any, Any, T]]) -> Callable[..., Coroutine[Any, Any, T]]:
        @wraps(fn)
        async def wrapper(*args: Any, **kwargs: Any) -> T:
            last_exception: Optional[BaseException] = None
            for attempt in range(max_retries + 1):
                try:
                    return await fn(*args, **kwargs)
                except Exception as exc:
                    last_exception = exc
                    if attempt >= max_retries or not is_retryable_exception(exc):
                        logger.error(
                            "Async LLM call failed definitively (attempt %d/%d, retryable=%s): %s",
                            attempt + 1,
                            max_retries + 1,
                            is_retryable_exception(exc),
                            exc,
                        )
                        raise

                    delay = calculate_backoff_delay(
                        attempt=attempt,
                        initial_delay=initial_delay,
                        max_delay=max_delay,
                        backoff_factor=backoff_factor,
                        jitter=jitter,
                    )
                    logger.warning(
                        "Async LLM call encountered transient failure (%s: %s). "
                        "Retrying in %.2fs (attempt %d of %d)...",
                        type(exc).__name__,
                        exc,
                        delay,
                        attempt + 1,
                        max_retries,
                    )
                    await asyncio.sleep(delay)

            if last_exception:
                raise last_exception
            raise RuntimeError("Exhausted retries without exception capture")

        return wrapper

    if func is not None:
        return decorator(func)
    return decorator


class ResilientChatModelWrapper(Runnable):
    """
    Wrapper around LangChain chat models providing automatic exponential backoff
    on 502/504 timeouts across both sync (invoke) and async (ainvoke) interfaces.

    Seamlessly integrates into LangGraph workflows and supports tool binding.
    """

    def __init__(
        self,
        model: Any,
        max_retries: int = DEFAULT_MAX_RETRIES,
        initial_delay: float = DEFAULT_INITIAL_DELAY,
        max_delay: float = DEFAULT_MAX_DELAY,
        backoff_factor: float = DEFAULT_BACKOFF_FACTOR,
        jitter: bool = DEFAULT_JITTER,
    ) -> None:
        self.model = model
        self.max_retries = max_retries
        self.initial_delay = initial_delay
        self.max_delay = max_delay
        self.backoff_factor = backoff_factor
        self.jitter = jitter

    def invoke(self, input: Any, config: Optional[RunnableConfig] = None, **kwargs: Any) -> Any:
        @with_llm_retry(
            max_retries=self.max_retries,
            initial_delay=self.initial_delay,
            max_delay=self.max_delay,
            backoff_factor=self.backoff_factor,
            jitter=self.jitter,
        )
        def _call_invoke() -> Any:
            return self.model.invoke(input, config=config, **kwargs)

        return _call_invoke()

    async def ainvoke(self, input: Any, config: Optional[RunnableConfig] = None, **kwargs: Any) -> Any:
        @async_with_llm_retry(
            max_retries=self.max_retries,
            initial_delay=self.initial_delay,
            max_delay=self.max_delay,
            backoff_factor=self.backoff_factor,
            jitter=self.jitter,
        )
        async def _call_ainvoke() -> Any:
            return await self.model.ainvoke(input, config=config, **kwargs)

        return await _call_ainvoke()

    def stream(self, input: Any, config: Optional[RunnableConfig] = None, **kwargs: Any) -> Any:
        @with_llm_retry(
            max_retries=self.max_retries,
            initial_delay=self.initial_delay,
            max_delay=self.max_delay,
            backoff_factor=self.backoff_factor,
            jitter=self.jitter,
        )
        def _call_stream() -> Any:
            return self.model.stream(input, config=config, **kwargs)

        return _call_stream()

    async def astream(self, input: Any, config: Optional[RunnableConfig] = None, **kwargs: Any) -> Any:
        @async_with_llm_retry(
            max_retries=self.max_retries,
            initial_delay=self.initial_delay,
            max_delay=self.max_delay,
            backoff_factor=self.backoff_factor,
            jitter=self.jitter,
        )
        async def _call_astream() -> Any:
            return await self.model.astream(input, config=config, **kwargs)

        return await _call_astream()

    def batch(self, inputs: List[Any], config: Optional[RunnableConfig] = None, **kwargs: Any) -> List[Any]:
        @with_llm_retry(
            max_retries=self.max_retries,
            initial_delay=self.initial_delay,
            max_delay=self.max_delay,
            backoff_factor=self.backoff_factor,
            jitter=self.jitter,
        )
        def _call_batch() -> List[Any]:
            return self.model.batch(inputs, config=config, **kwargs)

        return _call_batch()

    async def abatch(self, inputs: List[Any], config: Optional[RunnableConfig] = None, **kwargs: Any) -> List[Any]:
        @async_with_llm_retry(
            max_retries=self.max_retries,
            initial_delay=self.initial_delay,
            max_delay=self.max_delay,
            backoff_factor=self.backoff_factor,
            jitter=self.jitter,
        )
        async def _call_abatch() -> List[Any]:
            return await self.model.abatch(inputs, config=config, **kwargs)

        return await _call_abatch()

    def bind_tools(self, tools: Any, **kwargs: Any) -> "ResilientChatModelWrapper":
        """Support LangChain / LangGraph bind_tools while preserving retry wrappers."""
        bound_model = self.model.bind_tools(tools, **kwargs)
        return ResilientChatModelWrapper(
            bound_model,
            max_retries=self.max_retries,
            initial_delay=self.initial_delay,
            max_delay=self.max_delay,
            backoff_factor=self.backoff_factor,
            jitter=self.jitter,
        )

    def bind(self, **kwargs: Any) -> "ResilientChatModelWrapper":
        """Support LangChain runnable binding."""
        bound_model = self.model.bind(**kwargs)
        return ResilientChatModelWrapper(
            bound_model,
            max_retries=self.max_retries,
            initial_delay=self.initial_delay,
            max_delay=self.max_delay,
            backoff_factor=self.backoff_factor,
            jitter=self.jitter,
        )

    def with_config(self, config: Optional[RunnableConfig] = None, **kwargs: Any) -> "ResilientChatModelWrapper":
        configured_model = self.model.with_config(config=config, **kwargs)
        return ResilientChatModelWrapper(
            configured_model,
            max_retries=self.max_retries,
            initial_delay=self.initial_delay,
            max_delay=self.max_delay,
            backoff_factor=self.backoff_factor,
            jitter=self.jitter,
        )

    def __getattr__(self, name: str) -> Any:
        return getattr(self.model, name)


def wrap_model_with_retry(
    model: Any,
    max_retries: int = DEFAULT_MAX_RETRIES,
    initial_delay: float = DEFAULT_INITIAL_DELAY,
    max_delay: float = DEFAULT_MAX_DELAY,
    backoff_factor: float = DEFAULT_BACKOFF_FACTOR,
    jitter: bool = DEFAULT_JITTER,
) -> ResilientChatModelWrapper:
    """
    Wrap a LangChain or LangGraph model with the exponential backoff retry wrapper.
    """
    if isinstance(model, ResilientChatModelWrapper):
        return model
    return ResilientChatModelWrapper(
        model=model,
        max_retries=max_retries,
        initial_delay=initial_delay,
        max_delay=max_delay,
        backoff_factor=backoff_factor,
        jitter=jitter,
    )
