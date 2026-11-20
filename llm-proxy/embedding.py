"""
Embeddings: a deterministic local vector by default, Gemini's
``text-embedding-004`` only when the operator chose it.

DeepSeek, the default team's only provider, has no embeddings endpoint (its
API reference lists chat, FIM, models, balance, files and tools; read
2026-11-20). So the default needs no key and no outside service: a local
vector computed here, which finds near-identical text rather than meaning,
and nothing leaves the proxy (issue #751). Gemini embeds only when
``EMBEDDING_PROVIDER=gemini`` and its key is set: a Gemini key configured for
some role does not, by itself, send every memory to Google (GDPR art. 25 and
28: no processor the operator did not choose). When the Gemini call fails the
local vector is used and the failure logged. Only the provider's answer is
charged.

Every vector has ``DIMS`` values, the width of ``vector_memory.embedding``
(``db/init-db/init.sql``). Gemini's 768 values are padded with zeros, which
keeps the cosine distance between two Gemini vectors unchanged; vectors from
different sources are not comparable with each other, so switching the
provider leaves earlier memories matching poorly until they are written again.
"""

import logging
import math
import os

import requests
from usage import estimated_usage

logger = logging.getLogger("llm-proxy")

DIMS = 1536
PROVIDERS = ("local", "gemini")
LOCAL_MODEL = "local-fallback-embedding"


def selected() -> str:
    """``EMBEDDING_PROVIDER`` as configured: ``local`` (the default) or ``gemini``."""
    value = os.getenv("EMBEDDING_PROVIDER", "local").strip().lower() or "local"
    return value if value in PROVIDERS else "local"


def key_for(gemini_key: str) -> str:
    """The Gemini key to embed with, or "" when Gemini was not chosen."""
    return gemini_key if selected() == "gemini" else ""


def capability(gemini_key: str) -> dict:
    """What ``/health`` says of the embeddings: ``state``, ``provider`` and why."""
    raw = os.getenv("EMBEDDING_PROVIDER", "local").strip().lower() or "local"
    if raw not in PROVIDERS:
        return {"state": "degraded", "provider": "local", "model": LOCAL_MODEL,
                "reason": f"EMBEDDING_PROVIDER={raw!r} is not one of {', '.join(PROVIDERS)}: "
                          "the local vector is used"}
    if raw == "gemini" and gemini_key:
        return {"state": "on", "provider": "gemini", "model": "text-embedding-004",
                "reason": "EMBEDDING_PROVIDER=gemini: memories are embedded by Google Gemini"}
    if raw == "gemini":
        return {"state": "degraded", "provider": "local", "model": LOCAL_MODEL,
                "reason": "EMBEDDING_PROVIDER=gemini but GEMINI_API_KEY is not set: the local vector is used"}
    return {"state": "degraded", "provider": "local", "model": LOCAL_MODEL,
            "reason": "no embedding provider chosen (DeepSeek has no embeddings endpoint): memory search "
                      "uses a local vector that finds near-identical text, not meaning; nothing leaves "
                      "the proxy"}


def input_text(data: dict) -> str:
    """The request's ``input`` as one string; a list is joined by lines."""
    value = data.get("input", "")
    if isinstance(value, list):
        value = "\n".join(str(item) for item in value)
    return str(value)


def fallback_embedding(text: str, dims: int = DIMS) -> list[float]:
    """Deterministic local embedding: the default, and the stand-in when the provider fails."""
    vector = [0.0] * dims
    for index, byte in enumerate(text.encode("utf-8")):
        slot = (index * 131 + byte) % dims
        vector[slot] += (byte % 17) + 1
    norm = math.sqrt(sum(value * value for value in vector)) or 1.0
    return [value / norm for value in vector]


def _local(text: str) -> dict:
    return {
        "data": [{"embedding": fallback_embedding(text), "index": 0}],
        "model": LOCAL_MODEL,
    }


def embed(text: str, data: dict, *, api_key: str, base_url: str) -> tuple[dict, int]:
    """``(body, tokens to charge)`` for *text*; tokens is 0 for the local vector.

    *api_key* is the Gemini key when Gemini was chosen (``key_for``), else "".
    """
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
            if values and len(values) <= DIMS:
                estimate = max(1, len(text) // 4)
                return (
                    {
                        "data": [{"embedding": values + [0.0] * (DIMS - len(values)), "index": 0}],
                        "model": "text-embedding-004",
                        **estimated_usage(estimate),
                    },
                    estimate,
                )
            if values:
                logger.warning("Embedding provider returned %d values, more than %d: using the local vector",
                               len(values), DIMS)
        return _local(text), 0
    except Exception as e:
        logger.warning("Embedding provider failed, using local fallback: %s", e)
        return _local(text), 0
