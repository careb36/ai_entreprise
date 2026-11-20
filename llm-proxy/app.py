"""
LLM Proxy - Enterprise Multi-Model Gateway v2.0 (Phase 4)

Governance-ready proxy that routes requests to Gemini, DeepSeek, Qwen or
Nebius with:
- **Distributed Tracing**: OpenTelemetry instrumentation (``telemetry.py``).
- **Explicit Model Routing**: a model id picks its provider by prefix and must
  be in that provider's catalogue (refreshed hourly); anything else is a 400
  ``unknown_model`` (``routing.py``, ``catalogue.py``).  Responses carry
  ``served_model``.
- **Quota Enforcement**: Persistent token tracking per Business Unit via
  PostgreSQL (``quota.py``).
- **Provider status**: what each provider answered, observed at startup, on
  ``GET /v1/providers?refresh=1`` and on every request (``provider_status.py``,
  ``provider_keys.py``), served by ``/v1/providers`` and ``/health``.

This module is the entry point gunicorn starts (``app:app``). It holds the
configuration, the live catalogues and their refresh, and the HTTP routes; the
other modules hold the logic and receive this state as arguments at call time,
so a test that replaces a key, a catalogue set or a refresh here is seen by
them, and ``track_usage_in_db`` is only called from the routes below.
"""

import logging
import os
import threading
import time

import catalogue
import connections
import embedding
import provider_keys
import provider_status
import providers
import requests  # noqa: F401 - tests replace requests.post through this module
import routing
import telemetry
import thinking
import timeouts
import upstream
from flask import Flask, jsonify, request
from quota import (
    add_quota_warning_header,
    enforce_business_unit_quota,
    track_usage_in_db,
)

app = Flask(__name__)
endpoint_base = telemetry.instrument(app)

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
# Upper bound for one chat request's provider calls, fallbacks and the retry
# without thinking included (300 s by default, ``timeouts.py``).  gunicorn's
# worker timeout and the backend client's wait are this plus a margin, so the
# proxy answers (with a named ``upstream_timeout`` if need be) first.
LLM_UPSTREAM_TIMEOUT_SECONDS = timeouts.upstream_timeout_seconds()
# The other providers a failed request may move to, in order (issue #751).
# Empty, the default: a request stays with its provider (DeepSeek Pro <-> Flash).
LLM_FALLBACK_PROVIDERS = routing.parse_fallback_providers(os.getenv("LLM_FALLBACK_PROVIDERS", ""))

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger("llm-proxy")
if endpoint_base:
    logger.info("OpenTelemetry exporter enabled for llm-proxy: %s/v1/traces", endpoint_base)
else:
    logger.info("OpenTelemetry exporter disabled for llm-proxy")


def _configured_key(provider: str) -> str:
    """The key as configured, placeholder included (only the key check reads this)."""
    return {"gemini": GEMINI_API_KEY, "deepseek": DEEPSEEK_API_KEY, "qwen": QWEN_API_KEY,
            "nebius": NEBIUS_API_KEY}.get(provider, "")


def _provider_key(provider: str) -> str:
    """The key to send, or "" when none is set or it is an ``.env.example`` placeholder."""
    key = _configured_key(provider).strip()
    return "" if provider_status.is_placeholder(key) else key


def _provider_url(provider: str) -> str:
    return {"gemini": GEMINI_PROXY_URL, "deepseek": DEEPSEEK_PROXY_URL, "qwen": QWEN_PROXY_URL,
            "nebius": NEBIUS_PROXY_URL}.get(provider, "")


# --- Provider status ---
# The startup check's timeout, and the on-demand one's (shorter: a person waits).
_STARTUP_CHECK_TIMEOUT_SECONDS = 10.0
_REFRESH_CHECK_TIMEOUT_SECONDS = 5.0
# Each on-demand check makes a one-token call per provider: one per 30 s at most.
_REFRESH_MIN_INTERVAL_SECONDS = 30.0
_provider_status = provider_status.Board(provider_status.PROVIDERS)
_refresh_limiter = provider_status.RefreshLimiter(_REFRESH_MIN_INTERVAL_SECONDS)


def _check_providers(source: str, timeout: float) -> None:
    """Ask every provider whether its key works and record what each answered.

    The proxy starts whatever the outcome: another provider may serve.
    """
    names = provider_status.PROVIDERS
    seen = provider_keys.check_all({p: _configured_key(p) for p in names},
                                   {p: _provider_url(p) for p in names}, timeout)
    for provider, obs in seen.items():
        _provider_status.record(provider, obs["status"], http_status=obs.get("http_status"),
                                source=source, checked=obs.get("checked"), detail=obs.get("detail"))


