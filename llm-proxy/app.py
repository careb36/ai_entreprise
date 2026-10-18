"""
LLM Proxy - Enterprise Multi-Model Gateway v2.0 (Phase 4)

Governance-ready proxy that routes requests to Gemini or DeepSeek with:
- **Distributed Tracing**: OpenTelemetry instrumentation.
- **Dynamic Model Resolution**: Hourly refresh of available Gemini models.
- **Quota Enforcement**: Persistent token tracking per Business Unit via PostgreSQL.
"""

import os
import time
import threading
import logging
import math
from flask import Flask, request, jsonify
import requests
from sqlalchemy import create_engine, text

# --- OpenTelemetry ---
from opentelemetry import trace
from opentelemetry.trace import StatusCode
from opentelemetry.sdk.trace import TracerProvider
from opentelemetry.sdk.trace.export import BatchSpanProcessor
from opentelemetry.exporter.otlp.proto.http.trace_exporter import OTLPSpanExporter
from opentelemetry.instrumentation.flask import FlaskInstrumentor
from opentelemetry.instrumentation.requests import RequestsInstrumentor
from opentelemetry.sdk.resources import Resource, SERVICE_NAME

app = Flask(__name__)


def _is_truthy(value: str | None) -> bool:
    return (value or "").strip().lower() in {"1", "true", "yes", "on"}


def _resolve_otlp_endpoint() -> str | None:
    if _is_truthy(os.getenv("OTEL_SDK_DISABLED")):
        return None
    explicit_endpoint = os.getenv("OTEL_EXPORTER_OTLP_TRACES_ENDPOINT") or os.getenv("OTEL_EXPORTER_OTLP_ENDPOINT")
    if explicit_endpoint:
        return explicit_endpoint.rstrip("/")
    if _is_truthy(os.getenv("OTEL_EXPORTER_OTLP_ENABLED")):
        return "http://localhost:4318"
    return None

resource = Resource.create({SERVICE_NAME: "llm-proxy"})
tracer_provider = TracerProvider(resource=resource)
endpoint_base = _resolve_otlp_endpoint()
if endpoint_base:
    otlp_exporter = OTLPSpanExporter(endpoint=endpoint_base + "/v1/traces")
    span_processor = BatchSpanProcessor(otlp_exporter)
    tracer_provider.add_span_processor(span_processor)
trace.set_tracer_provider(tracer_provider)
FlaskInstrumentor().instrument_app(app)
RequestsInstrumentor().instrument()
_tracer = trace.get_tracer(__name__)

# --- Config & Logging ---
GEMINI_API_KEY = os.getenv("GEMINI_API_KEY", "")
DEEPSEEK_API_KEY = os.getenv("DEEPSEEK_API_KEY", "")
QWEN_API_KEY = os.getenv("QWEN_API_KEY", "")
GEMINI_PROXY_URL = "https://generativelanguage.googleapis.com/v1beta"
DEEPSEEK_PROXY_URL = "https://api.deepseek.com/v1"
QWEN_PROXY_URL = "https://token-plan.maas.qwencloudapi.com/compatible-mode/v1"

# --- API Key Validation (deferred to after logger is defined) ---

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger("llm-proxy")
if endpoint_base:
    logger.info("OpenTelemetry exporter enabled for llm-proxy: %s/v1/traces", endpoint_base)
else:
    logger.info("OpenTelemetry exporter disabled for llm-proxy")


# --- API Key Validation ---
def _validate_api_keys():
    """Validate API keys at startup and log warnings for invalid/missing keys."""
    errors = []
    if DEEPSEEK_API_KEY:
        try:
            resp = requests.post(
                f"{DEEPSEEK_PROXY_URL}/chat/completions",
                headers={"Authorization": f"Bearer {DEEPSEEK_API_KEY}"},
                json={"model": "deepseek-chat", "messages": [{"role": "user", "content": "ping"}], "max_tokens": 1},
                timeout=10,
            )
            if resp.status_code == 401 or resp.status_code == 403:
                errors.append("DEEPSEEK_API_KEY is invalid")
            else:
                logger.info("DeepSeek API key validated successfully")
        except Exception as e:
            logger.warning(f"Could not validate DeepSeek API key: {e}")
    else:
        logger.warning("DEEPSEEK_API_KEY is not set")

    if QWEN_API_KEY:
        try:
            resp = requests.post(
                f"{QWEN_PROXY_URL}/chat/completions",
                headers={"Authorization": f"Bearer {QWEN_API_KEY}"},
                json={"model": "qwen3.6-flash", "messages": [{"role": "user", "content": "ping"}], "max_tokens": 1},
                timeout=10,
            )
            if resp.status_code == 401 or resp.status_code == 403 or "invalid_api_key" in resp.text:
                errors.append("QWEN_API_KEY is invalid")
            else:
                logger.info("Qwen API key validated successfully")
        except Exception as e:
            logger.warning(f"Could not validate Qwen API key: {e}")
    else:
        logger.warning("QWEN_API_KEY is not set")

    if errors:
        for err in errors:
            logger.error(f"API KEY VALIDATION FAILED: {err}")
        logger.error("Some API keys are invalid. Agent tasks will fail until keys are corrected.")

