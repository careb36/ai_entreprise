"""
One chat completion on one of the deployment's providers.

Gemini has its own request shape (``gemini.py``); DeepSeek, Qwen and Nebius
speak the OpenAI chat API. The caller passes the key and base URL at call
time, so ``app`` reads them from its own globals (where the tests replace
them). Every call goes through ``upstream.post_upstream``.
"""

import upstream
from gemini import call_gemini
from usage import openai_reported_count, openai_text, with_usage


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
    upstream.check_openai_answer(provider, model, status, result)
    return with_usage(result, openai_reported_count(result), data.get("messages", []), openai_text(result))


def call_provider(
    provider: str, model: str, data: dict, timeout: float, *, api_key: str, base_url: str
) -> tuple[dict, int]:
    """``(result, tokens)`` from *provider*, or an ``UpstreamError``."""
    if provider == "gemini":
        return call_gemini(model, data, timeout, post=upstream.post_upstream, base_url=base_url, api_key=api_key)
    return call_openai_compatible(provider, model, data, timeout, api_key=api_key, base_url=base_url)
