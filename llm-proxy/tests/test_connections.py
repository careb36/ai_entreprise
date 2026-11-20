"""A company's own provider connection (``connections.py``; issue #356, step 3):
the proxy calls the connection's endpoint with its key only when the request
carries the internal token the backend shares with it; without a configured
token, or with a wrong one, the connection is refused and nothing is called;
the company's key never reaches the answer; a connection has no fallback to
the deployment's providers."""

import base64
import json

import pytest
from conftest import FakeResponse, openai_ok

TOKEN = "internal-secret"


class Recorder:
    def __init__(self, response):
        self.calls = []
        self.response = response

    def __call__(self, url, headers=None, json=None, timeout=None, **_):
        self.calls.append({"url": url, "headers": headers or {}, "json": json})
        return self.response


def _conn(**over):
    conn = {"name": "acme-openrouter", "base_url": "https://openrouter.example/api/v1",
            "api_key": "sk-company", "headers": {"X-Title": "La Fabrique"}, **over}
    return base64.b64encode(json.dumps(conn).encode()).decode()


def _chat(client, headers, model="anthropic/claude-x"):
    return client.post("/v1/chat/completions",
                       json={"model": model, "messages": [{"role": "user", "content": "hi"}]},
                       headers=headers)


@pytest.fixture()
def recorder(proxy, monkeypatch):
    rec = Recorder(openai_ok("from the company's provider"))
    monkeypatch.setattr(proxy.requests, "post", rec)
    monkeypatch.setenv("LLM_PROXY_INTERNAL_TOKEN", TOKEN)
    return rec


def test_a_connection_is_called_with_the_companys_key(client, recorder):
    resp = _chat(client, {"X-Provider-Connection": _conn(), "X-Internal-Token": TOKEN})
    assert resp.status_code == 200, resp.get_json()
    call = recorder.calls[0]
    assert call["url"] == "https://openrouter.example/api/v1/chat/completions"
    assert call["headers"]["Authorization"] == "Bearer sk-company"
    assert call["headers"]["X-Title"] == "La Fabrique"
    assert call["json"]["model"] == "anthropic/claude-x"
    body = resp.get_json()
    assert body["provider"] == "connection:acme-openrouter"
    assert body["served_model"] == "anthropic/claude-x"
    assert "sk-company" not in json.dumps(body)


@pytest.mark.parametrize(
    ("headers", "message"),
    [
        ({"X-Provider-Connection": _conn()}, "missing or wrong"),
        ({"X-Provider-Connection": _conn(), "X-Internal-Token": "guess"}, "missing or wrong"),
        ({"X-Provider-Connection": "not base64!", "X-Internal-Token": TOKEN}, "base64 JSON"),
        ({"X-Provider-Connection": _conn(base_url="file:///etc"), "X-Internal-Token": TOKEN}, "http(s)"),
    ],
)
def test_a_connection_without_the_right_token_is_refused(client, recorder, headers, message):
    resp = _chat(client, headers)
    assert resp.status_code == 403
    assert message in resp.get_json()["message"]
    assert recorder.calls == []


def test_without_a_configured_token_connections_are_disabled(client, proxy, monkeypatch):
    rec = Recorder(openai_ok())
    monkeypatch.setattr(proxy.requests, "post", rec)
    monkeypatch.delenv("LLM_PROXY_INTERNAL_TOKEN", raising=False)
    resp = _chat(client, {"X-Provider-Connection": _conn(), "X-Internal-Token": ""})
    assert resp.status_code == 403 and "disabled" in resp.get_json()["message"]
    assert rec.calls == []


def test_a_failing_connection_does_not_fall_back_to_the_deployments_providers(client, recorder):
    recorder.response = FakeResponse(503, {"error": {"message": "overloaded"}})
    resp = _chat(client, {"X-Provider-Connection": _conn(), "X-Internal-Token": TOKEN})
    assert resp.status_code == 502
    assert len(recorder.calls) == 1  # no catalogue fallback
    assert "sk-company" not in json.dumps(resp.get_json())


def test_requests_without_a_connection_route_as_before(client, upstream):
    upstream.set("deepseek", openai_ok("from deepseek"))
    resp = _chat(client, {}, model="deepseek-v4-pro")
    assert resp.status_code == 200 and resp.get_json()["provider"] == "deepseek"


def test_a_provider_error_that_echoes_the_secrets_is_redacted(client, recorder, proxy, monkeypatch):
    echo = FakeResponse(401, {"error": {"message": "bad key sk-company for X-Title=La Fabrique"}})
    monkeypatch.setattr(proxy.requests, "post", Recorder(echo))
    resp = _chat(client, {"X-Provider-Connection": _conn(), "X-Internal-Token": TOKEN})
    assert resp.status_code >= 400
    text = json.dumps(resp.get_json())
    assert "sk-company" not in text
    assert "La Fabrique" not in text
    assert "[redacted]" in text


def test_a_transport_error_that_echoes_the_key_is_redacted(client, recorder, proxy, monkeypatch):
    def boom(url, headers=None, json=None, timeout=None, **_):
        raise proxy.requests.RequestException("connection reset; Authorization: Bearer sk-company")

    monkeypatch.setattr(proxy.requests, "post", boom)
    resp = _chat(client, {"X-Provider-Connection": _conn(), "X-Internal-Token": TOKEN})
    assert resp.status_code >= 400
    assert "sk-company" not in json.dumps(resp.get_json())
