"""The proxy client: what it sends, how long it waits, and when it asks again."""

from __future__ import annotations

import sys
from pathlib import Path

import pytest
import requests

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "app"))

import llm_proxy_client
from llm_proxy_client import DEFAULT_TIMEOUT_SECONDS, LLMProxyClient


class _Response:
    def __init__(self, status: int, body: dict):
        self.status_code = status
        self.ok = status < 400
        self._body = body
        self.text = str(body)

    def json(self):
        return self._body


def _posting(monkeypatch, answers):
    """Fake ``requests.post`` answering in order; records every call."""
    calls: list[dict] = []

    def post(url, json=None, headers=None, timeout=None):
        calls.append({"url": url, "json": json, "headers": headers, "timeout": timeout})
        answer = answers.pop(0)
        if isinstance(answer, Exception):
            raise answer
        return answer

    monkeypatch.setattr(llm_proxy_client.requests, "post", post)
    monkeypatch.setattr(llm_proxy_client.time, "sleep", lambda s: None)
    return calls


def test_the_default_wait_covers_the_proxy_s_upstream_budget(monkeypatch):
    """A client that gives up before the proxy does discards an answer the
    provider has already billed; the default follows the proxy's budget."""
    monkeypatch.delenv("LLM_UPSTREAM_TIMEOUT_SECONDS", raising=False)
    assert llm_proxy_client.read_timeout_seconds() == 315.0
    assert LLMProxyClient("http://p").timeout == 315.0
    monkeypatch.setenv("LLM_UPSTREAM_TIMEOUT_SECONDS", "200")
    assert LLMProxyClient("http://p").timeout == 215.0
    monkeypatch.setenv("LLM_UPSTREAM_TIMEOUT_SECONDS", "not a number")
    assert LLMProxyClient("http://p").timeout == 315.0


def test_max_tokens_travels_only_when_set(monkeypatch):
    monkeypatch.delenv("LLM_THINKING", raising=False)
    ok = {"choices": [{"message": {"content": "hi"}}]}
    calls = _posting(
        monkeypatch, [_Response(200, ok), _Response(200, ok), _Response(200, ok)]
    )
    client = LLMProxyClient("http://p")
    client.max_retries = 0

    client.chat([{"role": "user", "content": "q"}], model="m", role="QA")
    assert "max_tokens" not in calls[0]["json"]

    client.max_tokens = 600
    client.chat([{"role": "user", "content": "q"}], model="m", role="QA")
    assert calls[1]["json"]["max_tokens"] == 600

    client.chat([{"role": "user", "content": "q"}], model="m", role="QA", max_tokens=50)
    assert calls[2]["json"]["max_tokens"] == 50, (
        "an explicit cap wins over the client's"
    )
    assert calls[2]["timeout"] == DEFAULT_TIMEOUT_SECONDS


def test_every_call_names_its_thinking_mode(monkeypatch):
    """DeepSeek thinks when the field is absent: no call leaves it absent."""
    monkeypatch.delenv("LLM_THINKING", raising=False)
    monkeypatch.delenv("LLM_THINKING_RESERVE_TOKENS", raising=False)
    ok = {"choices": [{"message": {"content": "hi"}}]}
    calls = _posting(monkeypatch, [_Response(200, ok) for _ in range(3)])
    client = LLMProxyClient("http://p")
    client.max_tokens = 4096

    client.chat([{"role": "user", "content": "q"}], model="m", role="Architect")
    client.chat([{"role": "user", "content": "q"}], model="m", role="QA")
    client.structured = True
    client.chat([{"role": "user", "content": "q"}], model="m", role="PO")

    reasoning, plain, structured = (c["json"] for c in calls)
    assert reasoning["thinking"] == {"type": "enabled"}
    assert reasoning["max_tokens"] == 4096 + 16384, "room for the reasoning on top"
    assert plain["thinking"] == {"type": "disabled"}
    assert plain["max_tokens"] == 4096
    assert structured["thinking"] == {"type": "disabled"}
    assert structured["max_tokens"] == 4096
    assert calls[2]["headers"]["X-LLM-Purpose"] == "structured"
    assert calls[0]["headers"]["X-LLM-Purpose"] == "free"


def test_a_read_timeout_is_named_and_not_sent_again(monkeypatch):
    """The proxy received it and may still be billing it: no second call."""
    calls = _posting(monkeypatch, [requests.ReadTimeout("Read timed out.")])

    result = LLMProxyClient("http://p").chat(
        [{"role": "user", "content": "q"}], model="m"
    )

    assert len(calls) == 1
    assert result["ok"] is False and result["reason"] == "proxy_read_timeout"
    assert result["error"].startswith("proxy_read_timeout: ")


def test_a_connection_the_proxy_dropped_is_named_and_not_sent_again(monkeypatch):
    """A proxy worker killed mid-call closes the connection: a named error."""
    dropped = requests.ConnectionError(
        "('Connection aborted.', RemoteDisconnected('Remote end closed connection without response'))"
    )
    calls = _posting(monkeypatch, [dropped])

    result = LLMProxyClient("http://p").chat(
        [{"role": "user", "content": "q"}], model="m"
    )

    assert len(calls) == 1
    assert result["reason"] == "proxy_connection_lost"


def test_an_error_the_proxy_answered_is_not_sent_again(monkeypatch):
    """The proxy already spent the upstream call and its fallbacks on it."""
    calls = _posting(
        monkeypatch,
        [_Response(502, {"error": "upstream_error", "detail": {"status": 500}})],
    )
    client = LLMProxyClient("http://p")
    assert client.max_retries == 2

    result = client.chat([{"role": "user", "content": "q"}], model="m")

    assert len(calls) == 1
    assert result["ok"] is False and result["status_code"] == 502


def test_a_request_that_never_reached_the_proxy_is_retried(monkeypatch):
    ok = {"choices": [{"message": {"content": "hi"}}]}
    calls = _posting(
        monkeypatch,
        [
            requests.ConnectionError(
                "Failed to establish a new connection: [Errno 111] Connection refused"
            ),
            _Response(200, ok),
        ],
    )
    client = LLMProxyClient("http://p")

    assert client.chat([{"role": "user", "content": "q"}], model="m") == ok
    assert len(calls) == 2


@pytest.mark.parametrize("status", [429, 500, 503])
def test_no_status_is_retried(monkeypatch, status):
    calls = _posting(monkeypatch, [_Response(status, {"error": "x"})])
    LLMProxyClient("http://p").chat([{"role": "user", "content": "q"}], model="m")
    assert len(calls) == 1
