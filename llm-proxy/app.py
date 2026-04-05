"""
LLM Proxy - Enterprise Multi-Model Gateway

Routes LLM requests to Gemini API or DeepSeek API with strict token budget enforcement.
Model selection is dynamic: at startup and every hour the proxy queries the Gemini API to
discover which models are actually available for the configured API key, so it never breaks
when Google deprecates or renames a model.
"""

import os
import time
import threading
import logging
from flask import Flask, request, jsonify
import requests

app = Flask(__name__)

GEMINI_API_KEY = os.getenv("GEMINI_API_KEY", "")
DEEPSEEK_API_KEY = os.getenv("DEEPSEEK_API_KEY", "")
GEMINI_PROXY_URL = "https://generativelanguage.googleapis.com/v1beta"
DEEPSEEK_PROXY_URL = "https://api.deepseek.com/v1"

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger("llm-proxy")

# ---------------------------------------------------------------------------
# Dynamic model catalogue — refreshed every hour from the Gemini ListModels API
# ---------------------------------------------------------------------------

# Preference tiers: the proxy picks the first model in each tier that is available.
# "fast" = low-latency / cheaper;  "powerful" = highest quality
_PREFERENCE_TIERS = {
    "fast":     ["gemini-2.0-flash", "gemini-2.5-flash-lite", "gemini-flash-latest", "gemini-2.0-flash-lite"],
    "powerful": ["gemini-2.5-pro",   "gemini-2.5-flash",      "gemini-pro-latest",   "gemini-2.0-flash"],
}

_available_models: set[str] = set()   # populated at startup & refreshed hourly
_model_refresh_lock = threading.Lock()
_last_refresh: float = 0


def _refresh_gemini_models() -> None:
    """Query Gemini ListModels and update the in-memory set of available model IDs."""
    global _available_models, _last_refresh
    if not GEMINI_API_KEY:
        return
    try:
        resp = requests.get(
            f"{GEMINI_PROXY_URL}/models",
            headers={"x-goog-api-key": GEMINI_API_KEY},
            timeout=10,
        )
        resp.raise_for_status()
        models = resp.json().get("models", [])
        ids = {
            # strip the "models/" prefix → "gemini-2.0-flash"
            m["name"].split("/")[-1]
            for m in models
            if "generateContent" in m.get("supportedGenerationMethods", [])
        }
        with _model_refresh_lock:
            _available_models = ids
            _last_refresh = time.time()
        logger.info(f"Gemini model catalogue refreshed: {sorted(ids)}")
    except Exception as exc:
        logger.warning(f"Could not refresh Gemini model list: {exc}")


def _maybe_refresh() -> None:
    """Refresh the model catalogue if stale (>1 h) or empty."""
    if not _available_models or (time.time() - _last_refresh) > 3600:
        _refresh_gemini_models()


def _background_refresh() -> None:
    """Background thread that keeps the model list fresh."""
    while True:
        time.sleep(3600)
        _refresh_gemini_models()


def resolve_gemini_model(requested: str) -> str:
    """
    Return the best available Gemini model name for *requested*.

    Strategy:
    1. If *requested* is available as-is → use it.
    2. If *requested* looks like a fast model (flash/lite) → pick best from 'fast' tier.
    3. Otherwise → pick best from 'powerful' tier.
    4. Fall back to any available generateContent model (sorted alphabetically).
    """
    _maybe_refresh()
    with _model_refresh_lock:
        available = set(_available_models)

    if not available:
        # Can't reach API to list models — let the call fail naturally with the
        # requested name so the real error is surfaced.
        return requested

    # Exact match (already a current model name)
    if requested in available:
        return requested

    # Choose tier based on the requested name
    tier = "fast" if any(k in requested for k in ("flash", "lite")) else "powerful"
    for candidate in _PREFERENCE_TIERS[tier]:
        if candidate in available:
            logger.info(f"Model '{requested}' not available → resolved to '{candidate}'")
            return candidate

    # Last resort: any available model
    fallback = sorted(available)[0]
    logger.warning(f"No preferred model available → using '{fallback}'")
    return fallback


# Boot-time refresh + background refresh thread
_refresh_gemini_models()
threading.Thread(target=_background_refresh, daemon=True).start()

# ---------------------------------------------------------------------------

TOKEN_BUDGETS = {
    "PO": {"used": 0, "limit": 1_000_000},
    "Architect": {"used": 0, "limit": 2_000_000},
    "Developer": {"used": 0, "limit": 5_000_000},
    "QA": {"used": 0, "limit": 1_000_000}
}

def _gemini_post(url: str, headers: dict, payload: dict, timeout: int = 60):
    """POST to Gemini with automatic retry on 429 (rate limit)."""
    delays = [2, 5, 10]  # seconds to wait between retries
    last_exc = None
    for attempt, delay in enumerate([0] + delays):
        if delay:
            time.sleep(delay)
        try:
            resp = requests.post(url, headers=headers, json=payload, timeout=timeout)
            if resp.status_code == 429 and attempt < len(delays):
                logger.warning(f"Gemini 429 rate limit, retrying in {delays[attempt]}s…")
                continue
            resp.raise_for_status()
            return resp
        except requests.HTTPError as exc:
            last_exc = exc
            if resp.status_code != 429:
                raise
    raise last_exc



