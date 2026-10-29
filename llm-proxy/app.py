"""
LLM Proxy - Enterprise Multi-Model Gateway v2.0 (Phase 4)

Governance-ready proxy that routes requests to Gemini, DeepSeek or Qwen with:
- **Distributed Tracing**: OpenTelemetry instrumentation.
- **Explicit Model Routing**: a model id picks its provider by prefix and must
  be in that provider's catalogue (refreshed hourly); anything else is a 400
  ``unknown_model``.  Responses carry ``served_model``.
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
NEBIUS_API_KEY = os.getenv("NEBIUS_API_KEY", "")
GEMINI_PROXY_URL = "https://generativelanguage.googleapis.com/v1beta"
DEEPSEEK_PROXY_URL = "https://api.deepseek.com/v1"
QWEN_PROXY_URL = "https://token-plan.maas.qwencloudapi.com/compatible-mode/v1"
# Nebius Token Factory serves the NVIDIA open models (Nemotron) behind an
# OpenAI-compatible API.  Overridable so a self-hosted Serverless Endpoint can
# be pointed at without touching code.
NEBIUS_PROXY_URL = os.getenv("NEBIUS_PROXY_URL", "https://api.tokenfactory.nebius.com/v1")
# Upper bound for one chat request's provider calls, fallbacks included.  It
# stays below the backend task client's 120 s timeout so the proxy answers
# (with an error if need be) before the caller gives up on it.
LLM_UPSTREAM_TIMEOUT_SECONDS = float(os.getenv("LLM_UPSTREAM_TIMEOUT_SECONDS", "110"))

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
                json={"model": "deepseek-flash", "messages": [{"role": "user", "content": "ping"}], "max_tokens": 1},
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


    if GEMINI_API_KEY:
        try:
            resp = requests.get(
                f"{GEMINI_PROXY_URL}/models",
                headers={"x-goog-api-key": GEMINI_API_KEY},
                timeout=10,
            )
            if resp.status_code in (401, 403):
                errors.append("GEMINI_API_KEY is invalid")
            else:
                logger.info("Gemini API key validated successfully")
        except Exception as e:
            logger.warning(f"Could not validate Gemini API key: {e}")
    else:
        logger.warning("GEMINI_API_KEY is not set")

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

    if NEBIUS_API_KEY:
        try:
            resp = requests.get(
                f"{NEBIUS_PROXY_URL}/models",
                headers={"Authorization": f"Bearer {NEBIUS_API_KEY}"},
                timeout=10,
            )
            if resp.status_code in (401, 403):
                errors.append("NEBIUS_API_KEY is invalid")
            else:
                logger.info("Nebius Token Factory API key validated successfully")
        except Exception as e:
            logger.warning(f"Could not validate Nebius API key: {e}")
    else:
        logger.warning("NEBIUS_API_KEY is not set")

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
    "fast":     ["gemini-3.8-flash", "gemini-flash-latest", "gemini-3.1-flash-lite",
                 "gemini-2.5-flash-lite", "gemini-2.0-flash-lite"],
    "powerful": ["gemini-3.8-flash", "gemini-3.1-pro-preview", "gemini-pro-latest",
                 "gemini-2.5-pro"],
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
_available_deepseek_models: set[str] = set()
_available_qwen_models: set[str] = set()
_available_nebius_models: set[str] = set()
_model_refresh_lock = threading.Lock()
_last_refresh: float = 0
_deepseek_last_refresh: float = 0
_qwen_last_refresh: float = 0
_nebius_last_refresh: float = 0


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



def _refresh_deepseek_models():
    global _available_deepseek_models, _deepseek_last_refresh
    if not DEEPSEEK_API_KEY:
        return
    try:
        resp = requests.get(
            f"{DEEPSEEK_PROXY_URL}/models",
            headers={"Authorization": f"Bearer {DEEPSEEK_API_KEY}"},
            timeout=10,
        )
        resp.raise_for_status()
        ids = {m.get("id") for m in resp.json().get("data", []) if m.get("id")}
        with _model_refresh_lock:
            _available_deepseek_models = ids
            _deepseek_last_refresh = time.time()
        logger.info(f"DeepSeek models refreshed: {sorted(ids)}")
    except Exception as e:
        logger.warning(f"DeepSeek model refresh failed: {e}")


def _refresh_qwen_models():
    global _available_qwen_models, _qwen_last_refresh
    if not QWEN_API_KEY:
        return
    try:
        resp = requests.get(
            f"{QWEN_PROXY_URL}/models",
            headers={"Authorization": f"Bearer {QWEN_API_KEY}"},
            timeout=10,
        )
        resp.raise_for_status()
        # Keep only chat-capable LLMs (exclude image/audio/video/tts/realtime/wan).
        skip = ("image", "audio", "video", "tts", "realtime", "wan", "embedding")
        ids = {
            m.get("id")
            for m in resp.json().get("data", [])
            if m.get("id") and not any(k in m.get("id", "").lower() for k in skip)
        }
        with _model_refresh_lock:
            _available_qwen_models = ids
            _qwen_last_refresh = time.time()
        logger.info(f"Qwen models refreshed: {sorted(ids)}")
    except Exception as e:
        logger.warning(f"Qwen model refresh failed: {e}")


def _refresh_nebius_models():
    """Fetch the Token Factory catalogue: the served NVIDIA open models."""
    global _available_nebius_models, _nebius_last_refresh
    if not NEBIUS_API_KEY:
        return
    try:
        resp = requests.get(
            f"{NEBIUS_PROXY_URL}/models",
            headers={"Authorization": f"Bearer {NEBIUS_API_KEY}"},
            timeout=10,
        )
        resp.raise_for_status()
        skip = ("embedding", "rerank")
        ids = {
            m.get("id")
            for m in resp.json().get("data", [])
            if m.get("id") and not any(k in m.get("id", "").lower() for k in skip)
        }
        with _model_refresh_lock:
            _available_nebius_models = ids
            _nebius_last_refresh = time.time()
        logger.info(f"Nebius models refreshed: {sorted(ids)}")
    except Exception as e:
        logger.warning(f"Nebius model refresh failed: {e}")


# Deprecated Gemini ids mapped to their working equivalents.
_GEMINI_ALIASES = {
    "gemini-2.5-flash": "gemini-3.8-flash",
    "gemini-2.5-pro": "gemini-3.1-pro-preview",
    "gemini-2.0-flash": "gemini-3.8-flash",
}

# Models that are no longer available to new users (Google deprecation).
_GEMINI_DEPRECATED = {"gemini-2.5-flash", "gemini-2.5-pro", "gemini-2.0-flash", "gemini-2.0-flash-lite"}

# A model id names its provider by prefix.  The id must also be in that
# provider's fetched catalogue before a request is sent anywhere.
# Nebius ids carry a vendor segment ("nvidia/…", "meta-llama/…"), hence the
# vendor/ prefix rather than a bare model-name prefix.
_PROVIDER_PREFIXES: dict[str, tuple[str, ...]] = {
    "gemini": ("gemini-", "gemma-"),
    "deepseek": ("deepseek-",),
    "qwen": ("qwen", "qwq-", "qvq-"),
    "nebius": ("nvidia/", "meta-llama/", "mistralai/", "deepseek-ai/", "qwen/", "google/"),
}

# Where a request goes when its provider answers 429 or 5xx, in order.  Each
# candidate is used only if it is in that provider's catalogue.
_FALLBACK_CANDIDATES: list[tuple[str, list[str]]] = [
    ("deepseek", ["deepseek-flash"]),
    ("qwen", ["qwen3.6-flash"]),
    ("gemini", _PREFERENCE_TIERS["fast"]),
]


def provider_for_model(model_id: str) -> str | None:
    """The provider whose prefix *model_id* carries, or ``None``."""
    lower = (model_id or "").strip().lower()
    for provider, prefixes in _PROVIDER_PREFIXES.items():
        if lower.startswith(prefixes):
            return provider
    return None


def _provider_key(provider: str) -> str:
    return {"gemini": GEMINI_API_KEY, "deepseek": DEEPSEEK_API_KEY, "qwen": QWEN_API_KEY,
            "nebius": NEBIUS_API_KEY}.get(provider, "")


def _provider_catalogue(provider: str) -> set[str]:
    """The provider's fetched model ids that route to it, refreshing once if empty."""
    sources = {
        "gemini": (_refresh_gemini_models, lambda: _available_models),
        "deepseek": (_refresh_deepseek_models, lambda: _available_deepseek_models),
        "qwen": (_refresh_qwen_models, lambda: _available_qwen_models),
        "nebius": (_refresh_nebius_models, lambda: _available_nebius_models),
    }
    if provider not in sources:
        return set()
    refresh, current = sources[provider]
    if not current() and _provider_key(provider):
        refresh()
    with _model_refresh_lock:
        ids = set(current())
    return {mid for mid in ids if provider_for_model(mid) == provider}


