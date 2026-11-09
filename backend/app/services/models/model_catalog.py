"""The model catalogue: which providers and models exist, and what each can do.

Source: models.dev (https://models.dev/api.json, MIT), the catalogue opencode
reads. For each provider it gives the name, the docs and the API's base URL;
for each model, whether it calls tools, reasons and returns structured output,
its context and output limits and its price per million tokens.

La Fabrique keeps the providers it can call through one OpenAI-compatible
client: those models.dev marks ``@ai-sdk/openai-compatible`` with an ``api``
URL, plus the ones in ``BASE_URL_OVERRIDES`` whose native SDK has an
OpenAI-compatible endpoint (OpenAI, Anthropic, Google, Groq, ...).

The backend starts from the snapshot in ``app/data/models_dev.json``
(``scripts/update_models_snapshot.py`` refreshes it) and, when
``MODELS_CATALOG_REFRESH_HOURS`` is set, refreshes from ``MODELS_CATALOG_URL``
in the background; a failed refresh keeps what it had. The catalogue is a
description of the providers, not evidence of what a provider serves: what a
connection actually lists is its validation (``provider_connections``).
"""

from __future__ import annotations

import json
import logging
import threading
import time
from pathlib import Path
from typing import Any

import httpx

logger = logging.getLogger(__name__)

SNAPSHOT_PATH = Path(__file__).resolve().parents[2] / "data" / "models_dev.json"
OPENAI_COMPATIBLE_NPM = "@ai-sdk/openai-compatible"
FETCH_TIMEOUT_SECONDS = 30.0

# OpenAI-compatible endpoints of providers models.dev lists with a native SDK
# (no ``api`` URL), and corrections where its URL lacks the version path.
BASE_URL_OVERRIDES: dict[str, str] = {
    "openai": "https://api.openai.com/v1",
    "anthropic": "https://api.anthropic.com/v1",
    "google": "https://generativelanguage.googleapis.com/v1beta/openai",
    "groq": "https://api.groq.com/openai/v1",
    "mistral": "https://api.mistral.ai/v1",
    "togetherai": "https://api.together.xyz/v1",
    "xai": "https://api.x.ai/v1",
    "cerebras": "https://api.cerebras.ai/v1",
    "deepseek": "https://api.deepseek.com/v1",
    "ollama": "http://localhost:11434/v1",
    "openrouter": "https://openrouter.ai/api/v1",
    "deepinfra": "https://api.deepinfra.com/v1/openai",
}

# The providers the shipped snapshot keeps (the runtime refresh keeps all).
SNAPSHOT_PROVIDERS: tuple[str, ...] = (
    "alibaba",
    "anthropic",
    "cerebras",
    "deepinfra",
    "deepseek",
    "fireworks-ai",
    "google",
    "groq",
    "huggingface",
    "lmstudio",
    "mistral",
    "moonshotai",
    "nebius",
    "ollama-cloud",
    "openai",
    "openrouter",
    "togetherai",
    "xai",
    "zai",
)

_MODEL_FIELDS = (
    "name",
    "family",
    "tool_call",
    "reasoning",
    "structured_output",
    "attachment",
    "open_weights",
    "release_date",
)


def _slim_model(model: dict[str, Any]) -> dict[str, Any]:
    out = {k: model[k] for k in _MODEL_FIELDS if model.get(k) is not None}
    limit, cost = model.get("limit") or {}, model.get("cost") or {}
    for key, value in (
        ("context", limit.get("context")),
        ("output", limit.get("output")),
        ("cost_input", cost.get("input")),
        ("cost_output", cost.get("output")),
    ):
        if value is not None:
            out[key] = value
    return out


def provider_base_url(provider_id: str, entry: dict[str, Any]) -> str | None:
    """The OpenAI-compatible base URL La Fabrique calls for a models.dev entry."""
    if provider_id in BASE_URL_OVERRIDES:
        return BASE_URL_OVERRIDES[provider_id]
    if entry.get("npm") == OPENAI_COMPATIBLE_NPM and entry.get("api"):
        return str(entry["api"]).rstrip("/")
    return None


