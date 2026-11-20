"""
Embeddings: a local semantic model by default, the hash vector when it cannot
load, Gemini's ``text-embedding-004`` only when the operator chose it.

DeepSeek, the default team's only provider, has no embeddings endpoint (its
API reference lists chat, FIM, models, balance, files and tools; read
2026-11-20). So the default needs no key and no outside service
(``EMBEDDING_PROVIDER``):

- ``local`` (the default): multilingual-e5-small run on CPU in this process
  (``local_model.py``, issue #755). It finds meaning, in Spanish and English,
  and nothing leaves the proxy. When the model cannot load (a file missing or
  not matching its pinned hash, the runtime absent, the self-check failed) the
  hash vector stands in and ``/health`` says why.
- ``local-hash``: the deterministic hash vector of issue #751, on purpose. It
  finds near-identical text, not meaning.
- ``gemini``: only when chosen and its key is set. A Gemini key configured for
  some role does not, by itself, send every memory to Google (GDPR art. 25 and
  28: no processor the operator did not choose). When the Gemini call fails
  a local vector is used and the failure logged. Only the provider's answer is
  charged.

Every vector has ``DIMS`` values, the width of ``vector_memory.embedding``
(``db/init-db/init.sql``). The local model's 384 values and Gemini's 768 are
padded with zeros, which keeps the cosine distance between two vectors from
the same source unchanged. Vectors from different sources are not comparable
with each other, so every answer names its ``model``: the backend records it
beside the vector, compares only vectors of the same model, and re-embeds the
others once a semantic model answers.
"""

import logging
import math
import os

import local_model
import requests
from usage import estimated_usage

logger = logging.getLogger("llm-proxy")

DIMS = 1536
PROVIDERS = ("local", "local-hash", "gemini")
HASH_MODEL = "local-fallback-embedding"
LOCAL_MODEL = HASH_MODEL  # the name #751 gave the hash vector, kept for its callers
_HASH_MEANING = "finds near-identical text, not meaning; nothing leaves the proxy"


def _raw() -> str:
    return os.getenv("EMBEDDING_PROVIDER", "local").strip().lower() or "local"


def selected() -> str:
    """``EMBEDDING_PROVIDER`` as configured: ``local`` (the default), ``local-hash`` or ``gemini``."""
    value = _raw()
    return value if value in PROVIDERS else "local"


def key_for(gemini_key: str) -> str:
    """The Gemini key to embed with, or "" when Gemini was not chosen."""
    return gemini_key if selected() == "gemini" else ""


def _local_capability(prefix: str = "") -> dict:
    """The local model when it loads, else the hash vector, with why."""
    reason = local_model.ensure_loaded()
    if reason is None:
        return {"state": "degraded" if prefix else "on", "provider": "local", "model": local_model.NAME,
                "reason": prefix + f"memories are embedded by the local semantic model {local_model.NAME} "
                          "(multilingual, on CPU in the proxy); no key, nothing leaves the proxy"}
    return {"state": "degraded", "provider": "local-hash", "model": HASH_MODEL,
            "reason": prefix + f"the local model {local_model.NAME} is not loaded ({reason}): memory "
                      f"search uses the hash vector, which {_HASH_MEANING}"}


def capability(gemini_key: str) -> dict:
    """What ``/health`` says of the embeddings: ``state``, ``provider``, ``model`` and why."""
    raw = _raw()
    if raw not in PROVIDERS:
        return _local_capability(f"EMBEDDING_PROVIDER={raw!r} is not one of {', '.join(PROVIDERS)}: ")
    if raw == "gemini" and gemini_key:
        return {"state": "on", "provider": "gemini", "model": "text-embedding-004",
                "reason": "EMBEDDING_PROVIDER=gemini: memories are embedded by Google Gemini"}
    if raw == "gemini":
        return _local_capability("EMBEDDING_PROVIDER=gemini but GEMINI_API_KEY is not set: ")
    if raw == "local-hash":
        return {"state": "degraded", "provider": "local-hash", "model": HASH_MODEL,
                "reason": f"EMBEDDING_PROVIDER=local-hash: memory search uses the hash vector, which "
                          f"{_HASH_MEANING}"}
    return _local_capability()


def input_text(data: dict) -> str:
    """The request's ``input`` as one string; a list is joined by lines."""
    value = data.get("input", "")
    if isinstance(value, list):
        value = "\n".join(str(item) for item in value)
    return str(value)


def fallback_embedding(text: str, dims: int = DIMS) -> list[float]:
    """Deterministic hash vector: ``local-hash``, and the stand-in when the model cannot load."""
    vector = [0.0] * dims
    for index, byte in enumerate(text.encode("utf-8")):
        slot = (index * 131 + byte) % dims
        vector[slot] += (byte % 17) + 1
    norm = math.sqrt(sum(value * value for value in vector)) or 1.0
    return [value / norm for value in vector]


def _hash(text: str) -> dict:
    return {"data": [{"embedding": fallback_embedding(text), "index": 0}], "model": HASH_MODEL}


def _local(text: str) -> dict:
    """The local model's vector; the hash vector when chosen or when the model is not loaded."""
    if selected() == "local-hash":
        return _hash(text)
    try:
        return {"data": [{"embedding": local_model.embed(text, DIMS), "index": 0}],
                "model": local_model.NAME}
    except local_model.ModelUnavailable:
        return _hash(text)
    except Exception as e:  # noqa: BLE001 - one text the runtime fails on still gets a vector
        logger.warning("Local embedding model failed on this text, using the hash vector: %s", e)
        return _hash(text)


def embed(text: str, data: dict, *, api_key: str, base_url: str) -> tuple[dict, int]:
    """``(body, tokens to charge)`` for *text*; tokens is 0 for a local vector.

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
                logger.warning("Embedding provider returned %d values, more than %d: using a local vector",
                               len(values), DIMS)
        return _local(text), 0
    except Exception as e:
        logger.warning("Embedding provider failed, using a local vector: %s", e)
        return _local(text), 0