def available_model_ids() -> list[str]:
    """Every model id the chat endpoint routes, as ``/v1/models`` lists them."""
    ids: set[str] = set()
    for provider in _PROVIDER_PREFIXES:
        catalogue = _provider_catalogue(provider)
        if provider == "gemini":
            catalogue -= _GEMINI_DEPRECATED
        ids |= catalogue
    return sorted(ids)


def resolve_model(requested: str) -> tuple[str, str] | None:
    """
    Return ``(provider, model_id)`` for *requested*, or ``None`` when no
    provider serves it.  A deprecated Gemini id is aliased to its replacement
    when the replacement is in the catalogue.  Nothing else is substituted:
    an id that is not in its provider's catalogue is unknown.
    """
    provider = provider_for_model(requested)
    if provider is None:
        return None
    catalogue = _provider_catalogue(provider)
    if provider == "gemini":
        alias = _GEMINI_ALIASES.get(requested)
        if alias and alias in catalogue:
            return provider, alias
    if requested in catalogue:
        return provider, requested
    return None


def _fallback_targets(failed_provider: str) -> list[tuple[str, str]]:
    """Catalogue models to try, one per other provider, after a 429/5xx."""
    targets = []
    for provider, candidates in _FALLBACK_CANDIDATES:
        if provider == failed_provider:
            continue
        catalogue = _provider_catalogue(provider)
        for candidate in candidates:
            if candidate in catalogue:
                targets.append((provider, candidate))
                break
    return targets