_validate_api_keys()


def _fallback_embedding(text: str, dims: int = 1536) -> list[float]:
    """Deterministic local embedding fallback used only when provider embedding fails."""
    vector = [0.0] * dims
    for index, byte in enumerate(text.encode("utf-8")):
        slot = (index * 131 + byte) % dims
        vector[slot] += (byte % 17) + 1
    norm = math.sqrt(sum(value * value for value in vector)) or 1.0
    return [value / norm for value in vector]

# --- Database ---
def _build_database_url() -> str:
    host = os.getenv("POSTGRES_HOST", "localhost")
    port = os.getenv("POSTGRES_PORT", "5432")
    user = os.getenv("POSTGRES_USER", "factory")
    password = os.getenv("POSTGRES_PASSWORD", "")
    db = os.getenv("POSTGRES_DB", "factory")
    return f"postgresql://{user}:{password}@{host}:{port}/{db}"

engine = create_engine(_build_database_url(), pool_pre_ping=True)

# --- Dynamic Model Catalogue ---

# Preference order for fallback resolution (most preferred first).
# Only used when the exact requested model is not in the live catalogue.
_PREFERENCE_TIERS = {
    "fast":     ["gemini-2.5-flash", "gemini-flash-latest", "gemini-2.5-flash-lite",
                 "gemini-2.0-flash-lite", "gemini-2.0-flash"],
    "powerful": ["gemini-2.5-pro",   "gemini-3-pro-preview", "gemini-pro-latest",
                 "gemini-2.5-flash"],
}

# Tier classification rules applied in order — first match wins.
# Tuple: (substring_tests, tier_name, description)
_TIER_RULES: list[tuple[list[str], str, str]] = [
    (["preview"],            "preview",    "Experimental preview — may be unstable"),
    (["flash-lite", "lite"], "fast",       "Lightweight, fastest responses"),
    (["2.5-pro", "3-pro"],   "strategic",  "Deep reasoning, architecture, stakeholder decisions"),
    (["pro"],                "strategic",  "Deep reasoning, architecture, stakeholder decisions"),
    (["flash"],              "balanced",   "Fast reasoning, coordination, reviews"),
    (["coder"],              "code",       "Specialised code generation & refactoring (~10x cheaper)"),
    (["chat"],               "balanced",   "General-purpose reasoning and conversation"),
]

_available_models: set[str] = set()
_model_refresh_lock = threading.Lock()
_last_refresh: float = 0


def _classify_model(model_id: str) -> tuple[str, str]:
    """Return (tier, description) for a model ID based on naming conventions."""
    lower = model_id.lower()
    for substrings, tier, desc in _TIER_RULES:
        if any(s in lower for s in substrings):
            return tier, desc
    return "balanced", "General-purpose model"


def _refresh_gemini_models():
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
        ids = {
            m["name"].split("/")[-1]
            for m in resp.json().get("models", [])
            if "generateContent" in m.get("supportedGenerationMethods", [])
        }
        with _model_refresh_lock:
            _available_models = ids
            _last_refresh = time.time()
        logger.info(f"Models refreshed: {sorted(ids)}")
    except Exception as e:
        logger.warning(f"Model refresh failed: {e}")


def resolve_gemini_model(requested: str) -> str:
    """
    Return the best available Gemini model for the requested name.
    Falls back to the same capability tier if the exact model is unavailable.
    """
    if not _available_models:
        _refresh_gemini_models()
    with _model_refresh_lock:
        if requested in _available_models:
            return requested
        tier = "fast" if any(k in requested for k in ("flash", "lite")) else "powerful"
        for candidate in _PREFERENCE_TIERS[tier]:
            if candidate in _available_models:
                return candidate
    return requested


# Refresh models once at startup; background thread refreshes every hour.
_refresh_gemini_models()
threading.Thread(target=lambda: (time.sleep(3600), _refresh_gemini_models()), daemon=True).start()

