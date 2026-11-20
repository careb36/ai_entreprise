"""Each provider's status as an observation (the readiness findings before the
first real run): the key check classifies the provider's answer instead of
calling everything but 401/403 valid; a placeholder from ``.env.example`` is
not configured and is never sent; the proxy starts whatever the outcome and
says per provider what it saw, when, and with which HTTP status, never the
key; ``GET /v1/providers?refresh=1`` checks again, rate-limited; a request
refused for lack of credit is recorded, and the fallback is unchanged."""

import json
import logging

import provider_keys
import provider_status
import pytest
from conftest import FakeResponse, gemini_ok, openai_ok

# Built by concatenation so no credential-shaped literal sits in the source.
SECRET = "sk" + "-" + "proxytest" + "0123456789abcdef"


def _err(status, message, code=None):
    error = {"message": message}
    if code:
        error["code"] = code
    return FakeResponse(status, {"error": error}, text=json.dumps({"error": error}))


# ── classify: one fake answer per class ─────────────────────────────────


@pytest.mark.parametrize(
    ("status", "text", "expected"),
    [
        (200, '{"choices": []}', "ok"),
        (204, "", "ok"),
        (401, '{"error": {"message": "Authentication Fails"}}', "invalid_key"),
        (403, '{"error": {"message": "permission denied"}}', "invalid_key"),
        (400, '{"error": {"code": "invalid_api_key"}}', "invalid_key"),
        # DeepSeek: 402 Insufficient Balance.
        (402, '{"error": {"message": "Insufficient Balance"}}', "no_credit"),
        # Qwen / DashScope: Arrearage (400), the free tier used up (403), a bill overdue.
        (400, ('{"error": {"code": "Arrearage", "message": "Access denied, please make sure '
               'your account is in good standing."}}'), "no_credit"),
        (403, ('{"error": {"code": "AllocationQuota.FreeTierOnly", "message": "The free tier '
               'of the model has been exhausted."}}'), "no_credit"),
        (400, '{"code": "PrepaidBillOverdue", "message": "bill overdue"}', "no_credit"),
        # Gemini: the project's quota used up, or billing to enable.
        (429, ('{"error": {"status": "RESOURCE_EXHAUSTED", "message": "You exceeded your current '
               'quota, please check your plan and billing details."}}'), "no_credit"),
        (400, ('{"error": {"status": "FAILED_PRECONDITION", "message": "Please enable billing '
               'on your project."}}'), "no_credit"),
        # Nebius (OpenAI-compatible): 402, or the OpenAI quota code.
        (402, '{"detail": "Payment Required"}', "no_credit"),
        (429, '{"error": {"code": "insufficient_quota"}}', "no_credit"),
        (403, '{"error": {"message": "Not enough funds on the balance"}}', "no_credit"),
        # A per-minute limit is not a lack of credit, even when Gemini words it so.
        (429, ('{"error": {"message": "You exceeded your current quota, please check your plan '
               'and billing details.", "details": [{"quotaId": '
               '"GenerateRequestsPerMinutePerProjectPerModel-FreeTier"}]}}'), "rate_limited"),
        (429, '{"error": {"message": "Rate limit reached"}}', "rate_limited"),
        (500, "internal error", "unreachable"),
        (503, '{"error": {"message": "overloaded"}}', "unreachable"),
        (None, "timed out", "unreachable"),
        (400, '{"error": {"message": "Model Not Exist"}}', "unexpected"),
        (404, "not found", "unexpected"),
    ],
)
def test_classify_names_what_the_provider_answered(status, text, expected):
    assert provider_status.classify(status, text) == expected


@pytest.mark.parametrize(
    "value",
    ["", "   ", "your_deepseek_api_key", "your_qwen_api_key",
     "your_nebius_token_factory_api_key", "YOUR_KEY_HERE", "<your-key>", "changeme"],
)
def test_placeholders_and_blanks_are_not_keys(value):
    assert provider_status.is_placeholder(value) or not value.strip()


