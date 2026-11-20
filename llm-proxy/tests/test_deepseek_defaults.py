"""
The default team's two DeepSeek models through the proxy.

The backend's role defaults (``services/models/model_factory.py``) name
``deepseek-v4-pro`` and ``deepseek-flash``. Both must pass the proxy's check
(prefix and live catalogue) and reach DeepSeek with the caller's body. The
``thinking`` field is always explicit (``thinking.py``): a caller's valid
setting is forwarded unchanged, and a request without one gets ``disabled``
rather than DeepSeek's own default (thinking on,
https://api-docs.deepseek.com/guides/thinking_mode). The thinking tests are
in ``test_thinking.py``.
"""

import catalogue
import pytest
from conftest import openai_ok


def _chat(client, model, **extra):
    payload = {"model": model, "messages": [{"role": "user", "content": "hi"}], **extra}
    return client.post("/v1/chat/completions", json=payload)


@pytest.mark.parametrize("model", ["deepseek-v4-pro", "deepseek-flash"])
def test_a_default_model_reaches_deepseek_under_its_own_name(client, upstream, model):
    upstream.set("deepseek", openai_ok())

    resp = _chat(client, model)

    assert resp.status_code == 200
    assert upstream.providers_called() == ["deepseek"]
    assert upstream.calls[0]["json"]["model"] == model
    assert resp.get_json().get("served_model") == model


def test_a_request_without_thinking_never_gets_deepseeks_default(client, upstream):
    upstream.set("deepseek", openai_ok())

    _chat(client, "deepseek-flash")

    assert upstream.calls[0]["json"]["thinking"] == {"type": "disabled"}


def test_a_callers_thinking_setting_is_forwarded_unchanged(client, upstream):
    upstream.set("deepseek", openai_ok())

    _chat(client, "deepseek-flash", thinking={"type": "disabled"})

    assert upstream.calls[0]["json"]["thinking"] == {"type": "disabled"}


def test_a_model_missing_from_the_live_list_is_refused_not_substituted(client, upstream, proxy, monkeypatch):
    monkeypatch.setattr(proxy, "_available_deepseek_models", {"deepseek-v4-pro"})

    resp = _chat(client, "deepseek-flash")

    assert resp.status_code == 400
    assert resp.get_json()["error"] == "unknown_model"
    assert upstream.calls == []


def test_the_orchestrator_is_always_deepseek_flash():
    models = [
        {"id": "gemini-3.8-flash", "tier": "balanced"},
        {"id": "deepseek-v4-pro", "tier": "balanced"},
        {"id": "deepseek-flash", "tier": "fast"},
    ]
    assert catalogue.pick_orchestrator(models) == "deepseek-flash"
    # Without DeepSeek Flash in the catalogue, or with no catalogue at all, it
    # is still recommended rather than another provider's model.
    assert catalogue.pick_orchestrator(models[:2]) == "deepseek-flash"
    assert catalogue.pick_orchestrator(models[:1]) == "deepseek-flash"
    assert catalogue.pick_orchestrator([]) == "deepseek-flash"
