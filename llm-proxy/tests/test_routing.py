"""
Model routing in ``POST /v1/chat/completions`` (#102).

A model id picks its provider by prefix and must be in that provider's
catalogue; nothing unknown is silently sent to Gemini.  Provider errors come
back with provider, status and message, and a 429/5xx falls back only to a
model that is in a catalogue.  Provider HTTP is faked by replacing
``requests.post``.
"""

import pytest
from conftest import FakeResponse, gemini_ok, openai_ok


def _chat(client, model, content="Write a function"):
    payload = {"messages": [{"role": "user", "content": content}]}
    if model is not None:
        payload["model"] = model
    return client.post("/v1/chat/completions", json=payload)


@pytest.mark.parametrize(
    ("model", "provider"),
    [
        ("deepseek-v4-pro", "deepseek"),
        ("qwen3.7-plus", "qwen"),
        ("gemini-3.8-flash", "gemini"),
    ],
)
def test_model_routes_to_the_provider_its_prefix_names(client, upstream, model, provider):
    upstream.set("deepseek", openai_ok("from deepseek"))
    upstream.set("qwen", openai_ok("from qwen"))
    upstream.set("gemini", gemini_ok("from gemini"))

    resp = _chat(client, model)

    assert resp.status_code == 200
    assert upstream.providers_called() == [provider]
    body = resp.get_json()
    assert body.get("served_model") == model
    assert body.get("provider") == provider
    assert body["choices"][0]["message"]["content"] == f"from {provider}"


def test_deprecated_gemini_id_is_aliased_and_reported(client, upstream):
    upstream.set("gemini", gemini_ok())

    resp = _chat(client, "gemini-2.5-flash")

    assert resp.status_code == 200
    assert "models/gemini-3.8-flash:generateContent" in upstream.calls[0]["url"]
    assert resp.get_json().get("served_model") == "gemini-3.8-flash"


@pytest.mark.parametrize("model", ["kimi-k2.6", "deepseek-coder", "gemini-9-ultra"])
def test_unknown_model_is_a_400_listing_what_is_available(client, upstream, model):
    resp = _chat(client, model)

    assert resp.status_code == 400
    body = resp.get_json()
    assert body.get("error") == "unknown_model"
    assert body.get("model") == model
    assert body.get("available") == [
        "deepseek-flash",
        "deepseek-v4-pro",
        "gemini-3.1-pro-preview",
        "gemini-3.8-flash",
        "qwen3.6-flash",
        "qwen3.7-plus",
    ]
    assert upstream.calls == []


@pytest.mark.parametrize("model", [None, "", "   "])
def test_empty_model_is_a_400(client, upstream, model):
    resp = _chat(client, model)

    assert resp.status_code == 400
    assert resp.get_json().get("available")
    assert upstream.calls == []


def test_upstream_500_is_returned_with_provider_status_and_message(client, upstream, proxy, monkeypatch):
    # Only DeepSeek is configured, so there is nothing to fall back to.
    monkeypatch.setattr(proxy, "_available_qwen_models", set())
    monkeypatch.setattr(proxy, "_available_models", set())
    upstream.set("deepseek", FakeResponse(500, {"message": "internal failure"}))

    resp = _chat(client, "deepseek-v4-pro")

    assert resp.status_code == 502
    body = resp.get_json()
    assert body.get("provider") == "deepseek"
    assert body.get("status") == 500
    assert body.get("message") == "internal failure"
    assert "choices" not in body


def test_upstream_4xx_is_an_error_without_fallback(client, upstream):
    upstream.set("deepseek", FakeResponse(401, {"error": {"message": "invalid api key"}}))
    upstream.set("qwen", openai_ok())

    resp = _chat(client, "deepseek-v4-pro")

    assert resp.status_code == 502
    assert resp.get_json()["status"] == 401
    assert resp.get_json()["message"] == "invalid api key"
    assert upstream.providers_called() == ["deepseek"]


def test_ok_status_without_choices_is_an_error_not_an_empty_answer(client, upstream):
    upstream.set("qwen", FakeResponse(200, {"id": "x", "choices": []}))

    resp = _chat(client, "qwen3.7-plus")

    assert resp.status_code == 502
    assert resp.get_json()["provider"] == "qwen"


def _openai_body(message: dict, finish_reason: str = "stop") -> FakeResponse:
    return FakeResponse(
        200,
        {
            "choices": [{"message": message, "finish_reason": finish_reason}],
            "usage": {"total_tokens": 7},
        },
    )


def test_an_empty_answer_falls_back_to_another_provider(client, upstream):
    upstream.set("deepseek", _openai_body({"role": "assistant", "content": "  "}))
    upstream.set("qwen", openai_ok("answer from qwen"))

    resp = _chat(client, "deepseek-v4-pro")

    assert resp.status_code == 200
    body = resp.get_json()
    assert upstream.providers_called() == ["deepseek", "qwen"]
    assert body["choices"][0]["message"]["content"] == "answer from qwen"
    assert body["fallback_from"]["message"] == "empty response"


