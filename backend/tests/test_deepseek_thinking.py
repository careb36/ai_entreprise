"""DeepSeek's thinking mode is chosen per call, and an empty answer is a named error.

Every call names ``thinking`` (``services/models/thinking.py``): structured
calls run without it, the reasoning roles' free-form calls with it and room
for the reasoning. When the proxy answers with a named error (an empty
answer whose reasoning used the budget, an upstream timeout), the planner
and the QA review carry that reason on instead of an empty string.
"""

from __future__ import annotations

import json

import llm_proxy_client
import pytest
from services.loop.worker import execute, qa_review
from services.models import thinking
from services.models.model_factory import AgentRole, ModelFactory
from services.planning import sprint_planner


@pytest.fixture(autouse=True)
def _policy_defaults(monkeypatch):
    monkeypatch.delenv("LLM_THINKING", raising=False)
    monkeypatch.delenv("LLM_THINKING_RESERVE_TOKENS", raising=False)


@pytest.mark.parametrize(
    ("role", "structured", "mode"),
    [
        ("Architect", False, "enabled"),
        ("PO", False, "enabled"),
        ("Backend Developer", False, "enabled"),
        ("QA", False, "disabled"),
        ("ScrumMaster", False, "disabled"),
        ("Reviewer", False, "disabled"),
        ("EngramProcessor", False, "disabled"),
        ("Manager", False, "disabled"),
        ("PO", True, "disabled"),
        ("Architect", True, "disabled"),
    ],
)
def test_the_mode_of_a_call(role, structured, mode):
    assert thinking.mode_for(role, structured=structured) == mode


@pytest.mark.parametrize(("value", "mode"), [("on", "enabled"), ("off", "disabled")])
def test_an_operator_may_force_every_call(monkeypatch, value, mode):
    monkeypatch.setenv("LLM_THINKING", value)
    assert thinking.mode_for("QA", structured=True) == mode
    assert thinking.mode_for("Architect", structured=False) == mode


def test_auto_and_unknown_values_follow_the_policy(monkeypatch):
    for value in ("auto", "", "sometimes"):
        monkeypatch.setenv("LLM_THINKING", value)
        assert thinking.mode_for("PO", structured=True) == "disabled"
        assert thinking.mode_for("PO", structured=False) == "enabled"


def test_a_thinking_call_gets_room_for_its_reasoning(monkeypatch):
    assert thinking.max_tokens_for("enabled", 4096) == 4096 + 16384
    assert thinking.max_tokens_for("disabled", 4096) == 4096
    assert thinking.max_tokens_for("enabled", None) is None, (
        "the provider's own default"
    )
    monkeypatch.setenv("LLM_THINKING_RESERVE_TOKENS", "2000")
    assert thinking.max_tokens_for("enabled", 600) == 2600
    monkeypatch.setenv("LLM_THINKING_RESERVE_TOKENS", "-5")
    assert thinking.reasoning_reserve() == thinking.DEFAULT_REASONING_RESERVE_TOKENS


def test_the_langchain_clients_name_their_thinking_mode(monkeypatch):
    monkeypatch.setattr(ModelFactory, "_model_cache", {})
    engram = ModelFactory.get_model(AgentRole.ENGRAM_PROCESSOR)
    architect = ModelFactory.get_model(AgentRole.ARCHITECT)

    assert engram.extra_body == {"thinking": {"type": "disabled"}}
    assert engram.max_tokens == 2048
    assert architect.extra_body == {"thinking": {"type": "enabled"}}
    assert architect.max_tokens == 4096 + 16384


class _ProxyAnswer:
    """What the proxy answers when DeepSeek's reasoning used the whole budget."""

    status_code = 502
    ok = False

    def __init__(self, reason: str, message: str):
        self._body = {
            "error": "upstream_error",
            "provider": "deepseek",
            "model": "deepseek-v4-pro",
            "status": 200,
            "reason": reason,
            "message": message,
        }
        self.text = json.dumps(self._body)

    def json(self):
        return self._body


EXHAUSTED = _ProxyAnswer(
    "empty_content_reasoning_exhausted",
    "empty_content_reasoning_exhausted: finish_reason=length; the reasoning used the whole max_tokens",
)


@pytest.fixture()
def proxy_posts(monkeypatch):
    sent: list[dict] = []

    def post(url, json=None, headers=None, timeout=None):
        sent.append({"json": json, "headers": headers})
        return EXHAUSTED

    monkeypatch.setattr(llm_proxy_client.requests, "post", post)
    return sent


def test_the_planner_records_the_named_reason_of_its_fallback(proxy_posts):
    agents = [{"role": r, "name": r, "model": "deepseek-v4-pro"} for r in ("PO", "Dev")]

    plan = sprint_planner.plan_execution_sprint("Acme", "Ship docs", "S", agents)

    assert plan["tasks"], "the deterministic plan stands in"
    fallback = plan["planner_fallback"]
    assert fallback["reason"] == "model_error"
    assert fallback["detail"].startswith(
        "empty_content_reasoning_exhausted: finish_reason=length"
    )
    assert proxy_posts[0]["json"]["thinking"] == {"type": "disabled"}
    assert proxy_posts[0]["headers"]["X-LLM-Purpose"] == "structured"


def test_an_empty_2xx_plan_is_named_not_parsed(monkeypatch):
    class _Empty:
        def chat(self, **_):
            return {"choices": [{"message": {"content": ""}, "finish_reason": "stop"}]}

    monkeypatch.setattr(sprint_planner, "_client", lambda po: _Empty())
    agents = [
        {"role": "PO", "name": "PO", "model": "m"},
        {"role": "Dev", "name": "D", "model": "m"},
    ]

    plan = sprint_planner.plan_execution_sprint("Acme", "Ship docs", "S", agents)

    assert plan["planner_fallback"] == {
        "reason": "model_error",
        "detail": "empty_content: the answer had no text (finish_reason=stop)",
    }


def test_an_unparseable_plan_names_the_parse_error(monkeypatch):
    class _Prose:
        def chat(self, **_):
            return {"choices": [{"message": {"content": "Here is my plan: ..."}}]}

    monkeypatch.setattr(sprint_planner, "_client", lambda po: _Prose())
    agents = [
        {"role": "PO", "name": "PO", "model": "m"},
        {"role": "Dev", "name": "D", "model": "m"},
    ]

    plan = sprint_planner.plan_execution_sprint("Acme", "Ship docs", "S", agents)

    assert plan["planner_fallback"]["reason"] == "unparseable"
    assert plan["planner_fallback"]["detail"].startswith(
        "JSONDecodeError: Expecting value"
    )
    assert plan["planner_fallback"]["detail"].endswith("(answer of 20 characters)")


def test_the_foundation_plan_records_its_fallback_the_same_way(proxy_posts):
    agents = [{"role": r, "name": r, "model": "deepseek-v4-pro"} for r in ("PO", "Dev")]

    plan = sprint_planner.plan_foundation_sprint(
        "Acme", agents, {"language": "python"}, None
    )

    assert plan["tasks"]
    assert plan["planner_fallback"]["reason"] == "model_error"
    assert plan["planner_fallback"]["detail"].startswith(
        "empty_content_reasoning_exhausted: "
    )


def test_the_qa_review_fails_with_the_named_reason(proxy_posts):
    with pytest.raises(execute.LLMCallError) as raised:
        qa_review._call_llm_for_review(
            {"role": "QA", "model": "deepseek-flash"}, "Review"
        )

    assert "empty_content_reasoning_exhausted" in str(raised.value)
    assert proxy_posts[0]["json"]["thinking"] == {"type": "disabled"}