# --- Governance Middleware ---
@app.before_request
def enforce_business_unit_quota():
    if request.path == "/health": return
    bu_id = request.headers.get("X-Business-Unit-ID")
    if not bu_id: return
    try:
        with engine.connect() as conn:
            query = text("SELECT token_quota_limit, token_quota_used FROM business_units WHERE id = :id")
            res = conn.execute(query, {"id": bu_id}).fetchone()
            if not res: return jsonify({"error": "Business Unit not found"}), 404
            limit, used = res
            if limit > 0 and used >= limit:
                return jsonify({"error": "Quota exceeded", "used": used, "limit": limit}), 429
    except Exception as e:
        logger.error(f"Quota check failed: {e}")


@app.after_request
def add_quota_warning_header(response):
    """Add X-Quota-Warning header when a business unit has consumed ≥ 80 % of its limit."""
    bu_id = request.headers.get("X-Business-Unit-ID")
    if not bu_id:
        return response
    try:
        with engine.connect() as conn:
            res = conn.execute(
                text("SELECT token_quota_limit, token_quota_used FROM business_units WHERE id = :id"),
                {"id": bu_id},
            ).fetchone()
            if res:
                limit, used = res
                if limit > 0 and used >= limit * 0.8:
                    response.headers["X-Quota-Warning"] = "true"
                    response.headers["X-Quota-Used"] = str(used)
                    response.headers["X-Quota-Limit"] = str(limit)
    except Exception as e:
        logger.error(f"Quota warning check failed: {e}")
    return response

# --- Usage Tracking ---
def track_usage_in_db(bu_id, tokens):
    """Track token usage in the database for a business unit."""
    if not bu_id:
        return
    try:
        with engine.connect() as conn:
            conn.execute(
                text("UPDATE business_units SET token_quota_used = token_quota_used + :tokens WHERE id = :id"),
                {"tokens": tokens, "id": bu_id},
            )
            conn.commit()
    except Exception as e:
        logger.error(f"Failed to track usage in DB: {e}")


# --- API Endpoints ---

@app.route("/health")
def health():
    return jsonify({"status": "healthy", "database": "connected", "models": sorted(_available_models)})


@app.route("/v1/models")
def list_models():
    """
    Return a structured catalogue of all available models with tier classification.

    Response schema:
      {
        "models": [
          {
            "id":          "gemini-2.5-pro",
            "provider":    "gemini" | "deepseek",
            "tier":        "strategic" | "balanced" | "code" | "fast" | "preview",
            "description": "..."
          },
          ...
        ],
        "orchestrator": "gemini-2.5-flash",   // recommended model for meta-tasks
        "refreshed_at": 1712345678.0
      }
    """
    if not _available_models:
        _refresh_gemini_models()

    models = []
    with _model_refresh_lock:
        for model_id in sorted(_available_models):
            tier, desc = _classify_model(model_id)
            models.append({"id": model_id, "provider": "gemini", "tier": tier, "description": desc})

    # DeepSeek models are available only when the API key is configured.
    if DEEPSEEK_API_KEY:
        models.extend([
            {"id": "deepseek-chat", "provider": "deepseek", "tier": "balanced",
             "description": "DeepSeek Chat — general-purpose reasoning and conversation"},
            {"id": "deepseek-flash", "provider": "deepseek", "tier": "fast",
             "description": "DeepSeek Flash — fast, lightweight responses"},
        ])

    # Qwen models are available only when the API key is configured.
    if QWEN_API_KEY:
        models.extend([
            {"id": "qwen3.7-max",   "provider": "qwen", "tier": "strategic",
             "description": "Qwen 3.7 Max — highest capability model"},
            {"id": "qwen3.7-plus", "provider": "qwen", "tier": "strategic",
             "description": "Qwen 3.7 Plus — strong reasoning, architecture, and stakeholder decisions"},
            {"id": "qwen3.6-flash", "provider": "qwen", "tier": "balanced",
             "description": "Qwen 3.6 Flash — fast reasoning and coordination"},
        ])

    # Pick the best non-preview balanced/fast model for orchestration tasks
    # (team suggestion, routing decisions) — prefer stability over power.
    orchestrator = _pick_orchestrator(models)

    return jsonify({"models": models, "orchestrator": orchestrator, "refreshed_at": _last_refresh})