def test_an_empty_answer_everywhere_is_a_502_naming_it(client, upstream):
    empty = _openai_body({"role": "assistant", "content": None})
    for provider in ("deepseek", "qwen", "gemini"):
        upstream.set(provider, empty)
    upstream.set("gemini", FakeResponse(503, {"message": "gemini down"}))

    resp = _chat(client, "deepseek-v4-pro")

    assert resp.status_code == 502
    body = resp.get_json()
    assert (body["provider"], body["message"]) == ("deepseek", "empty response")


def test_a_tool_call_without_text_is_an_answer(client, upstream):
    call = {"id": "c1", "type": "function", "function": {"name": "f", "arguments": "{}"}}
    upstream.set(
        "deepseek",
        _openai_body({"role": "assistant", "content": None, "tool_calls": [call]}, "tool_calls"),
    )

    resp = _chat(client, "deepseek-v4-pro")

    assert resp.status_code == 200
    assert upstream.providers_called() == ["deepseek"]
    assert resp.get_json()["choices"][0]["message"]["tool_calls"] == [call]


def test_an_empty_answer_cut_at_the_length_limit_is_returned_as_cut(client, upstream):
    upstream.set("deepseek", _openai_body({"role": "assistant", "content": ""}, "length"))

    resp = _chat(client, "deepseek-v4-pro")

    assert resp.status_code == 200
    assert upstream.providers_called() == ["deepseek"]
    assert resp.get_json()["choices"][0]["finish_reason"] == "length"


def test_quota_error_falls_back_to_a_catalogue_model(client, upstream):
    upstream.set("deepseek", FakeResponse(429, {"error": {"message": "quota exhausted"}}))
    upstream.set("qwen", openai_ok("answer from qwen"))

    resp = _chat(client, "deepseek-v4-pro")

    assert resp.status_code == 200
    body = resp.get_json()
    assert upstream.providers_called() == ["deepseek", "qwen"]
    assert upstream.calls[1]["json"]["model"] == "qwen3.6-flash"
    assert body.get("served_model") == "qwen3.6-flash"
    assert body.get("fallback_from", {}).get("status") == 429
    assert body["choices"][0]["message"]["content"] == "answer from qwen"


def test_5xx_fallback_skips_candidates_missing_from_the_catalogue(client, upstream, proxy, monkeypatch):
    # DeepSeek is configured but its catalogue lacks the fallback candidate.
    monkeypatch.setattr(proxy, "_available_deepseek_models", {"deepseek-v4-pro"})
    upstream.set("gemini", FakeResponse(503, {"error": {"message": "overloaded"}}))
    upstream.set("deepseek", openai_ok())
    upstream.set("qwen", openai_ok("answer from qwen"))

    resp = _chat(client, "gemini-3.8-flash")

    assert resp.status_code == 200
    assert upstream.providers_called() == ["gemini", "qwen"]
    assert resp.get_json().get("served_model") == "qwen3.6-flash"


@pytest.mark.parametrize(
    "response",
    [
        FakeResponse(402, {"error": {"message": "Insufficient Balance"}}),
        FakeResponse(400, {"error": {"message": "Your credit balance is too low"}}),
        FakeResponse(403, {"error": {"code": "Arrearage", "message": "Arrearage"}}),
        FakeResponse(200, {"error": {"message": "insufficient_quota"}}),
    ],
)
def test_a_provider_without_credit_falls_back(client, upstream, response):
    upstream.set("deepseek", response)
    upstream.set("qwen", openai_ok("answer from qwen"))

    resp = _chat(client, "deepseek-v4-pro")

    assert resp.status_code == 200
    assert upstream.providers_called() == ["deepseek", "qwen"]
    assert resp.get_json()["fallback_from"]["status"] == response.status_code


@pytest.mark.parametrize(
    "response",
    [
        FakeResponse(403, {"error": {"message": "forbidden"}}),
        FakeResponse(400, {"error": {"message": "insufficient permissions for model"}}),
        FakeResponse(400, {"error": {"message": "bad request from the load balancer"}}),
    ],
)
def test_a_refusal_that_is_not_about_credit_does_not_fall_back(client, upstream, response):
    upstream.set("deepseek", response)
    upstream.set("qwen", openai_ok("answer from qwen"))

    resp = _chat(client, "deepseek-v4-pro")

    assert resp.status_code == 502
    assert upstream.providers_called() == ["deepseek"]


