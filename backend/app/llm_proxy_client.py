"""
LLMProxyClient — HTTP client for the internal LLM proxy service.

The proxy (``llm-proxy/app.py``) centralises all model routing, API-key
management, token-budget enforcement, and the live model catalogue of each
configured provider (DeepSeek by default; Gemini, Qwen and Nebius optional).
This client provides a simple synchronous interface so that backend services
do not need to import or configure LLM SDKs directly.

All methods return a plain dict so callers can inspect ``result.get("error")``
without catching exceptions for common failure paths.
"""

import asyncio
import logging
import os
import time
from typing import Any

import httpx
import requests
from opentelemetry import propagate

logger = logging.getLogger("fabrique-backend")

# The model a call names when its caller names none: the default team's Flash
# model (``FLASH_MODEL`` in ``services/models/model_factory.py``).
DEFAULT_MODEL = "deepseek-flash"


def _json_body(response: Any) -> dict[str, Any] | None:
    """The proxy's JSON error body (provider, status, message), if it sent one."""
    try:
        body = response.json()
    except Exception:  # noqa: BLE001 - A non-JSON error body is described by its text.
        return None
    return body if isinstance(body, dict) else None


# The proxy's upstream budget when ``LLM_UPSTREAM_TIMEOUT_SECONDS`` is unset
# (``llm-proxy/timeouts.py`` holds the same default): long enough for a
# DeepSeek call that reasons before it answers.
DEFAULT_UPSTREAM_TIMEOUT_SECONDS = 300.0
# What the client waits beyond the proxy's budget: the proxy's own work and
# the time to send its answer, timeout or not, back. A shorter wait gives up
# on an answer the provider is still writing and has billed.
READ_TIMEOUT_MARGIN_SECONDS = 15.0
# The header that tells the proxy a call's answer is parsed, so it may retry
# one whose reasoning used the whole budget with thinking off.
PURPOSE_HEADER = "X-LLM-Purpose"

# The named reasons an answer carries in ``reason`` when the proxy gave none.
PROXY_READ_TIMEOUT = "proxy_read_timeout"
PROXY_CONNECTION_LOST = "proxy_connection_lost"
PROXY_UNREACHABLE = "proxy_unreachable"


def upstream_timeout_seconds() -> float:
    """``LLM_UPSTREAM_TIMEOUT_SECONDS``, or the default when unset or not a positive number."""
    try:
        value = float(os.getenv("LLM_UPSTREAM_TIMEOUT_SECONDS", ""))
    except ValueError:
        return DEFAULT_UPSTREAM_TIMEOUT_SECONDS
    return value if value > 0 else DEFAULT_UPSTREAM_TIMEOUT_SECONDS


def read_timeout_seconds() -> float:
    """How long a call waits for the proxy: its upstream budget plus a margin,
    so the proxy always answers (with a named error if need be) first."""
    return upstream_timeout_seconds() + READ_TIMEOUT_MARGIN_SECONDS


DEFAULT_TIMEOUT_SECONDS = read_timeout_seconds()


def _payload(
    messages: list,
    model: str,
    max_tokens: int | None,
    tools: list | None = None,
    *,
    role: str | None = None,
    structured: bool = False,
) -> dict[str, Any]:
    """The request body, with the call's thinking mode always explicit
    (``services/models/thinking.py``): never DeepSeek's implicit default."""
    from services.models import thinking

    payload: dict[str, Any] = {"messages": messages, "model": model}
    payload.update(
        thinking.request_fields(role, structured=structured, max_tokens=max_tokens)
    )
    if tools:
        payload["tools"] = tools
    return payload


def _transport_failure(exc: Exception) -> tuple[str, bool]:
    """The named reason of a request that got no answer, and whether it may be
    sent again: only one that never reached the proxy is (sending one the
    proxy received would bill its upstream call twice)."""
    text = str(exc)
    if (
        isinstance(
            exc, (requests.ConnectTimeout, httpx.ConnectTimeout, httpx.ConnectError)
        )
        or "NewConnectionError" in text
        or "Failed to establish" in text
        or "Connection refused" in text
    ):
        return PROXY_UNREACHABLE, True
    if isinstance(exc, (requests.Timeout, httpx.TimeoutException)):
        return PROXY_READ_TIMEOUT, False
    return PROXY_CONNECTION_LOST, False