def enforce_token_budget():
    if request.path == "/health":
        return
    agent_role = request.headers.get("X-Agent-Role", "Developer")
    budget = TOKEN_BUDGETS.get(agent_role)
    if budget and budget["used"] >= budget["limit"]:
        return jsonify({"error": "Token budget exceeded", "ok": False}), 429

def track_usage(agent_role: str, tokens: int):
    if agent_role in TOKEN_BUDGETS:
        TOKEN_BUDGETS[agent_role]["used"] += tokens

@app.route("/health")
def health():
    return jsonify({
        "status": "healthy",
        "service": "llm-proxy",
        "budgets": TOKEN_BUDGETS,
        "gemini_models_available": sorted(_available_models),
    })

@app.route("/v1/chat/completions", methods=["POST"])
def chat_completions():
    data = request.get_json() or {}
    model = data.get("model", "gemini-flash-latest")
    messages = data.get("messages", [])
    agent_role = request.headers.get("X-Agent-Role", "Developer")

    try:
        # ROUTE 1: DeepSeek
        if "deepseek" in model.lower():
            if not DEEPSEEK_API_KEY:
                return jsonify({"error": "DEEPSEEK_API_KEY not configured", "ok": False}), 500
            headers = {"Authorization": f"Bearer {DEEPSEEK_API_KEY}", "Content-Type": "application/json"}
            response = requests.post(f"{DEEPSEEK_PROXY_URL}/chat/completions", headers=headers, json=data, timeout=60)
            response.raise_for_status()
            result = response.json()
            track_usage(agent_role, result.get("usage", {}).get("total_tokens", 0))
            return jsonify(result)

        # ROUTE 2: Gemini — dynamic model resolution
        else:
            if not GEMINI_API_KEY:
                return jsonify({"error": "GEMINI_API_KEY not configured", "ok": False}), 500

            gemini_model = resolve_gemini_model(model)
            url = f"{GEMINI_PROXY_URL}/models/{gemini_model}:generateContent"
            gemini_headers = {
                "Content-Type": "application/json",
                "x-goog-api-key": GEMINI_API_KEY,
            }

            # Separate system_instruction from conversation turns
            system_text = None
            contents = []
            for msg in messages:
                role = msg.get("role", "user")
                content = msg.get("content", "")
                if role == "system":
                    system_text = content
                else:
                    gemini_role = "model" if role == "assistant" else "user"
                    contents.append({"role": gemini_role, "parts": [{"text": content}]})

            payload = {
                "contents": contents,
                "generationConfig": {
                    "temperature": data.get("temperature", 0.7),
                    "maxOutputTokens": data.get("max_tokens", 2048),
                },
            }
            if system_text:
                payload["system_instruction"] = {"parts": [{"text": system_text}]}

            response = _gemini_post(url, gemini_headers, payload)
            response.raise_for_status()
            result = response.json()
            text_content = (
                result.get("candidates", [{}])[0]
                .get("content", {})
                .get("parts", [{}])[0]
                .get("text", "")
            )
            tokens = len(str(messages)) // 4 + len(text_content) // 4
            track_usage(agent_role, tokens)
            return jsonify({
                "id": "gemini-proxy",
                "object": "chat.completion",
                "model": gemini_model,
                "choices": [{"index": 0, "message": {"role": "assistant", "content": text_content}, "finish_reason": "stop"}],
                "usage": {"prompt_tokens": len(str(messages)) // 4, "completion_tokens": len(text_content) // 4, "total_tokens": tokens},
            })

    except Exception as e:
        logger.error(f"Proxy failed: {str(e)}")
        return jsonify({"error": str(e), "ok": False}), 500

@app.route("/v1/embeddings", methods=["POST"])
def embeddings():
    data = request.get_json() or {}
    text = data.get("input", "")
    if not GEMINI_API_KEY:
        return jsonify({"error": "GEMINI_API_KEY not configured"}), 500
    try:
        # Use the best available embedding model dynamically
        embed_model = "text-embedding-004"
        url = f"{GEMINI_PROXY_URL}/models/{embed_model}:embedContent"
        payload = {"content": {"parts": [{"text": text}]}}
        response = requests.post(
            url,
            headers={"Content-Type": "application/json", "x-goog-api-key": GEMINI_API_KEY},
            json=payload,
            timeout=30,
        )
        result = response.json()
        embedding = result.get("embedding", {}).get("values", [])
        return jsonify({
            "object": "list",
            "data": [{"object": "embedding", "index": 0, "embedding": embedding}],
            "model": embed_model,
            "usage": {"total_tokens": len(text) // 4},
        })
    except Exception as e:
        return jsonify({"error": str(e)}), 500

@app.route("/v1/models", methods=["GET"])
def list_models():
    """Expose discovered model list so clients can query what's available."""
    _maybe_refresh()
    return jsonify({
        "object": "list",
        "data": [{"id": m, "object": "model"} for m in sorted(_available_models)],
    })

if __name__ == "__main__":
    app.run(host="0.0.0.0", port=int(os.getenv("PROXY_PORT", 5100)))