# Refresh models once at startup; background thread refreshes every hour.
_refresh_gemini_models()
_refresh_deepseek_models()
_refresh_qwen_models()
_refresh_nebius_models()
threading.Thread(target=lambda: (time.sleep(3600), _refresh_gemini_models()), daemon=True).start()
threading.Thread(target=lambda: (time.sleep(3600), _refresh_deepseek_models()), daemon=True).start()
threading.Thread(target=lambda: (time.sleep(3600), _refresh_qwen_models()), daemon=True).start()
threading.Thread(target=lambda: (time.sleep(3600), _refresh_nebius_models()), daemon=True).start()

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
            "provider":    "gemini" | "deepseek" | "qwen" | "nebius",
            "tier":        "strategic" | "balanced" | "code" | "fast" | "preview",
            "description": "..."
          },
          ...
        ],
        "orchestrator": "gemini-2.5-flash",   // recommended model for meta-tasks
        "refreshed_at": 1712345678.0
      }
    """
    # Only ids the chat endpoint routes are listed.
    models = []
    for model_id in sorted(_provider_catalogue("gemini") - _GEMINI_DEPRECATED):
        tier, desc = _classify_model(model_id)
        models.append({"id": model_id, "provider": "gemini", "tier": tier, "description": desc})

    # DeepSeek models are available only when the API key is configured.
    if DEEPSEEK_API_KEY:
        ds_ids = sorted(_provider_catalogue("deepseek"))
        for mid in ds_ids:
            tier = "fast" if "flash" in mid else "balanced"
            models.append({"id": mid, "provider": "deepseek", "tier": tier,
                           "description": f"DeepSeek {mid}"})

    # Qwen models are available only when the API key is configured.
    if QWEN_API_KEY:
        q_ids = sorted(_provider_catalogue("qwen"))
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

    # Nebius models host the NVIDIA open models (Nemotron) and are available
    # only when the API key is configured.
    if NEBIUS_API_KEY:
        for mid in sorted(_provider_catalogue("nebius")):
            lower = mid.lower()
            if "nano" in lower:
                tier = "fast"
            elif "ultra" in lower or "253b" in lower or "340b" in lower:
                tier = "strategic"
            else:
                tier = "balanced"
            models.append({"id": mid, "provider": "nebius", "tier": tier,
                           "description": f"NVIDIA/Nebius {mid}"})

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
        "gemini-3.8-flash", "gemini-flash-latest", "gemini-3.1-flash-lite",
        "gemini-2.5-flash", "gemini-2.5-pro",
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


class UpstreamError(Exception):
    """A provider call that produced no usable answer."""

    def __init__(
        self,
        provider: str,
        model: str,
        status: int | None,
        message: str,
        retriable: bool | None = None,
    ):
        super().__init__(f"{provider} {status} {message}")
        self.provider = provider
        self.model = model
        self.status = status
        self.message = message
        self.attempts: list[dict] = []
        # 429 and 5xx may succeed elsewhere; so may a refused connection.
        if retriable is None:
            retriable = status is None or status == 429 or status >= 500
        self.retriable = retriable

    def as_dict(self) -> dict:
        return {"provider": self.provider, "model": self.model, "status": self.status, "message": self.message}


def _upstream_message(resp) -> str:
    """The provider's own error text, or the start of the body."""
    try:
        body = resp.json()
    except ValueError:
        body = None
    if isinstance(body, dict):
        err = body.get("error")
        if isinstance(err, dict) and err.get("message"):
            return str(err["message"])
        if isinstance(err, str) and err:
            return err
        if body.get("message"):
            return str(body["message"])
    text_body = (getattr(resp, "text", "") or "").strip()
    return text_body[:300] or f"HTTP {resp.status_code}"