def _failure_answer(reason: str, exc: Exception, timeout: float) -> dict[str, Any]:
    """The answer of a call the proxy never answered, its reason named."""
    described = {
        PROXY_READ_TIMEOUT: f"no answer from the LLM proxy within {timeout:.0f}s",
        PROXY_CONNECTION_LOST: "the LLM proxy closed the connection before answering",
        PROXY_UNREACHABLE: "the LLM proxy could not be reached",
    }[reason]
    return {"error": f"{reason}: {described} ({exc})", "ok": False, "reason": reason}


class LLMProxyClient:
    """
    Synchronous HTTP client for the Fabrique LLM proxy with Governance support.

    Args:
        base_url: Base URL of the LLM proxy service (default: ``http://localhost:5100``).
                  Override via the ``LLM_PROXY_URL`` environment variable in production.
    """

    def __init__(self, base_url: str | None = None):
        self.base_url = base_url or os.getenv("LLM_PROXY_URL", "http://localhost:5100")
        self.timeout = read_timeout_seconds()
        # A cap on the answer for the calls whose form is fixed (a review, a
        # brief, a plan); ``None`` leaves the provider's own limit.
        self.max_tokens: int | None = None
        # Whether the answer is parsed (JSON, a verdict, a probe): such a call
        # runs with thinking off (``services/models/thinking.py``).
        self.structured = False
        self.max_retries = int(os.getenv("LLM_PROXY_MAX_RETRIES", "2"))
        # The company whose provider connections a ``conn:<id>:<model>`` may use.
        self.company_id: int | None = None
        self.retry_backoff_sec = float(os.getenv("LLM_PROXY_RETRY_BACKOFF_SEC", "1.0"))

    def _get_headers(
        self, role: str = "Developer", bu_id: int | None = None
    ) -> dict[str, str]:
        """Construct headers for trace propagation and governance."""
        headers = {
            "Content-Type": "application/json",
            "X-Agent-Role": role,
        }
        if bu_id:
            headers["X-Business-Unit-ID"] = str(bu_id)
        headers[PURPOSE_HEADER] = "structured" if self.structured else "free"

        # Propagate OpenTelemetry context (TraceParent)
        propagate.inject(headers)
        return headers

    def _route(
        self, model: str, headers: dict[str, str]
    ) -> tuple[str, dict[str, Any] | None]:
        """The model to send and the headers updated for a company connection;
        an error answer instead when the connection cannot be used."""
        from services.models import provider_connections

        try:
            sent, extra = provider_connections.proxy_route(model, self.company_id)
        except Exception as exc:  # noqa: BLE001 - The reason is the answer.
            return model, {"error": f"provider connection: {exc}", "ok": False}
        headers.update(extra)
        return sent, None

    def _request(
        self,
        messages: list,
        model: str,
        role: str,
        max_tokens: int | None,
        tools: list[dict[str, Any]] | None,
    ) -> dict[str, Any]:
        return _payload(
            messages,
            model,
            max_tokens or self.max_tokens,
            tools,
            role=role,
            structured=self.structured,
        )

    def chat(
        self,
        messages: list,
        model: str = DEFAULT_MODEL,
        role: str = "Developer",
        bu_id: int | None = None,
        max_tokens: int | None = None,
        tools: list[dict[str, Any]] | None = None,
    ) -> dict[str, Any]:
        """
        Send a multi-turn chat request to the LLM proxy (OpenAI-compatible).

        ``max_tokens`` caps the answer; without it the provider's own limit
        applies. ``tools`` is the OpenAI ``tools`` list; the answer may then
        carry ``tool_calls`` in its message instead of content. Only a request
        that never reached the proxy is retried: an error the proxy answered
        has already cost its upstream call (and the proxy's own fallbacks), so
        sending it again would bill it again.
        """
        headers = self._get_headers(role, bu_id)
        model, refused = self._route(model, headers)
        if refused:
            return refused
        payload = self._request(messages, model, role, max_tokens, tools)

        for attempt in range(self.max_retries + 1):
            try:
                response = requests.post(
                    f"{self.base_url}/v1/chat/completions",
                    json=payload,
                    headers=headers,
                    timeout=self.timeout,
                )

                if response.ok:
                    return response.json()

                body = response.text[:500]
                logger.error(
                    "LLM proxy request failed: status=%s model=%s role=%s body=%s",
                    response.status_code,
                    model,
                    role,
                    body,
                )
                return {
                    "error": f"HTTP {response.status_code}: {body}",
                    "ok": False,
                    "status_code": response.status_code,
                    "detail": _json_body(response),
                }

            except requests.RequestException as e:
                reason, resend = _transport_failure(e)
                if not resend:
                    logger.error(
                        "LLM proxy request for model=%s role=%s got no answer (%s): %s; not resent",
                        model,
                        role,
                        reason,
                        e,
                    )
                    return _failure_answer(reason, e, self.timeout)
                if attempt < self.max_retries:
                    delay = self.retry_backoff_sec * (attempt + 1)
                    logger.warning(
                        "LLM proxy request exception for model=%s role=%s (attempt %s/%s): %s; retrying in %.1fs",
                        model,
                        role,
                        attempt + 1,
                        self.max_retries + 1,
                        e,
                        delay,
                    )
                    time.sleep(delay)
                    continue

                logger.error("LLM Proxy call failed after retries: %s", e)
                return _failure_answer(reason, e, self.timeout)

    async def chat_async(
        self,
        messages: list,
        model: str = DEFAULT_MODEL,
        role: str = "Developer",
        bu_id: int | None = None,
        max_tokens: int | None = None,
        tools: list[dict[str, Any]] | None = None,
    ) -> dict[str, Any]:
        """
        Async variant of ``chat()`` used by LangGraph async nodes.
        """
        headers = self._get_headers(role, bu_id)
        model, refused = await asyncio.to_thread(self._route, model, headers)
        if refused:
            return refused
        payload = self._request(messages, model, role, max_tokens, tools)

        for attempt in range(self.max_retries + 1):
            try:
                async with httpx.AsyncClient(timeout=self.timeout) as client:
                    response = await client.post(
                        f"{self.base_url}/v1/chat/completions",
                        json=payload,
                        headers=headers,
                    )

                if response.is_success:
                    result = response.json()
                    if response.headers.get("X-Quota-Warning") == "true":
                        result["quota_warning"] = True
                        result["quota_used"] = response.headers.get("X-Quota-Used")
                        result["quota_limit"] = response.headers.get("X-Quota-Limit")
                    return result

                body = response.text[:500]
                logger.error(
                    "LLM proxy async request failed: status=%s model=%s role=%s body=%s",
                    response.status_code,
                    model,
                    role,
                    body,
                )
                return {
                    "error": f"HTTP {response.status_code}: {body}",
                    "ok": False,
                    "status_code": response.status_code,
                    "detail": _json_body(response),
                }

            except httpx.RequestError as e:
                reason, resend = _transport_failure(e)
                if not resend:
                    logger.error(
                        "LLM proxy async request for model=%s role=%s got no answer (%s): %s; not resent",
                        model,
                        role,
                        reason,
                        e,
                    )
                    return _failure_answer(reason, e, self.timeout)
                if attempt < self.max_retries:
                    delay = self.retry_backoff_sec * (attempt + 1)
                    logger.warning(
                        "LLM proxy async exception for model=%s role=%s (attempt %s/%s): %s; retrying in %.1fs",
                        model,
                        role,
                        attempt + 1,
                        self.max_retries + 1,
                        e,
                        delay,
                    )
                    await asyncio.sleep(delay)
                    continue

                logger.error("LLM Proxy async call failed after retries: %s", e)
                return _failure_answer(reason, e, self.timeout)

    def get_available_models(self) -> dict:
        """
        Fetch the live model catalogue from the proxy.

        Returns a dict with keys:
          - ``models``: list of dicts with id, provider, tier, description
          - ``orchestrator``: recommended model for meta-tasks (DeepSeek Flash)

        Returns an empty dict on error so callers can fall back gracefully.
        """
        try:
            response = requests.get(f"{self.base_url}/v1/models", timeout=60)
            response.raise_for_status()
            return response.json()
        except requests.RequestException as e:
            logger.warning(f"Failed to fetch model catalogue: {e}")
            return {}

    def embed(
        self, text: str, role: str = "Developer", bu_id: int | None = None
    ) -> list | None:
        """Retrieve a dense vector embedding."""
        try:
            headers = self._get_headers(role, bu_id)
            response = requests.post(
                f"{self.base_url}/v1/embeddings",
                json={"input": text},
                headers=headers,
                timeout=self.timeout,
            )
            response.raise_for_status()
            # The proxy returns {"data": [{"embedding": [...]}]} (OpenAI style)
            data = response.json().get("data", [])
            if data:
                return data[0].get("embedding")
            return None
        except requests.RequestException as e:
            logger.error(f"LLM Proxy embedding failed: {e}")
            return None
