"""
Embeddings: Gemini's ``text-embedding-004`` when a key is set, otherwise (or
when that call fails) a deterministic local vector, so retrieval keeps
working without a provider. Only the provider's answer is charged.
"""

import logging
import math

import requests
from usage import estimated_usage

logger = logging.getLogger("llm-proxy")


def input_text(data: dict) -> str:
    """The request's ``input`` as one string; a list is joined by lines."""
    value = data.get("input", "")
    if isinstance(value, list):
        value = "\n".join(str(item) for item in value)
    return str(value)


def fallback_embedding(text: str, dims: int = 1536) -> list[float]:
    """Deterministic local embedding fallback used only when provider embedding fails."""
    vector = [0.0] * dims
    for index, byte in enumerate(text.encode("utf-8")):
        slot = (index * 131 + byte) % dims
        vector[slot] += (byte % 17) + 1
    norm = math.sqrt(sum(value * value for value in vector)) or 1.0
    return [value / norm for value in vector]


def _local(text: str) -> dict:
    return {
        "data": [{"embedding": fallback_embedding(text), "index": 0}],
        "model": "local-fallback-embedding",
    }


def embed(text: str, data: dict, *, api_key: str, base_url: str) -> tuple[dict, int]:
    """``(body, tokens to charge)`` for *text*; tokens is 0 for the local vector."""
    try:
        if api_key:
            payload = {
                "model": "models/text-embedding-004",
                "content": {
                    "parts": [{"text": text}],
                },
                "taskType": data.get("taskType", "RETRIEVAL_DOCUMENT"),
            }
            resp = requests.post(
                f"{base_url}/models/text-embedding-004:embedContent",
                headers={"x-goog-api-key": api_key},
                json=payload,
                timeout=60,
            )
            resp.raise_for_status()
            body = resp.json()
            values = body.get("embedding", {}).get("values") or []
            if values:
                estimate = max(1, len(text) // 4)
                return (
                    {
                        "data": [{"embedding": values, "index": 0}],
                        "model": "text-embedding-004",
                        **estimated_usage(estimate),
                    },
                    estimate,
                )
        return _local(text), 0
    except Exception as e:
        logger.warning("Embedding provider failed, using local fallback: %s", e)
        return _local(text), 0