def _post_upstream(provider: str, model: str, url: str, headers: dict, payload: dict, timeout: float):
    """POST to a provider; any non-2xx or unreadable body becomes an ``UpstreamError``."""
    try:
        resp = requests.post(url, headers=headers, json=payload, timeout=timeout)
    except requests.Timeout:
        # Not retried elsewhere: the time budget is already spent.
        raise UpstreamError(provider, model, None, f"timed out after {timeout:.0f}s", retriable=False)
    except requests.RequestException as exc:
        raise UpstreamError(provider, model, None, f"request failed: {exc}")
    if not 200 <= resp.status_code < 300:
        raise UpstreamError(provider, model, resp.status_code, _upstream_message(resp))
    try:
        body = resp.json()
    except ValueError:
        raise UpstreamError(provider, model, resp.status_code, "response body is not JSON", retriable=False)
    if not isinstance(body, dict):
        raise UpstreamError(provider, model, resp.status_code, "response body is not a JSON object", retriable=False)
    if body.get("error"):
        raise UpstreamError(provider, model, resp.status_code, _upstream_message(resp), retriable=False)
    return resp.status_code, body


_OPENAI_COMPATIBLE_BASE_URLS = {
    "deepseek": DEEPSEEK_PROXY_URL,
    "qwen": QWEN_PROXY_URL,
    "nebius": NEBIUS_PROXY_URL,
}


def _call_openai_compatible(provider: str, model: str, data: dict, timeout: float) -> tuple[dict, int]:
    base_url = _OPENAI_COMPATIBLE_BASE_URLS[provider]
    status, result = _post_upstream(
        provider,
        model,
        f"{base_url}/chat/completions",
        {"Authorization": f"Bearer {_provider_key(provider)}"},
        {**data, "model": model},
        timeout,
    )
    if not result.get("choices"):
        raise UpstreamError(provider, model, status, "response contained no choices", retriable=False)
    tokens = (result.get("usage") or {}).get("total_tokens", 0)
    return result, tokens


