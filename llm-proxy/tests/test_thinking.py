"""DeepSeek's thinking mode, empty answers and the time budget, against a fake DeepSeek.

- ``thinking`` is always explicit for DeepSeek and never sent elsewhere;
- a 200 with content is an answer; a 200 with empty content is a named
  error: ``empty_content_reasoning_exhausted`` when the reasoning used the
  whole budget (``finish_reason: length``), ``empty_content`` otherwise;
- a structured call (``X-LLM-Purpose: structured``) that exhausted its budget
  reasoning is retried once with thinking off;
- an answer slower than the budget, keep-alive lines included, is a named
  ``upstream_timeout``;
- gunicorn's and the proxy's timeouts derive from ``LLM_UPSTREAM_TIMEOUT_SECONDS``.

DeepSeek's fields: https://api-docs.deepseek.com/guides/thinking_mode and
https://api-docs.deepseek.com/api/create-chat-completion.
"""

import importlib.util
from pathlib import Path

import pytest
import thinking
import timeouts
import upstream as upstream_module
from conftest import FakeResponse, openai_ok

STRUCTURED = {"X-LLM-Purpose": "structured"}


def _deepseek(content: str | None, finish: str = "stop", reasoning: str | None = None,
              tokens: int = 50) -> FakeResponse:
    message = {"role": "assistant", "content": content}
    if reasoning is not None:
        message["reasoning_content"] = reasoning
    return FakeResponse(200, {"choices": [{"message": message, "finish_reason": finish}],
                              "usage": {"total_tokens": tokens}})


def _chat(client, model="deepseek-v4-pro", headers=None, **extra):
    payload = {"model": model, "messages": [{"role": "user", "content": "plan"}], **extra}
    return client.post("/v1/chat/completions", json=payload, headers=headers or {})


@pytest.fixture()
def others_down(upstream):
    """No fallback answers, so the primary's own error is what comes back."""
    for provider in ("qwen", "gemini"):
        upstream.set(provider, FakeResponse(503, {"message": "down"}))
    return upstream


# --- (a) content ---------------------------------------------------------------

def test_a_deepseek_answer_with_content_is_returned(client, upstream):
    upstream.set("deepseek", _deepseek('{"tasks": []}', reasoning="I thought about it"))

    resp = _chat(client, thinking={"type": "enabled"})

    assert resp.status_code == 200
    message = resp.get_json()["choices"][0]["message"]
    assert message["content"] == '{"tasks": []}'
    assert upstream.calls[0]["json"]["thinking"] == {"type": "enabled"}


# --- (b) empty content, cut at the limit, after reasoning ------------------------

def test_reasoning_that_used_the_budget_is_a_named_error(client, others_down):
    others_down.set("deepseek", _deepseek("", "length", reasoning="step 1 ... step 900", tokens=4096))

    resp = _chat(client, thinking={"type": "enabled"})

    assert resp.status_code == 502
    body = resp.get_json()
    assert body["reason"] == "empty_content_reasoning_exhausted"
    assert body["message"].startswith("empty_content_reasoning_exhausted: finish_reason=length")
    assert "step 1" not in str(body), "the reasoning is never passed off as the answer"


def test_a_structured_call_is_retried_once_with_thinking_off(client, upstream):
    upstream.set("deepseek", [
        _deepseek(None, "length", reasoning="long reasoning", tokens=4096),
        _deepseek('{"tasks": [1, 2]}', tokens=300),
    ])

    resp = _chat(client, headers=STRUCTURED, thinking={"type": "enabled"})

    assert resp.status_code == 200
    body = resp.get_json()
    assert body["choices"][0]["message"]["content"] == '{"tasks": [1, 2]}'
    assert [c["json"]["thinking"] for c in upstream.calls] == [
        {"type": "enabled"}, {"type": "disabled"}]
    assert body["thinking_retry"] == {
        "reason": "empty_content_reasoning_exhausted", "first_attempt_tokens": 4096}


def test_the_retry_is_for_structured_calls_only(client, others_down):
    others_down.set("deepseek", [_deepseek("", "length", reasoning="r", tokens=10),
                                 _deepseek("", "length", reasoning="r", tokens=10)])

    resp = _chat(client, thinking={"type": "enabled"})

    assert resp.status_code == 502
    # The primary and its fallback (deepseek-flash is DeepSeek too) both ran
    # with thinking as asked; none was retried with thinking off.
    assert all(c["json"]["thinking"] == {"type": "enabled"}
               for c in others_down.calls if c["provider"] == "deepseek")


def test_a_failed_retry_keeps_its_own_reason(client, others_down):
    others_down.set("deepseek", [_deepseek("", "length", reasoning="r", tokens=10),
                                 _deepseek("   ", "stop", tokens=5)])

    resp = _chat(client, headers=STRUCTURED, thinking={"type": "enabled"})

    assert resp.status_code == 502
    assert resp.get_json()["attempts"][0]["reason"] == "empty_content"


# --- (c) empty content otherwise --------------------------------------------------

@pytest.mark.parametrize("content", ["", "   ", None])
def test_any_other_empty_answer_is_empty_content(client, others_down, content):
    others_down.set("deepseek", _deepseek(content, "stop", reasoning="thought"))

    resp = _chat(client, headers=STRUCTURED, thinking={"type": "disabled"})

    assert resp.status_code == 502
    body = resp.get_json()
    assert body["reason"] == "empty_content"
    assert "reasoning_content: 7 characters" in body["message"]