def _pick_orchestrator(models: list[dict]) -> str:
    """
    Choose the best model for short orchestration/meta tasks (fast, stable, not preview).
    Preference: balanced > fast > strategic, never preview.
    """
    preferred_ids = [
        "gemini-2.5-flash", "gemini-flash-latest", "gemini-2.5-flash-lite",
        "gemini-2.0-flash", "gemini-2.5-pro",
    ]
    available_ids = {m["id"] for m in models if m["tier"] != "preview"}
    for candidate in preferred_ids:
        if candidate in available_ids:
            return candidate
    # Fall back to any non-preview balanced model
    for m in models:
        if m["tier"] in ("balanced", "fast") and m["tier"] != "preview":
            return m["id"]
    return models[0]["id"] if models else "gemini-flash-latest"


@app.route("/v1/chat/completions", methods=["POST"])
def chat_completions():
    data = request.json
    model = data.get("model", "gemini-flash-latest")
    messages = data.get("messages", [])
    bu_id = request.headers.get("X-Business-Unit-ID")
    agent_role = request.headers.get("X-Agent-Role", "Developer")

    try:
        if "qwen" in model.lower():
            resp = requests.post(
                f"{QWEN_PROXY_URL}/chat/completions",
                headers={"Authorization": f"Bearer {QWEN_API_KEY}"},
                json=data,
                timeout=60,
            )
            result = resp.json()
            tokens = result.get("usage", {}).get("total_tokens", 0)
        elif "deepseek" in model.lower():
            resp = requests.post(f"{DEEPSEEK_PROXY_URL}/chat/completions", headers={"Authorization": f"Bearer {DEEPSEEK_API_KEY}"}, json=data, timeout=60)
            result = resp.json()
            tokens = result.get("usage", {}).get("total_tokens", 0)
        else:
            gemini_model = resolve_gemini_model(model)
            contents = [{"role": "model" if m["role"] == "assistant" else "user", "parts": [{"text": m["content"]}]} for m in messages if m["role"] != "system"]
            system = next((m["content"] for m in messages if m["role"] == "system"), None)
            payload = {"contents": contents, "generationConfig": {"temperature": data.get("temperature", 0.7)}}
            if system: payload["system_instruction"] = {"parts": [{"text": system}]}
            
            resp = requests.post(f"{GEMINI_PROXY_URL}/models/{gemini_model}:generateContent", headers={"x-goog-api-key": GEMINI_API_KEY}, json=payload, timeout=60)
            res = resp.json()
            text_out = res["candidates"][0]["content"]["parts"][0]["text"]
            tokens = (len(str(messages)) + len(text_out)) // 4
            result = {
                "choices": [{"message": {"role": "assistant", "content": text_out}}],
                "usage": {"total_tokens": tokens},
                "model": gemini_model
            }
        
        track_usage_in_db(bu_id, tokens)
        return jsonify(result)
    except Exception as e:
        return jsonify({"error": str(e)}), 500


@app.route("/v1/embeddings", methods=["POST"])
def embeddings():
    data = request.json or {}
    input_text = data.get("input", "")
    if isinstance(input_text, list):
        input_text = "\n".join(str(item) for item in input_text)
    input_text = str(input_text)

    bu_id = request.headers.get("X-Business-Unit-ID")

    if not input_text.strip():
        return jsonify({"error": "input is required"}), 400

    try:
        if GEMINI_API_KEY:
            payload = {
                "model": "models/text-embedding-004",
                "content": {
                    "parts": [{"text": input_text}],
                },
                "taskType": data.get("taskType", "RETRIEVAL_DOCUMENT"),
            }
            resp = requests.post(
                f"{GEMINI_PROXY_URL}/models/text-embedding-004:embedContent",
                headers={"x-goog-api-key": GEMINI_API_KEY},
                json=payload,
                timeout=60,
            )
            resp.raise_for_status()
            body = resp.json()
            values = body.get("embedding", {}).get("values") or []
            if values:
                track_usage_in_db(bu_id, max(1, len(input_text) // 4))
                return jsonify(
                    {
                        "data": [{"embedding": values, "index": 0}],
                        "model": "text-embedding-004",
                    }
                )

        fallback = _fallback_embedding(input_text)
        return jsonify(
            {
                "data": [{"embedding": fallback, "index": 0}],
                "model": "local-fallback-embedding",
            }
        )
    except Exception as e:
        logger.warning("Embedding provider failed, using local fallback: %s", e)
        fallback = _fallback_embedding(input_text)
        return jsonify(
            {
                "data": [{"embedding": fallback, "index": 0}],
                "model": "local-fallback-embedding",
            }
        )

if __name__ == "__main__":
    app.run(host="0.0.0.0", port=int(os.getenv("PROXY_PORT", 5100)))
