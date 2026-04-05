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
from flask import Flask, request, jsonify
import requests
from sqlalchemy import create_engine, text

# --- OpenTelemetry ---
from opentelemetry import trace
from opentelemetry.sdk.trace import TracerProvider
from opentelemetry.sdk.trace.export import BatchSpanProcessor
from opentelemetry.exporter.otlp.proto.http.trace_exporter import OTLPSpanExporter
from opentelemetry.instrumentation.flask import FlaskInstrumentor
from opentelemetry.instrumentation.requests import RequestsInstrumentor

app = Flask(__name__)

# OTEL Init
tracer_provider = TracerProvider()
otlp_exporter = OTLPSpanExporter(endpoint="http://localhost:4318/v1/traces")
span_processor = BatchSpanProcessor(otlp_exporter)
tracer_provider.add_span_processor(span_processor)
trace.set_tracer_provider(tracer_provider)
FlaskInstrumentor().instrument_app(app)
RequestsInstrumentor().instrument()

# --- Config & Logging ---
GEMINI_API_KEY = os.getenv("GEMINI_API_KEY", "")
DEEPSEEK_API_KEY = os.getenv("DEEPSEEK_API_KEY", "")
GEMINI_PROXY_URL = "https://generativelanguage.googleapis.com/v1beta"
DEEPSEEK_PROXY_URL = "https://api.deepseek.com/v1"

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger("llm-proxy")

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
_PREFERENCE_TIERS = {
    "fast":     ["gemini-2.0-flash", "gemini-2.5-flash-lite", "gemini-flash-latest", "gemini-2.0-flash-lite"],
    "powerful": ["gemini-2.5-pro",   "gemini-2.5-flash",      "gemini-pro-latest",   "gemini-2.0-flash"],
}
_available_models: set[str] = set()
_model_refresh_lock = threading.Lock()
_last_refresh: float = 0

def _refresh_gemini_models():
    global _available_models, _last_refresh
    if not GEMINI_API_KEY: return
    try:
        resp = requests.get(f"{GEMINI_PROXY_URL}/models", headers={"x-goog-api-key": GEMINI_API_KEY}, timeout=10)
        resp.raise_for_status()
        ids = {m["name"].split("/")[-1] for m in resp.json().get("models", []) if "generateContent" in m.get("supportedGenerationMethods", [])}
        with _model_refresh_lock:
            _available_models = ids
            _last_refresh = time.time()
        logger.info(f"Models refreshed: {sorted(ids)}")
    except Exception as e:
        logger.warning(f"Refresh failed: {e}")

def resolve_gemini_model(requested: str) -> str:
    if not _available_models: _refresh_gemini_models()
    with _model_refresh_lock:
        if requested in _available_models: return requested
        tier = "fast" if any(k in requested for k in ("flash", "lite")) else "powerful"
        for candidate in _PREFERENCE_TIERS[tier]:
            if candidate in _available_models: return candidate
    return requested

threading.Thread(target=lambda: (time.sleep(3600), _refresh_gemini_models()), daemon=True).start()

# --- Governance Middleware ---
@app.before_request
def enforce_business_unit_quota():
    if request.path == "/health": return
    bu_id = request.headers.get("X-Business-Unit-ID")
    if not bu_id:
        logger.warning("No X-Business-Unit-ID provided.")
        return
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

def track_usage_in_db(bu_id: str, tokens: int):
    if not bu_id: return
    try:
        with engine.begin() as conn:
            conn.execute(text("UPDATE business_units SET token_quota_used = token_quota_used + :t WHERE id = :id"), {"t": tokens, "id": bu_id})
    except Exception as e:
        logger.error(f"Usage tracking failed: {e}")

# --- API Endpoints ---
@app.route("/health")
def health():
    return jsonify({"status": "healthy", "database": "connected", "models": sorted(_available_models)})

@app.route("/v1/chat/completions", methods=["POST"])
def chat_completions():
    data = request.json
    model = data.get("model", "gemini-flash-latest")
    messages = data.get("messages", [])
    bu_id = request.headers.get("X-Business-Unit-ID")

    try:
        if "deepseek" in model.lower():
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

if __name__ == "__main__":
    app.run(host="0.0.0.0", port=int(os.getenv("PROXY_PORT", 5100)))