_check_providers("startup", _STARTUP_CHECK_TIMEOUT_SECONDS)

# --- Dynamic Model Catalogue ---
# The fetched ids per provider, replaced whole by each refresh (tests set them here).
_available_models: set[str] = set()
_available_deepseek_models: set[str] = set()
_available_qwen_models: set[str] = set()
_available_nebius_models: set[str] = set()
_model_refresh_lock = threading.Lock()
_last_refresh: float = 0
_deepseek_last_refresh: float = 0
_qwen_last_refresh: float = 0
_nebius_last_refresh: float = 0


def _refresh_gemini_models():
    global _available_models, _last_refresh
    _available_models, _last_refresh = catalogue.refresh(
        "gemini", _provider_key("gemini"), GEMINI_PROXY_URL, _available_models, _last_refresh)


def _refresh_deepseek_models():
    global _available_deepseek_models, _deepseek_last_refresh
    _available_deepseek_models, _deepseek_last_refresh = catalogue.refresh(
        "deepseek", _provider_key("deepseek"), DEEPSEEK_PROXY_URL,
        _available_deepseek_models, _deepseek_last_refresh)


def _refresh_qwen_models():
    global _available_qwen_models, _qwen_last_refresh
    _available_qwen_models, _qwen_last_refresh = catalogue.refresh(
        "qwen", _provider_key("qwen"), QWEN_PROXY_URL, _available_qwen_models, _qwen_last_refresh)


def _refresh_nebius_models():
    global _available_nebius_models, _nebius_last_refresh
    _available_nebius_models, _nebius_last_refresh = catalogue.refresh(
        "nebius", _provider_key("nebius"), NEBIUS_PROXY_URL,
        _available_nebius_models, _nebius_last_refresh)


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
    return {mid for mid in ids if catalogue.provider_for_model(mid) == provider}


provider_for_model = catalogue.provider_for_model


def available_model_ids() -> list[str]:
    """Every model id the chat endpoint routes, as ``/v1/models`` lists them."""
    return routing.available_ids(_provider_catalogue)


def resolve_model(requested: str) -> tuple[str, str] | None:
    """``(provider, model_id)`` for *requested*, or ``None`` (``routing.resolve``)."""
    return routing.resolve(requested, _provider_catalogue)


def _fallback_targets(failed_provider: str, failed_model: str = "") -> list[tuple[str, str]]:
    """Catalogue models to try after a 429/5xx (``routing.fallback_targets``)."""
    return routing.fallback_targets(failed_provider, _provider_catalogue, failed_model=failed_model,
                                    cross_providers=LLM_FALLBACK_PROVIDERS)


def _capabilities() -> dict:
    """What this proxy can do with the configuration it has: names and reasons, never a key."""
    return {
        "providers_configured": [p for p in provider_status.PROVIDERS if _provider_key(p)],
        "embeddings": embedding.capability(_provider_key("gemini")),
        "failover": routing.failover_scope(LLM_FALLBACK_PROVIDERS),
    }


logger.info("Effective capabilities: %s", _capabilities())


def _call_provider(provider: str, model: str, data: dict, timeout: float,
                   structured: bool = False) -> tuple[dict, int]:
    # The key and URL are read here, at call time, so a test can replace them.
    key = _provider_key(provider)
    try:
        answer = providers.call_provider(
            provider, model, data, timeout, api_key=key, base_url=_provider_url(provider), structured=structured)
    except upstream.UpstreamError as exc:
        # A refusal that names the account is recorded; the error travels on
        # unchanged, so the fallback decides exactly as before.
        status = provider_status.classify(exc.status, exc.message)
        if exc.status is not None and status in ("no_credit", "invalid_key"):
            _provider_status.record(provider, status, http_status=exc.status, source="request",
                                    checked="key_and_credit", detail=provider_status.redact(exc.message, key))
        raise
    _provider_status.record(provider, "ok", http_status=None, source="request", checked="key_and_credit")
    return answer


def _complete_with_fallback(provider: str, model: str, data: dict,
                            structured: bool = False) -> tuple[dict, int, str, str, dict | None]:
    """The resolved model's answer, or a fallback's (``routing.complete_with_fallback``)."""
    def call(p: str, m: str, d: dict, t: float) -> tuple[dict, int]:
        return _call_provider(p, m, d, t, structured=structured)

    return routing.complete_with_fallback(
        provider, model, data, call=call, targets=_fallback_targets, budget=LLM_UPSTREAM_TIMEOUT_SECONDS)


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
app.before_request(enforce_business_unit_quota)
app.after_request(add_quota_warning_header)