def test_no_credit_anywhere_keeps_the_402_for_the_backend(client, upstream):
    no_credit = FakeResponse(402, {"error": {"message": "Insufficient Balance"}})
    for provider in ("deepseek", "qwen", "gemini"):
        upstream.set(provider, no_credit)

    resp = _chat(client, "deepseek-v4-pro")

    # The backend pauses the sprint on the primary's 402 (sprint_control).
    assert resp.status_code == 502
    assert resp.get_json()["status"] == 402


def test_failed_fallbacks_return_the_original_error(client, upstream):
    upstream.set("qwen", FakeResponse(500, {"message": "qwen down"}))
    upstream.set("deepseek", FakeResponse(503, {"message": "deepseek down"}))
    upstream.set("gemini", FakeResponse(500, {"message": "gemini down"}))

    resp = _chat(client, "qwen3.7-plus")

    assert resp.status_code == 502
    body = resp.get_json()
    assert (body["provider"], body["status"], body["message"]) == ("qwen", 500, "qwen down")
    assert [a["provider"] for a in body["attempts"]] == ["qwen", "deepseek", "gemini"]


def test_upstream_timeout_defaults_to_110_seconds(client, upstream, proxy):
    upstream.set("deepseek", openai_ok())

    _chat(client, "deepseek-v4-pro")

    assert upstream.calls[0]["timeout"] == 110
    assert proxy.LLM_UPSTREAM_TIMEOUT_SECONDS == 110


def test_nemotron_routes_to_nebius_token_factory(nebius, client, upstream):
    upstream.set("nebius", openai_ok("from nemotron"))

    resp = _chat(client, "nvidia/Llama-3.1-Nemotron-70B-Instruct")

    assert resp.status_code == 200
    assert upstream.providers_called() == ["nebius"]
    assert upstream.calls[0]["url"] == (
        "https://api.tokenfactory.nebius.com/v1/chat/completions"
    )
    body = resp.get_json()
    assert body.get("provider") == "nebius"
    assert body.get("served_model") == "nvidia/Llama-3.1-Nemotron-70B-Instruct"
    assert body["choices"][0]["message"]["content"] == "from nemotron"


def test_nebius_reports_a_provider_error_with_its_status(nebius, client, upstream):
    # 400 is not retriable, so no cross-provider fallback masks the answer.
    upstream.set("nebius", FakeResponse(400, {"error": {"message": "model is not available"}}))

    resp = _chat(client, "nvidia/Llama-3.1-Nemotron-70B-Instruct")

    assert resp.status_code >= 400
    body = resp.get_json()
    assert body.get("provider") == "nebius"
    assert "model is not available" in (body.get("detail") or body.get("message") or str(body))
    assert upstream.providers_called() == ["nebius"]


def test_nebius_models_are_listed_with_a_tier(nebius, client):
    resp = client.get("/v1/models")

    assert resp.status_code == 200
    listed = {m["id"]: m for m in resp.get_json()["models"] if m["provider"] == "nebius"}
    assert set(listed) == {
        "nvidia/Llama-3.1-Nemotron-70B-Instruct",
        "nvidia/Nemotron-Nano-9B",
    }
    assert listed["nvidia/Nemotron-Nano-9B"]["tier"] == "fast"


def test_nebius_models_need_the_key(proxy, client):
    # The shared fixture sets no NEBIUS_API_KEY, so nothing from Nebius is listed.
    resp = client.get("/v1/models")

    assert resp.status_code == 200
    assert not [m for m in resp.get_json()["models"] if m["provider"] == "nebius"]


# ── How a call's token count is known ────────────────────────────────────────


def test_a_reported_count_is_usage(client, upstream):
    upstream.set("deepseek", openai_ok())
    body = client.post("/v1/chat/completions", json={"model": "deepseek-flash", "messages": [{"role": "user", "content": "hi"}]}).get_json()
    assert body["usage"]["total_tokens"] == 12
    assert body["usage_status"] == "reported"
    assert "usage_estimate" not in body


def test_a_missing_count_is_estimated_apart_and_never_shown_as_usage(client, upstream, proxy, monkeypatch):
    charged = []
    monkeypatch.setattr(proxy, "track_usage_in_db", lambda bu_id, tokens: charged.append(tokens))
    upstream.set("deepseek", FakeResponse(200, {"choices": [{"message": {"role": "assistant", "content": "hello"}}]}))
    body = client.post("/v1/chat/completions", json={"model": "deepseek-flash", "messages": [{"role": "user", "content": "hi"}]}).get_json()
    assert "usage" not in body
    assert body["usage_status"] == "estimated"
    # The quota is still charged, with the estimate, so a budget cannot leak.
    assert charged == [body["usage_estimate"]] and body["usage_estimate"] > 0