def _call_gemini(model: str, data: dict, timeout: float) -> tuple[dict, int]:
    messages = data.get("messages", [])
    contents = [
        {"role": "model" if m["role"] == "assistant" else "user", "parts": [{"text": m["content"]}]}
        for m in messages
        if m["role"] != "system"
    ]
    system = next((m["content"] for m in messages if m["role"] == "system"), None)
    payload = {"contents": contents, "generationConfig": {"temperature": data.get("temperature", 0.7)}}
    if system:
        payload["system_instruction"] = {"parts": [{"text": system}]}
    status, res = _post_upstream(
        "gemini",
        model,
        f"{GEMINI_PROXY_URL}/models/{model}:generateContent",
        {"x-goog-api-key": GEMINI_API_KEY},
        payload,
        timeout,
    )
    try:
        text_out = res["candidates"][0]["content"]["parts"][0]["text"]
    except (KeyError, IndexError, TypeError):
        reason = (res.get("promptFeedback") or {}).get("blockReason") or "no candidates"
        raise UpstreamError("gemini", model, status, f"response had no text ({reason})", retriable=False)
    tokens = (len(str(messages)) + len(text_out)) // 4
    result = {
        "choices": [{"message": {"role": "assistant", "content": text_out}}],
        "usage": {"total_tokens": tokens},
        "model": model,
    }
    return result, tokens


def _call_provider(provider: str, model: str, data: dict, timeout: float) -> tuple[dict, int]:
    if provider == "gemini":
        return _call_gemini(model, data, timeout)
    return _call_openai_compatible(provider, model, data, timeout)


def _complete_with_fallback(provider: str, model: str, data: dict) -> tuple[dict, int, str, str, dict | None]:
    """
    Call the resolved model; on 429/5xx try one catalogue model per other
    provider within the same time budget.  Returns
    ``(result, tokens, served_provider, served_model, fallback_from)`` or
    raises the primary ``UpstreamError`` with every attempt attached.
    """
    deadline = time.monotonic() + LLM_UPSTREAM_TIMEOUT_SECONDS
    try:
        result, tokens = _call_provider(provider, model, data, LLM_UPSTREAM_TIMEOUT_SECONDS)
        return result, tokens, provider, model, None
    except UpstreamError as primary:
        if not primary.retriable:
            raise
        logger.warning("%s; trying fallback models from the catalogue", primary)
        primary.attempts = [primary.as_dict()]
        for fb_provider, fb_model in _fallback_targets(provider):
            remaining = deadline - time.monotonic()
            if remaining < 5:
                break
            try:
                result, tokens = _call_provider(fb_provider, fb_model, data, remaining)
            except UpstreamError as exc:
                logger.warning("Fallback failed: %s", exc)
                primary.attempts.append(exc.as_dict())
                if not exc.retriable:
                    break
                continue
            return result, tokens, fb_provider, fb_model, primary.as_dict()
        raise primary


@app.route("/v1/chat/completions", methods=["POST"])
def chat_completions():
    data = request.get_json(silent=True) or {}
    requested = str(data.get("model") or "").strip()
    bu_id = request.headers.get("X-Business-Unit-ID")

    if not requested:
        return jsonify({
            "error": "model_required",
            "model": "",
            "message": "request named no model",
            "available": available_model_ids(),
        }), 400

    resolved = resolve_model(requested)
    if resolved is None:
        available = available_model_ids()
        return jsonify({
            "error": "unknown_model",
            "model": requested,
            "provider": provider_for_model(requested),
            "message": f"unknown model '{requested}'; available: {', '.join(available) or 'none'}",
            "available": available,
        }), 400

    provider, model = resolved
    try:
        result, tokens, served_provider, served_model, fallback_from = _complete_with_fallback(provider, model, data)
    except UpstreamError as exc:
        body = {"error": "upstream_error", **exc.as_dict(), "requested_model": requested}
        if len(exc.attempts) > 1:
            body["attempts"] = exc.attempts
        return jsonify(body), 429 if exc.status == 429 else 502
    except Exception as exc:  # noqa: BLE001 - surface proxy bugs as JSON, not an HTML page
        logger.exception("chat_completions failed")
        return jsonify({"error": "proxy_error", "message": str(exc)}), 500

    result["served_model"] = served_model
    result["provider"] = served_provider
    result["requested_model"] = requested
    if fallback_from is not None:
        result["fallback_from"] = fallback_from
    track_usage_in_db(bu_id, tokens)
    return jsonify(result)


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