def test_a_real_looking_key_is_not_a_placeholder():
    assert not provider_status.is_placeholder(SECRET)


def test_redact_removes_the_key_and_key_shaped_tokens():
    text = f"Incorrect API key provided: {SECRET}; also sk-abc***xyz and AIza" + "SyFakeFake"
    clean = provider_status.redact(text, SECRET)
    assert SECRET not in clean
    assert "sk-abc" not in clean and "AIzaSy" not in clean
    assert "[redacted]" in clean


# ── the startup check ───────────────────────────────────────────────────


class FakeHttp:
    """Stands in for ``requests.get`` and ``requests.post`` in provider_keys."""

    def __init__(self, answers):
        self.answers = answers  # provider -> FakeResponse or Exception
        self.calls = []

    def _answer(self, url, headers):
        provider = next(p for p, host in (("deepseek", "deepseek"), ("qwen", "qwen"),
                                          ("nebius", "tokenfactory"), ("gemini", "generativelanguage"))
                        if host in url)
        self.calls.append({"provider": provider, "headers": headers or {}})
        answer = self.answers[provider]
        if isinstance(answer, Exception):
            raise answer
        return answer

    def get(self, url, headers=None, timeout=None, **_):
        return self._answer(url, headers)

    def post(self, url, headers=None, json=None, timeout=None, **_):
        return self._answer(url, headers)


URLS = {"gemini": "https://generativelanguage.googleapis.com/v1beta",
        "deepseek": "https://api.deepseek.com/v1",
        "qwen": "https://token-plan.maas.qwencloudapi.com/compatible-mode/v1",
        "nebius": "https://api.tokenfactory.nebius.com/v1"}


@pytest.fixture()
def http(monkeypatch):
    fake = FakeHttp({})
    monkeypatch.setattr(provider_keys.requests, "get", fake.get)
    monkeypatch.setattr(provider_keys.requests, "post", fake.post)
    return fake


def test_the_check_classifies_each_provider_and_never_logs_the_key(http, caplog):
    import requests

    http.answers = {
        "deepseek": _err(402, "Insufficient Balance"),
        "qwen": _err(401, f"Invalid API-key provided: {SECRET}", code="invalid_api_key"),
        "gemini": requests.Timeout("read timed out"),
        "nebius": FakeResponse(200, {"data": []}),
    }
    keys = {"gemini": SECRET, "deepseek": SECRET, "qwen": SECRET, "nebius": SECRET}
    with caplog.at_level(logging.INFO):
        seen = provider_keys.check_all(keys, URLS, timeout=2)

    assert seen["deepseek"]["status"] == "no_credit" and seen["deepseek"]["http_status"] == 402
    assert seen["qwen"]["status"] == "invalid_key" and seen["qwen"]["http_status"] == 401
    assert seen["gemini"]["status"] == "unreachable" and seen["gemini"]["http_status"] is None
    assert seen["nebius"]["status"] == "ok" and seen["nebius"]["checked"] == "key"
    assert seen["deepseek"]["checked"] == "key_and_credit"
    assert "validated successfully" not in caplog.text
    assert "no_credit" in caplog.text and "deepseek" in caplog.text
    assert SECRET not in caplog.text
    assert SECRET not in json.dumps(seen)


def test_a_placeholder_is_not_configured_and_is_never_sent(http, caplog):
    keys = {"gemini": "", "deepseek": "your_deepseek_api_key", "qwen": "your_qwen_api_key",
            "nebius": "your_nebius_token_factory_api_key"}
    with caplog.at_level(logging.INFO):
        seen = provider_keys.check_all(keys, URLS, timeout=2)
    assert {p: o["status"] for p, o in seen.items()} == dict.fromkeys(keys, "not_configured")
    assert "placeholder" in seen["deepseek"]["detail"]
    assert "placeholder" in caplog.text and "DEEPSEEK_API_KEY" in caplog.text
    assert http.calls == []


# ── the proxy's API ─────────────────────────────────────────────────────