# --- (d) slower than the budget ---------------------------------------------------

def test_keep_alive_lines_beyond_the_budget_end_in_a_named_timeout(monkeypatch):
    """DeepSeek sends empty lines while it works: a read timeout never fires."""
    alive = FakeResponse(200, chunks=[b"\n"] * 100, delay=0.05)
    monkeypatch.setattr(upstream_module.requests, "post", lambda *a, **k: alive)

    with pytest.raises(upstream_module.UpstreamError) as raised:
        upstream_module.post_upstream("deepseek", "deepseek-v4-pro", "https://api.deepseek.com/v1/x",
                                      {}, {}, timeout=0.3)

    assert raised.value.reason == "upstream_timeout"
    assert raised.value.message.startswith("upstream_timeout: ")
    assert raised.value.retriable is False


def test_a_socket_timeout_is_the_same_named_timeout(monkeypatch):
    def slow(*a, **k):
        raise upstream_module.requests.ReadTimeout("Read timed out.")

    monkeypatch.setattr(upstream_module.requests, "post", slow)

    with pytest.raises(upstream_module.UpstreamError) as raised:
        upstream_module.post_upstream("deepseek", "m", "https://api.deepseek.com/v1/x", {}, {}, timeout=1)

    assert raised.value.reason == "upstream_timeout"


def test_the_proxy_answers_a_timeout_with_its_reason(client, upstream, proxy, monkeypatch):
    monkeypatch.setattr(proxy, "LLM_UPSTREAM_TIMEOUT_SECONDS", 0.3)
    upstream.set("deepseek", FakeResponse(200, chunks=[b"\n"] * 100, delay=0.05))

    resp = _chat(client)

    assert resp.status_code == 502
    assert resp.get_json()["reason"] == "upstream_timeout"


def test_a_body_that_arrives_in_pieces_within_the_budget_is_read_whole(client, upstream):
    whole = openai_ok("in pieces").text.encode()
    upstream.set("deepseek", FakeResponse(200, chunks=[b"\n", b"\n", whole[:10], whole[10:]]))

    resp = _chat(client)

    assert resp.get_json()["choices"][0]["message"]["content"] == "in pieces"


# --- the field, per provider and policy -------------------------------------------

def test_only_deepseek_receives_the_thinking_field(client, upstream):
    upstream.set("qwen", openai_ok("from qwen"))

    _chat(client, model="qwen3.7-plus", thinking={"type": "enabled"})

    assert "thinking" not in upstream.calls[0]["json"]


def test_a_fallback_from_deepseek_drops_the_field(client, upstream, proxy, monkeypatch):
    monkeypatch.setattr(proxy, "_available_deepseek_models", {"deepseek-v4-pro"})
    upstream.set("deepseek", FakeResponse(503, {"message": "busy"}))
    upstream.set("qwen", openai_ok("from qwen"))

    _chat(client, thinking={"type": "enabled"})

    assert [c["provider"] for c in upstream.calls] == ["deepseek", "qwen"]
    assert "thinking" not in upstream.calls[1]["json"]


@pytest.mark.parametrize(("forced", "sent"), [("on", "enabled"), ("off", "disabled")])
def test_an_operator_can_force_the_mode(client, upstream, monkeypatch, forced, sent):
    monkeypatch.setenv("LLM_THINKING", forced)
    upstream.set("deepseek", openai_ok())

    _chat(client, thinking={"type": "disabled" if sent == "enabled" else "enabled"})

    assert upstream.calls[0]["json"]["thinking"] == {"type": sent}


def test_an_invalid_field_is_replaced_not_forwarded():
    sent = thinking.prepare("deepseek", {"thinking": {"type": "maybe"}}, env={})
    assert sent["thinking"] == {"type": "disabled"}


# --- the timeouts derive from one setting ----------------------------------------

def test_the_timeouts_derive_from_the_upstream_budget():
    assert timeouts.upstream_timeout_seconds({}) == 300.0
    assert timeouts.worker_timeout_seconds({}) == 330
    env = {"LLM_UPSTREAM_TIMEOUT_SECONDS": "200.5"}
    assert timeouts.upstream_timeout_seconds(env) == 200.5
    assert timeouts.worker_timeout_seconds(env) == 231
    for bad in ("", "soon", "0", "-3"):
        assert timeouts.upstream_timeout_seconds({"LLM_UPSTREAM_TIMEOUT_SECONDS": bad}) == 300.0


def test_gunicorn_reads_its_settings_from_the_environment(monkeypatch):
    monkeypatch.setenv("LLM_UPSTREAM_TIMEOUT_SECONDS", "120")
    monkeypatch.setenv("LLM_PROXY_THREADS", "4")
    path = Path(__file__).resolve().parents[1] / "gunicorn.conf.py"
    spec = importlib.util.spec_from_file_location("gunicorn_conf", path)
    conf = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(conf)

    assert conf.timeout == 150, "a worker outlives the proxy's own 120 s deadline"
    assert (conf.worker_class, conf.threads, conf.workers) == ("gthread", 4, 2)
    assert conf.bind == "0.0.0.0:5100"