# --- API Endpoints ---

@app.route("/health")
def health():
    return jsonify({"status": "healthy", "database": "connected", "models": sorted(_available_models),
                    "providers": _provider_status.snapshot(), "capabilities": _capabilities()})


@app.route("/v1/providers")
def list_providers():
    """What the proxy last observed of each provider, never its key.

    ``?refresh=1`` asks every provider again (5 s timeout each, in parallel),
    at most once every 30 s; a refused refresh answers the last observations
    with ``refresh: rate_limited`` and ``retry_after_seconds``.
    """
    body: dict = {}
    if request.args.get("refresh") in ("1", "true", "yes"):
        allowed, retry_after = _refresh_limiter.try_acquire()
        if allowed:
            _check_providers("refresh", _REFRESH_CHECK_TIMEOUT_SECONDS)
            body["refresh"] = "done"
        else:
            body.update(refresh="rate_limited", retry_after_seconds=retry_after)
    body["providers"] = _provider_status.snapshot()
    return jsonify(body)


@app.route("/v1/models")
def list_models():
    """
    Return a structured catalogue of all available models with tier classification.

    Response schema:
      {
        "models": [
          {
            "id":          "deepseek-v4-pro",
            "provider":    "gemini" | "deepseek" | "qwen" | "nebius",
            "tier":        "strategic" | "balanced" | "code" | "fast" | "preview",
            "description": "..."
          },
          ...
        ],
        "orchestrator": "deepseek-flash",   // recommended model for meta-tasks
        "refreshed_at": 1712345678.0
      }
    """
    # Only ids the chat endpoint routes are listed.
    keys = {p: _provider_key(p) for p in ("deepseek", "qwen", "nebius")}
    models = catalogue.listed_models(_provider_catalogue, keys)
    # DeepSeek Flash, the default team's model for meta-tasks.
    orchestrator = catalogue.pick_orchestrator(models)
    return jsonify({"models": models, "orchestrator": orchestrator, "refreshed_at": _last_refresh})


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

    try:
        conn = connections.from_headers(request.headers)
    except connections.ConnectionRefused as exc:
        return jsonify({"error": "connection_refused", "message": str(exc)}), 403
    resolved = ("connection", requested) if conn else resolve_model(requested)
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
    # A structured call's answer is parsed: one whose reasoning ate the budget
    # is retried with thinking off (``thinking.py``).
    structured = request.headers.get("X-LLM-Purpose", "").strip().lower() == thinking.STRUCTURED
    try:
        if conn:  # The company's own key: no fallback to the deployment's providers.
            result, tokens = connections.call(conn, model, data, upstream.post_upstream, LLM_UPSTREAM_TIMEOUT_SECONDS)
            served_provider, served_model, fallback_from = f"connection:{conn['name']}", model, None
        else:
            result, tokens, served_provider, served_model, fallback_from = _complete_with_fallback(
                provider, model, data, structured)
    except upstream.UpstreamError as exc:
        track_usage_in_db(bu_id, upstream.failed_tokens(exc))
        return jsonify(upstream.error_body(exc, requested)), 429 if exc.status == 429 else 502
    except Exception as exc:  # noqa: BLE001 - surface proxy bugs as JSON, not an HTML page
        logger.exception("chat_completions failed")
        return jsonify({"error": "proxy_error", "message": str(exc)}), 500

    result["served_model"] = served_model
    result["provider"] = served_provider
    result["requested_model"] = requested
    if fallback_from is not None:
        result["fallback_from"] = fallback_from
    track_usage_in_db(bu_id, tokens + upstream.failed_tokens(fallback_from))
    return jsonify(result)


@app.route("/v1/embeddings", methods=["POST"])
def embeddings():
    data = request.json or {}
    text = embedding.input_text(data)
    bu_id = request.headers.get("X-Business-Unit-ID")

    if not text.strip():
        return jsonify({"error": "input is required"}), 400

    body, tokens = embedding.embed(text, data, api_key=embedding.key_for(_provider_key("gemini")),
                                   base_url=GEMINI_PROXY_URL)
    if tokens:
        track_usage_in_db(bu_id, tokens)
    return jsonify(body)


if __name__ == "__main__":
    app.run(host="0.0.0.0", port=int(os.getenv("PROXY_PORT", 5100)))