def slim_catalog(
    raw: dict[str, Any], only: tuple[str, ...] | None = None
) -> dict[str, Any]:
    """models.dev's ``api.json`` reduced to what La Fabrique uses."""
    providers: dict[str, Any] = {}
    for pid, entry in sorted(raw.items()):
        if not isinstance(entry, dict) or (only and pid not in only):
            continue
        base_url = provider_base_url(pid, entry)
        if not base_url:
            continue
        models = entry.get("models") or {}
        providers[pid] = {
            "name": entry.get("name") or pid,
            "doc": entry.get("doc"),
            "base_url": base_url,
            "models": {
                str(mid): {"id": str(mid), **_slim_model(m)}
                for mid, m in sorted(models.items())
                if isinstance(m, dict)
            },
        }
    return {"source": "models.dev", "providers": providers}


class _Catalog:
    def __init__(self) -> None:
        self.data: dict[str, Any] | None = None
        self.loaded_at = 0.0
        self.origin = "snapshot"
        self._lock = threading.Lock()
        self._refreshing = False

    def get(self) -> dict[str, Any]:
        if self.data is None:
            with self._lock:
                if self.data is None:
                    self.data = json.loads(SNAPSHOT_PATH.read_text(encoding="utf-8"))
                    self.loaded_at = 0.0
        self._maybe_refresh()
        return self.data

    def _maybe_refresh(self) -> None:
        from config import settings

        hours = float(settings.MODELS_CATALOG_REFRESH_HOURS or 0)
        if hours <= 0 or self._refreshing:
            return
        if time.monotonic() - self.loaded_at < hours * 3600 and self.loaded_at:
            return
        self._refreshing = True
        threading.Thread(target=self._refresh_quietly, daemon=True).start()

    def _refresh_quietly(self) -> None:
        try:
            refresh()
        except Exception as exc:  # noqa: BLE001 - The snapshot stays.
            logger.warning("Model catalogue refresh failed: %s", exc)
            self.loaded_at = time.monotonic()  # do not retry on every request
        finally:
            self._refreshing = False


_catalog = _Catalog()


def refresh(transport: httpx.BaseTransport | None = None) -> dict[str, Any]:
    """Fetch models.dev now and replace the catalogue; raises on failure."""
    from config import settings

    with httpx.Client(timeout=FETCH_TIMEOUT_SECONDS, transport=transport) as client:
        response = client.get(settings.MODELS_CATALOG_URL)
        response.raise_for_status()
        data = slim_catalog(response.json())
    if not data["providers"]:
        raise ValueError("the catalogue listed no usable provider")
    _catalog.data, _catalog.loaded_at, _catalog.origin = data, time.monotonic(), "live"
    return data


def catalog() -> dict[str, Any]:
    return _catalog.get()


def origin() -> str:
    """``snapshot`` or ``live``: where the catalogue in memory came from."""
    return _catalog.origin


def provider(provider_id: str) -> dict[str, Any] | None:
    return catalog()["providers"].get(provider_id)


def base_url_for(provider_id: str) -> str | None:
    entry = provider(provider_id)
    if entry:
        return entry["base_url"]
    return BASE_URL_OVERRIDES.get(provider_id)


def providers_summary() -> list[dict[str, Any]]:
    return [
        {
            "id": pid,
            "name": entry["name"],
            "doc": entry.get("doc"),
            "base_url": entry["base_url"],
            "model_count": len(entry["models"]),
        }
        for pid, entry in sorted(catalog()["providers"].items())
    ]


def models_for(provider_id: str) -> list[dict[str, Any]]:
    entry = provider(provider_id)
    return list(entry["models"].values()) if entry else []


def connection_models(row: Any) -> dict[str, Any]:
    """A provider connection's models: the catalogue's entries for its
    provider, merged with the ids the provider listed when it was validated.
    A listed id the catalogue does not describe comes with no capabilities."""
    described = {m["id"]: m for m in models_for(row.provider)}
    listed = {str(mid) for mid in (row.listed_models or [])}
    models = []
    for mid in sorted(set(described) | listed):
        entry = dict(described.get(mid) or {"id": mid})
        entry["in_catalog"] = mid in described
        entry["listed"] = mid in listed
        models.append(entry)
    return {
        "connection_id": row.id,
        "name": row.name,
        "provider": row.provider,
        "validation_ok": row.validation_ok,
        "models": models,
    }
