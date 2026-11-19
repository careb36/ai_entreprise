"""
Asking each provider whether its key works, at startup and on demand.

One probe per provider, the cheapest endpoint that rejects a bad key: Gemini
and Nebius answer ``GET /models`` (it proves the key, not the credit), DeepSeek
and Qwen a one-token ``POST /chat/completions`` (it proves both: an account
without credit refuses it). The answer is classified
(``provider_status.classify``) instead of counting everything but 401/403 as
valid, and a key that is empty or an ``.env.example`` placeholder is reported
as not configured and never sent.

The proxy starts whatever the outcome, since another provider may serve; each
provider gets one log line saying what was seen, and the key never reaches a
log line or an observation.
"""

import logging
from concurrent.futures import ThreadPoolExecutor

import provider_status
import requests

logger = logging.getLogger(__name__)

# What each probe can prove (provider_status: ``checked``).
_CHECKED = {"gemini": "key", "nebius": "key", "deepseek": "key_and_credit", "qwen": "key_and_credit"}


def check_provider(provider: str, key: str, base_url: str, timeout: float) -> dict:
    """One observation of *provider*: ``status``, ``http_status``, ``checked``, ``detail``."""
    raw = (key or "").strip()
    if not raw:
        return {"status": "not_configured", "http_status": None, "checked": None,
                "detail": f"{provider.upper()}_API_KEY is not set"}
    if provider_status.is_placeholder(raw):
        return {"status": "not_configured", "http_status": None, "checked": None,
                "detail": f"{provider.upper()}_API_KEY holds a placeholder (as in .env.example), "
                          "treated as not configured"}
    try:
        http_status, text = _probe(provider, raw, base_url, timeout)
    except Exception as exc:  # noqa: BLE001 - a failed check must never stop the proxy
        return {"status": "unreachable", "http_status": None, "checked": _CHECKED.get(provider),
                "detail": provider_status.redact(f"{type(exc).__name__}: {exc}", raw)}
    status = provider_status.classify(http_status, text)
    observation = {"status": status, "http_status": http_status, "checked": _CHECKED.get(provider)}
    if status != "ok":
        observation["detail"] = provider_status.redact(text.strip() or f"HTTP {http_status}", raw)
    return observation


def check_all(keys: dict[str, str], urls: dict[str, str], timeout: float) -> dict[str, dict]:
    """Every provider's observation, checked in parallel, with one log line each."""
    with ThreadPoolExecutor(max_workers=max(1, len(keys))) as pool:
        futures = {p: pool.submit(check_provider, p, k, urls.get(p, ""), timeout) for p, k in keys.items()}
        seen = {p: f.result() for p, f in futures.items()}
    for provider, obs in seen.items():
        _log(provider, obs)
    serving = sorted(p for p, o in seen.items() if o["status"] == "ok")
    if serving:
        logger.info("Providers that answered the key check: %s", ", ".join(serving))
    else:
        logger.error("No provider answered the key check: agent tasks will fail until one does "
                     "(GET /v1/providers says why for each)")
    return seen


def _log(provider: str, obs: dict) -> None:
    status, http_status = obs["status"], obs.get("http_status")
    detail = obs.get("detail") or ""
    if status == "ok":
        logger.info("provider %s: ok (HTTP %s, checked %s)", provider, http_status, obs.get("checked"))
    elif status == "not_configured":
        logger.warning("provider %s: not_configured: %s", provider, detail)
    else:
        logger.error("provider %s: %s (HTTP %s): %s", provider, status, http_status, detail)


def _probe(provider: str, key: str, base_url: str, timeout: float) -> tuple[int, str]:
    """Ask the provider to authenticate: ``(http_status, body text)``."""
    if provider == "gemini":
        resp = requests.get(f"{base_url}/models", headers={"x-goog-api-key": key}, timeout=timeout)
    elif provider in ("deepseek", "qwen"):
        model = "deepseek-flash" if provider == "deepseek" else "qwen3.6-flash"
        resp = requests.post(
            f"{base_url}/chat/completions",
            headers={"Authorization": f"Bearer {key}"},
            json={"model": model, "messages": [{"role": "user", "content": "ping"}], "max_tokens": 1},
            timeout=timeout,
        )
    else:
        resp = requests.get(f"{base_url}/models", headers={"Authorization": f"Bearer {key}"}, timeout=timeout)
    return resp.status_code, resp.text or ""
