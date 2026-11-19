"""
Which models a given provider currently serves, and which provider a model id
belongs to.

A model id names its provider by prefix, and the id must also be in that
provider's fetched catalogue before a request is sent anywhere. A mistyped or
retired id fails here instead of being forwarded to a provider that never
served it.

The catalogue sets themselves, and the hourly refresh that fills them, live in
``app``: the tests swap a set or a refresh out on that module. This module
holds what does not change at run time (the prefixes, the tier rules, the
deprecated ids), fetches one provider's list (:func:`refresh`), and shapes the
``/v1/models`` listing (:func:`listed_models`).
"""

import logging
import time

import requests

logger = logging.getLogger("llm-proxy")

# Tier classification rules applied in order — first match wins.
# Tuple: (substring_tests, tier_name, description)
_TIER_RULES: list[tuple[list[str], str, str]] = [
    (["preview"], "preview", "Experimental preview — may be unstable"),
    (["flash-lite", "lite"], "fast", "Lightweight, fastest responses"),
    (["2.5-pro", "3-pro"], "strategic", "Deep reasoning, architecture, stakeholder decisions"),
    (["pro"], "strategic", "Deep reasoning, architecture, stakeholder decisions"),
    (["flash"], "balanced", "Fast reasoning, coordination, reviews"),
    (["coder"], "code", "Specialised code generation & refactoring (~10x cheaper)"),
    (["chat"], "balanced", "General-purpose reasoning and conversation"),
]

# Deprecated Gemini ids mapped to their working equivalents.
GEMINI_ALIASES = {
    "gemini-2.5-flash": "gemini-3.8-flash",
    "gemini-2.5-pro": "gemini-3.1-pro-preview",
    "gemini-2.0-flash": "gemini-3.8-flash",
}

# Models that are no longer available to new users (Google deprecation).
GEMINI_DEPRECATED = {
    "gemini-2.5-flash",
    "gemini-2.5-pro",
    "gemini-2.0-flash",
    "gemini-2.0-flash-lite",
}

# Nebius ids carry a vendor segment ("nvidia/…", "meta-llama/…"), hence the
# vendor/ prefix rather than a bare model-name prefix.
PROVIDER_PREFIXES: dict[str, tuple[str, ...]] = {
    "gemini": ("gemini-", "gemma-"),
    "deepseek": ("deepseek-",),
    "qwen": ("qwen", "qwq-", "qvq-"),
    "nebius": ("nvidia/", "meta-llama/", "mistralai/", "deepseek-ai/", "qwen/", "google/"),
}


def classify_model(model_id: str) -> tuple[str, str]:
    """Return (tier, description) for a model ID based on naming conventions."""
    lower = model_id.lower()
    for substrings, tier, desc in _TIER_RULES:
        if any(s in lower for s in substrings):
            return tier, desc
    return "balanced", "General-purpose model"


def provider_for_model(model_id: str) -> str | None:
    """The provider whose prefix *model_id* carries, or ``None``."""
    lower = (model_id or "").strip().lower()
    for provider, prefixes in PROVIDER_PREFIXES.items():
        if lower.startswith(prefixes):
            return provider
    return None


def fetch_model_ids(provider: str, key: str, base_url: str) -> set[str]:
    """The provider's live chat-capable model ids.

    Raises whatever the request raises: the caller decides whether a failed
    refresh is fatal (it is not) and keeps the previous catalogue.
    """
    if provider == "gemini":
        resp = requests.get(
            f"{base_url}/models", headers={"x-goog-api-key": key}, timeout=10
        )
        resp.raise_for_status()
        return {
            m["name"].split("/")[-1]
            for m in resp.json().get("models", [])
            if "generateContent" in m.get("supportedGenerationMethods", [])
        }

    resp = requests.get(
        f"{base_url}/models", headers={"Authorization": f"Bearer {key}"}, timeout=10
    )
    resp.raise_for_status()
    skip = {
        "qwen": ("image", "audio", "video", "tts", "realtime", "wan", "embedding"),
        "nebius": ("embedding", "rerank"),
    }.get(provider, ())
    return {
        m.get("id")
        for m in resp.json().get("data", [])
        if m.get("id") and not any(k in m.get("id", "").lower() for k in skip)
    }


def refresh(provider: str, key: str, base_url: str, current: set[str], last_refresh: float) -> tuple[set[str], float]:
    """Fetch one provider's catalogue. A failure keeps the previous one."""
    if not key:
        return current, last_refresh
    try:
        ids = fetch_model_ids(provider, key, base_url)
    except Exception as e:
        logger.warning(f"{provider} model refresh failed: {e}")
        return current, last_refresh
    logger.info(f"{provider} models refreshed: {sorted(ids)}")
    return ids, time.time()


def listed_models(catalogue, keys: dict[str, str]) -> list[dict]:
    """The ``/v1/models`` entries: every id the chat endpoint routes, with its tier.

    *catalogue* maps a provider to its routable ids; *keys* holds each
    provider's API key, and a provider other than Gemini is listed only when
    its key is set.
    """
    models = []
    for model_id in sorted(catalogue("gemini") - GEMINI_DEPRECATED):
        tier, desc = classify_model(model_id)
        models.append({"id": model_id, "provider": "gemini", "tier": tier, "description": desc})

    # DeepSeek models are available only when the API key is configured.
    if keys.get("deepseek"):
        ds_ids = sorted(catalogue("deepseek"))
        for mid in ds_ids:
            tier = "fast" if "flash" in mid else "balanced"
            models.append({"id": mid, "provider": "deepseek", "tier": tier,
                           "description": f"DeepSeek {mid}"})

    # Qwen models are available only when the API key is configured.
    if keys.get("qwen"):
        q_ids = sorted(catalogue("qwen"))
        for mid in q_ids:
            lower = mid.lower()
            if "max" in lower:
                tier = "strategic"
            elif "flash" in lower:
                tier = "balanced"
            else:
                tier = "balanced"
            models.append({"id": mid, "provider": "qwen", "tier": tier,
                           "description": f"Qwen {mid}"})

    # Nebius hosts the NVIDIA open models (Nemotron); listed only with its key.
    if keys.get("nebius"):
        for mid in sorted(catalogue("nebius")):
            lower = mid.lower()
            if "nano" in lower:
                tier = "fast"
            elif "ultra" in lower or "253b" in lower or "340b" in lower:
                tier = "strategic"
            else:
                tier = "balanced"
            models.append({"id": mid, "provider": "nebius", "tier": tier,
                           "description": f"NVIDIA/Nebius {mid}"})
    return models


def pick_orchestrator(models: list[dict]) -> str:
    """
    The recommended model for short orchestration/meta tasks: DeepSeek Flash,
    the default team's model for every task that is not a judgement (PR #739).

    It is recommended whatever the catalogue lists, as the frontend's
    recommendation is: when DeepSeek is not configured the screen shows an id
    that is not in the live catalogue rather than another provider's model.
    *models* is kept for callers.
    """
    del models
    return "deepseek-flash"
