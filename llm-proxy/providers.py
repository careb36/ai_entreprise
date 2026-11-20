"""
One chat completion on one of the deployment's providers.

Gemini has its own request shape (``gemini.py``); DeepSeek, Qwen and Nebius
speak the OpenAI chat API. The caller passes the key and base URL at call
time, so ``app`` reads them from its own globals (where the tests replace
them). Every call goes through ``upstream.post_upstream``.

DeepSeek's ``thinking`` field is made explicit here and sent to DeepSeek
only (``thinking.py``). A structured call whose thinking answer came back
empty because the reasoning used the whole budget is retried once, on the
same model, with thinking off, within what is left of the call's budget; the
answer then says so in ``thinking_retry``.

Tool calls a model wrote as text are read back into ``tool_calls``
(``inline_tool_calls.py``); when the request offered no tools, the call is
asked once more with ``NO_TOOLS_NOTE`` and the answer says so in
``inline_tool_calls_retry``.
"""

import logging
import time

import inline_tool_calls
import thinking
import upstream
from gemini import call_gemini
from usage import openai_reported_count, openai_text, with_usage

logger = logging.getLogger("llm-proxy")

# The least time worth a retry without thinking; less, and the first error stands.
MIN_RETRY_SECONDS = 5.0


def call_openai_compatible(
    provider: str, model: str, data: dict, timeout: float, *, api_key: str, base_url: str
) -> tuple[dict, int]:
    status, result = upstream.post_upstream(
        provider,
        model,
        f"{base_url}/chat/completions",
        {"Authorization": f"Bearer {api_key}"},
        {**data, "model": model},
        timeout,
    )
    inline_tool_calls.normalize(provider, model, status, result, data)
    upstream.check_openai_answer(provider, model, status, result)
    return with_usage(result, openai_reported_count(result), data.get("messages", []), openai_text(result))


def call_provider(
    provider: str,
    model: str,
    data: dict,
    timeout: float,
    *,
    api_key: str,
    base_url: str,
    structured: bool = False,
) -> tuple[dict, int]:
    """``(result, tokens)`` from *provider*, or an ``UpstreamError``."""
    if provider == "gemini":
        return call_gemini(model, data, timeout, post=upstream.post_upstream, base_url=base_url, api_key=api_key)
    deadline = time.monotonic() + timeout
    payload = thinking.prepare(provider, data)
    try:
        return call_openai_compatible(provider, model, payload, timeout, api_key=api_key, base_url=base_url)
    except upstream.UpstreamError as first:
        remaining = deadline - time.monotonic()
        retry = _retry_payload(provider, payload, first.reason, structured)
        if retry is None or remaining < MIN_RETRY_SECONDS:
            raise
        payload_again, label = retry
        logger.warning("%s; retrying once on %s (%s)", first, model, label)
        try:
            result, tokens = call_openai_compatible(
                provider, model, payload_again, remaining, api_key=api_key, base_url=base_url)
        except upstream.UpstreamError as second:
            second.tokens += first.tokens
            raise
        result[label] = {"reason": first.reason, "first_attempt_tokens": first.tokens}
        return result, tokens + first.tokens


def _retry_payload(provider: str, payload: dict, reason: str | None, structured: bool) -> tuple[dict, str] | None:
    """The request to send once more after *reason*, and the answer's key that says so."""
    if reason == inline_tool_calls.INLINE_TOOL_CALLS_WITHOUT_TOOLS:
        note = {"role": "user", "content": inline_tool_calls.NO_TOOLS_NOTE}
        return {**payload, "messages": [*payload.get("messages", []), note]}, "inline_tool_calls_retry"
    if thinking.retry_without(provider, payload, reason, structured):
        return thinking.without_thinking(payload), "thinking_retry"
    return None
