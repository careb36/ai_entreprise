"""
The proxy configured with DeepSeek alone (issue #751).

Only ``DEEPSEEK_API_KEY`` is set: a failed request moves between DeepSeek Pro
and Flash and never to another provider, even when another provider's key is
set, unless the operator names that provider in ``LLM_FALLBACK_PROVIDERS``.
Embeddings are local (the semantic model, or the hash vector when it cannot
load; issue #755), with no outside call and no error, and ``/health`` says
which with a reason.
"""

import embedding
import pytest
import routing
from conftest import FakeResponse, openai_ok


@pytest.fixture()
def deepseek_only(proxy, monkeypatch):
    """Only DeepSeek's key and catalogue; the other providers are unset and empty."""
    monkeypatch.setattr(proxy, "GEMINI_API_KEY", "")
    monkeypatch.setattr(proxy, "QWEN_API_KEY", "")
    monkeypatch.setattr(proxy, "NEBIUS_API_KEY", "")
    monkeypatch.setattr(proxy, "_available_models", set())
    monkeypatch.setattr(proxy, "_available_qwen_models", set())
    monkeypatch.setattr(proxy, "_available_nebius_models", set())
    monkeypatch.setattr(proxy, "_refresh_nebius_models", lambda: None)
    monkeypatch.delenv("EMBEDDING_PROVIDER", raising=False)
    return proxy


def _chat(client, model):
    return client.post("/v1/chat/completions",
                       json={"model": model, "messages": [{"role": "user", "content": "hi"}]})


class _ByModel:
    """``requests.post`` that answers by the model in the body (DeepSeek only)."""

    def __init__(self, answers):
        self.answers = answers
        self.calls = []

    def __call__(self, url, headers=None, json=None, timeout=None, **_):
        assert "deepseek" in url, f"a request left DeepSeek: {url}"
        self.calls.append(json["model"])
        return self.answers[json["model"]]


@pytest.mark.parametrize(("failed", "stand_in"), [("deepseek-v4-pro", "deepseek-flash"),
                                                   ("deepseek-flash", "deepseek-v4-pro")])
@pytest.mark.parametrize("status", [429, 500, 503])
def test_a_failed_deepseek_model_falls_back_to_the_other(deepseek_only, client, monkeypatch, failed, stand_in, status):
    fake = _ByModel({failed: FakeResponse(status, {"error": {"message": "busy"}}), stand_in: openai_ok("ok")})
    monkeypatch.setattr(deepseek_only.requests, "post", fake)

    resp = _chat(client, failed)

    assert resp.status_code == 200
    body = resp.get_json()
    assert fake.calls == [failed, stand_in]
    assert (body["provider"], body["served_model"]) == ("deepseek", stand_in)
    assert body["fallback_from"]["status"] == status


def test_both_deepseek_models_failing_is_a_502_without_leaving_deepseek(deepseek_only, client, monkeypatch):
    down = FakeResponse(503, {"error": {"message": "down"}})
    fake = _ByModel({"deepseek-v4-pro": down, "deepseek-flash": down})
    monkeypatch.setattr(deepseek_only.requests, "post", fake)

    resp = _chat(client, "deepseek-v4-pro")

    assert resp.status_code == 502
    assert [a["provider"] for a in resp.get_json()["attempts"]] == ["deepseek", "deepseek"]


def test_another_providers_key_does_not_make_it_a_fallback(proxy, client, upstream):
    # Every key and catalogue is set (the fixture), but LLM_FALLBACK_PROVIDERS is not.
    upstream.set("deepseek", FakeResponse(429, {"error": {"message": "quota"}}))
    upstream.set("qwen", openai_ok("from qwen"))
    upstream.set("gemini", openai_ok("from gemini"))

    resp = _chat(client, "deepseek-v4-pro")

    assert resp.status_code == 429
    assert upstream.providers_called() == ["deepseek", "deepseek"]