def test_max_tokens_reaches_gemini_as_max_output_tokens(client, upstream):
    """OpenAI-compatible providers read max_tokens from the body as is; Gemini
    wants it in generationConfig, or the answer runs to the model's own limit."""
    upstream.set("gemini", gemini_ok())
    upstream.set("deepseek", openai_ok())
    body = {"messages": [{"role": "user", "content": "hi"}], "max_tokens": 600}

    client.post("/v1/chat/completions", json={**body, "model": "gemini-3.8-flash"})
    client.post("/v1/chat/completions", json={**body, "model": "deepseek-v4-pro"})

    gemini, deepseek = upstream.calls
    assert gemini["json"]["generationConfig"] == {"temperature": 0.7, "maxOutputTokens": 600}
    assert deepseek["json"]["max_tokens"] == 600

    client.post("/v1/chat/completions", json={"model": "gemini-3.8-flash", "messages": body["messages"]})
    assert "maxOutputTokens" not in upstream.calls[2]["json"]["generationConfig"]


def test_gemini_answers_carry_a_finish_reason_like_the_others(client, upstream):
    """A caller tells an answer cut at the limit ("length") from a complete one."""
    for gemini_reason, expected in [("MAX_TOKENS", "length"), ("STOP", "stop"), (None, "other")]:
        candidate = {"content": {"parts": [{"text": "hello"}]}}
        if gemini_reason:
            candidate["finishReason"] = gemini_reason
        upstream.set("gemini", FakeResponse(200, {"candidates": [candidate]}))
        answer = _chat(client, "gemini-3.8-flash").get_json()
        assert answer["choices"][0]["finish_reason"] == expected, gemini_reason


def test_gemini_reports_its_own_count_or_the_estimate_stays_apart(client, upstream):
    upstream.set(
        "gemini",
        FakeResponse(
            200,
            {"candidates": [{"content": {"parts": [{"text": "hello"}]}}], "usageMetadata": {"totalTokenCount": 33}},
        ),
    )
    reported = client.post("/v1/chat/completions", json={"model": "gemini-3.8-flash", "messages": [{"role": "user", "content": "hi"}]}).get_json()
    assert (reported["usage"]["total_tokens"], reported["usage_status"]) == (33, "reported")

    upstream.set("gemini", gemini_ok())
    estimated = client.post("/v1/chat/completions", json={"model": "gemini-3.8-flash", "messages": [{"role": "user", "content": "hi"}]}).get_json()
    assert "usage" not in estimated
    assert estimated["usage_status"] == "estimated"


def _empty_with_usage(tokens: int) -> FakeResponse:
    return FakeResponse(
        200,
        {
            "choices": [{"message": {"role": "assistant", "content": ""}, "finish_reason": "stop"}],
            "usage": {"total_tokens": tokens},
        },
    )


def test_an_empty_answer_s_reported_tokens_are_charged_apart(client, upstream, proxy, monkeypatch):
    charged = []
    monkeypatch.setattr(proxy, "track_usage_in_db", lambda bu_id, tokens: charged.append(tokens))
    upstream.set("deepseek", _empty_with_usage(1000))
    upstream.set("qwen", openai_ok("answer from qwen"))

    body = client.post(
        "/v1/chat/completions",
        json={"model": "deepseek-v4-pro", "messages": [{"role": "user", "content": "hi"}]},
        headers={"X-Business-Unit-ID": "7"},
    ).get_json()

    # The served answer's usage is its own; the empty call's tokens are charged beside it.
    assert body["usage"]["total_tokens"] == 12
    assert body["fallback_from"]["failed_attempts_tokens"] == 1000
    assert charged == [1012]


def test_when_every_provider_fails_their_reported_tokens_are_still_charged(client, upstream, proxy, monkeypatch):
    charged = []
    monkeypatch.setattr(proxy, "track_usage_in_db", lambda bu_id, tokens: charged.append(tokens))
    upstream.set("deepseek", _empty_with_usage(300))
    upstream.set("qwen", _empty_with_usage(200))
    upstream.set("gemini", FakeResponse(503, {"message": "gemini down"}))

    resp = client.post(
        "/v1/chat/completions",
        json={"model": "deepseek-v4-pro", "messages": [{"role": "user", "content": "hi"}]},
        headers={"X-Business-Unit-ID": "7"},
    )

    assert resp.status_code == 502
    body = resp.get_json()
    assert body["tokens"] == 300
    assert [a.get("tokens") for a in body["attempts"]] == [300, 200, None]
    assert charged == [500]


def test_a_failure_that_reported_no_tokens_charges_nothing(client, upstream, proxy, monkeypatch):
    charged = []
    monkeypatch.setattr(proxy, "track_usage_in_db", lambda bu_id, tokens: charged.append(tokens))
    for provider in ("deepseek", "qwen", "gemini"):
        upstream.set(provider, FakeResponse(503, {"message": "down"}))

    resp = _chat(client, "deepseek-v4-pro")

    assert resp.status_code == 502
    assert "tokens" not in resp.get_json()
    assert charged == [0]