@pytest.fixture()
def board(proxy, monkeypatch):
    fresh = provider_status.Board(provider_status.PROVIDERS)
    monkeypatch.setattr(proxy, "_provider_status", fresh)
    monkeypatch.setattr(proxy, "_refresh_limiter", provider_status.RefreshLimiter(30))
    return fresh


def test_v1_providers_lists_every_provider_without_the_key(client, proxy, board, monkeypatch):
    monkeypatch.setattr(proxy, "DEEPSEEK_API_KEY", SECRET)
    board.record("deepseek", "no_credit", http_status=402, source="startup",
                 checked="key_and_credit", detail="Insufficient Balance")
    body = client.get("/v1/providers").get_json()
    assert set(body["providers"]) == {"gemini", "deepseek", "qwen", "nebius"}
    deepseek = body["providers"]["deepseek"]
    assert deepseek["status"] == "no_credit" and deepseek["http_status"] == 402
    assert deepseek["observed_at"] and deepseek["source"] == "startup"
    assert body["providers"]["nebius"]["status"] == "not_observed"
    assert SECRET not in json.dumps(body)


def test_health_carries_the_same_providers_block(client, board):
    board.record("qwen", "ok", http_status=200, source="startup", checked="key_and_credit")
    body = client.get("/health").get_json()
    assert body["providers"]["qwen"]["status"] == "ok"


def test_refresh_checks_again_and_is_rate_limited(client, proxy, board, http, monkeypatch):
    monkeypatch.setattr(proxy, "NEBIUS_API_KEY", "")
    http.answers = {"gemini": FakeResponse(200, {"models": []}),
                    "deepseek": _err(402, "Insufficient Balance"),
                    "qwen": openai_ok("pong")}
    first = client.get("/v1/providers?refresh=1").get_json()
    assert first["refresh"] == "done"
    assert first["providers"]["deepseek"]["status"] == "no_credit"
    assert first["providers"]["deepseek"]["source"] == "refresh"
    assert first["providers"]["nebius"]["status"] == "not_configured"
    assert all(c["provider"] != "nebius" for c in http.calls)
    calls = len(http.calls)

    second = client.get("/v1/providers?refresh=1").get_json()
    assert second["refresh"] == "rate_limited" and second["retry_after_seconds"] > 0
    assert len(http.calls) == calls  # nothing was asked again


def test_a_request_refused_for_lack_of_credit_is_recorded_and_still_falls_back(
        cross_provider, client, proxy, board, upstream):
    upstream.set("deepseek", _err(402, "Insufficient Balance"))
    upstream.set("qwen", openai_ok("from qwen"))
    resp = client.post("/v1/chat/completions",
                       json={"model": "deepseek-v4-pro", "messages": [{"role": "user", "content": "hi"}]})
    body = resp.get_json()
    assert resp.status_code == 200, body
    assert body["served_model"] == "qwen3.6-flash"
    assert body["fallback_from"]["provider"] == "deepseek" and body["fallback_from"]["status"] == 402

    seen = client.get("/v1/providers").get_json()["providers"]
    assert seen["deepseek"]["status"] == "no_credit" and seen["deepseek"]["source"] == "request"
    assert seen["deepseek"]["http_status"] == 402
    assert seen["qwen"]["status"] == "ok" and seen["qwen"]["source"] == "request"


def test_a_served_request_clears_an_earlier_no_credit(client, proxy, board, upstream):
    board.record("gemini", "no_credit", http_status=429, source="startup", checked="key")
    upstream.set("gemini", gemini_ok("hi"))
    resp = client.post("/v1/chat/completions",
                       json={"model": "gemini-3.8-flash", "messages": [{"role": "user", "content": "hi"}]})
    assert resp.status_code == 200, resp.get_json()
    assert client.get("/v1/providers").get_json()["providers"]["gemini"]["status"] == "ok"


def test_a_placeholder_key_is_not_sent_on_a_request(client, proxy, board, upstream, monkeypatch):
    monkeypatch.setattr(proxy, "DEEPSEEK_API_KEY", "your_deepseek_api_key")
    assert proxy._provider_key("deepseek") == ""