def test_fallback_targets_never_name_another_provider_by_default(deepseek_only):
    every = {"deepseek": {"deepseek-v4-pro", "deepseek-flash"}, "qwen": {"qwen3.6-flash"},
             "gemini": set(routing.PREFERENCE_TIERS["fast"])}
    for failed in ("deepseek-v4-pro", "deepseek-flash"):
        targets = routing.fallback_targets("deepseek", lambda p: every.get(p, set()), failed_model=failed)
        assert targets and {provider for provider, _ in targets} == {"deepseek"}
        assert failed not in [model for _, model in targets]


def test_llm_fallback_providers_is_parsed_in_order_and_unknown_names_dropped():
    assert routing.parse_fallback_providers("") == ()
    assert routing.parse_fallback_providers(" Gemini, qwen ,nope,gemini") == ("gemini", "qwen")


def test_embeddings_are_local_and_call_nothing(deepseek_only, client, monkeypatch):
    def refuse(*args, **kwargs):
        raise AssertionError("an embedding left the proxy")

    monkeypatch.setattr(deepseek_only.requests, "post", refuse)

    resp = client.post("/v1/embeddings", json={"input": "remember this decision"})

    assert resp.status_code == 200
    body = resp.get_json()
    assert body["model"] in ("multilingual-e5-small", embedding.HASH_MODEL)
    assert len(body["data"][0]["embedding"]) == embedding.DIMS


def test_a_gemini_key_alone_does_not_send_memories_to_gemini(proxy, client, monkeypatch):
    monkeypatch.delenv("EMBEDDING_PROVIDER", raising=False)

    def refuse(*args, **kwargs):
        raise AssertionError("an embedding left the proxy")

    monkeypatch.setattr(proxy.requests, "post", refuse)

    body = client.post("/v1/embeddings", json={"input": "x"}).get_json()

    assert body["model"] in ("multilingual-e5-small", embedding.HASH_MODEL)


def test_gemini_embeddings_when_chosen_are_padded_to_the_column_width(proxy, client, monkeypatch):
    monkeypatch.setenv("EMBEDDING_PROVIDER", "gemini")
    seen = []

    def gemini(url, headers=None, json=None, timeout=None):
        seen.append(url)
        answer = FakeResponse(200, {"embedding": {"values": [0.5] * 768}})
        answer.raise_for_status = lambda: None
        return answer

    monkeypatch.setattr(proxy.requests, "post", gemini)

    body = client.post("/v1/embeddings", json={"input": "x"}).get_json()

    assert "generativelanguage" in seen[0]
    assert body["model"] == "text-embedding-004"
    vector = body["data"][0]["embedding"]
    assert len(vector) == embedding.DIMS and vector[:768] == [0.5] * 768 and not any(vector[768:])


def test_health_names_the_capabilities_and_their_reasons(deepseek_only, client, model_unavailable):
    caps = client.get("/health").get_json()["capabilities"]

    assert caps["providers_configured"] == ["deepseek"]
    assert caps["embeddings"]["state"] == "degraded"
    assert caps["embeddings"]["provider"] == "local-hash"
    assert "not loaded" in caps["embeddings"]["reason"]
    assert caps["failover"]["scope"] == "same_provider"
    assert "d-key" not in str(caps)


def test_health_names_an_explicit_cross_provider_failover(cross_provider, client):
    failover = client.get("/health").get_json()["capabilities"]["failover"]

    assert failover["scope"] == "cross_provider"
    assert failover["providers"] == ["deepseek", "qwen", "gemini"]


@pytest.mark.parametrize(("value", "key", "state", "provider"), [
    ("gemini", "", "degraded", "local-hash"),
    ("gemini", "g", "on", "gemini"),
    ("bogus", "g", "degraded", "local-hash"),
    ("local-hash", "g", "degraded", "local-hash"),
    ("local", "g", "degraded", "local-hash"),
])
def test_embedding_capability_says_why(monkeypatch, model_unavailable, value, key, state, provider):
    monkeypatch.setenv("EMBEDDING_PROVIDER", value)

    cap = embedding.capability(key)

    assert (cap["state"], cap["provider"]) == (state, provider)
    assert cap["reason"]
