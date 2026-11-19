"""A model's price per million tokens, from the model catalogue (models.dev).

``price_for(model, provider)`` finds the catalogue entry a turn was served by
and returns its ``cost_input`` / ``cost_output`` (US dollars per million
input and output tokens), or no price with the reason. The rules, most
specific first:

1. the provider the proxy named (``deepseek``, ``gemini``, ``qwen``,
   ``nebius``; mapped onto the catalogue's ids by ``PROXY_PROVIDERS``) or a
   company connection's models.dev provider (``connection_provider``);
2. a model id written ``<provider>/<model>`` whose prefix is a catalogue
   provider.
   When the serving provider is known and does not price the model, the
   answer is ``price_unknown``: another provider's price is not its own
   (a local ``openai-compatible`` server is never priced as a cloud one);
3. otherwise, every provider that lists the model id: one price among
   them is the price, different prices are ``price_ambiguous``, none is
   ``price_unknown``.

A free model is priced 0 only when the catalogue says 0; a model the
catalogue does not price is never taken for free.

The default team's two DeepSeek models are priced from DeepSeek's own pricing
page instead (``PUBLISHED_PRICES``), which models.dev lags: the peak price, so
a cost report never under-states; off-peak hours cost half (see
``docs/contexts/models.md``). ``deepseek-v4-flash`` is a retired name DeepSeek
still accepts and serves with ``deepseek-flash``, so it is priced as that.
"""

from __future__ import annotations

from typing import Any

from services.models import model_catalog

# The proxy's provider names (``llm-proxy/app.py``) → models.dev provider ids.
PROXY_PROVIDERS: dict[str, str] = {
    "gemini": "google",
    "qwen": "alibaba",
    "deepseek": "deepseek",
    "nebius": "nebius",
}

# DeepSeek's published prices per million tokens (USD), peak hours, input on a
# cache miss; https://api-docs.deepseek.com/quick_start/pricing, read
# 2026-11-19. Keyed by models.dev provider id and model id.
PUBLISHED_PRICES: dict[tuple[str, str], dict[str, float]] = {
    ("deepseek", "deepseek-flash"): {"input": 0.30, "output": 1.20},
    ("deepseek", "deepseek-v4-pro"): {"input": 1.32, "output": 3.96},
}
# Retired names a provider still accepts and serves with another model.
PRICE_ALIASES: dict[tuple[str, str], str] = {
    ("deepseek", "deepseek-v4-flash"): "deepseek-flash",
}

PRICE_UNKNOWN = "price_unknown"
PRICE_AMBIGUOUS = "price_ambiguous"
NO_MODEL = "model_unknown"


def _price_of(entry: dict[str, Any] | None) -> dict[str, float] | None:
    if not isinstance(entry, dict):
        return None
    cost_in, cost_out = entry.get("cost_input"), entry.get("cost_output")
    if not isinstance(cost_in, int | float) or not isinstance(cost_out, int | float):
        return None
    if isinstance(cost_in, bool) or isinstance(cost_out, bool):
        return None
    return {"input": float(cost_in), "output": float(cost_out)}


def _listed_price(
    providers: dict[str, Any], pid: str, mid: str
) -> tuple[dict[str, float] | None, str]:
    """*pid*'s price for *mid* and the model id it is priced as."""
    mid = PRICE_ALIASES.get((pid, mid), mid)
    published = PUBLISHED_PRICES.get((pid, mid))
    if published is not None:
        return dict(published), mid
    return _price_of(((providers.get(pid) or {}).get("models") or {}).get(mid)), mid


def price_for(
    model: str | None,
    provider: str | None = None,
    connection_provider: str | None = None,
    catalog: dict[str, Any] | None = None,
) -> tuple[dict[str, Any] | None, str | None]:
    """``({"input", "output", "provider", "model"}, None)`` or ``(None, reason)``."""
    name = (model or "").strip()
    if not name:
        return None, NO_MODEL
    providers = (catalog or model_catalog.catalog()).get("providers") or {}
    named = connection_provider or PROXY_PROVIDERS.get((provider or "").strip())
    candidates: list[tuple[str, str]] = []
    if named:
        candidates.append((named, name))
    prefix, slash, rest = name.partition("/")
    if slash and prefix in providers:
        candidates.append((prefix, rest))
    for pid, mid in candidates:
        price, mid = _listed_price(providers, pid, mid)
        if price is not None:
            return {**price, "provider": pid, "model": mid}, None
    if named:
        # The provider that served it is known: another's price is not its.
        return None, PRICE_UNKNOWN
    found: dict[tuple[float, float], str] = {}
    for pid in sorted(providers):
        price, _ = _listed_price(providers, pid, name)
        if price is not None:
            found.setdefault((price["input"], price["output"]), pid)
    if len(found) == 1:
        (cost_in, cost_out), pid = next(iter(found.items()))
        return {
            "input": cost_in,
            "output": cost_out,
            "provider": pid,
            "model": name,
        }, None
    return None, PRICE_AMBIGUOUS if found else PRICE_UNKNOWN


def cents(input_tokens: int, output_tokens: int, price: dict[str, Any]) -> float:
    """US cents for the tokens at *price* (dollars per million), unrounded."""
    dollars = (input_tokens * price["input"] + output_tokens * price["output"]) / 1e6
    return dollars * 100
