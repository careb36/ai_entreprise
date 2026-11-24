"""QA reads the delivery before its verdict (``qa_review_reads``, ``review_tools``).

The review turn gets the read-only workspace tools on the folder the delivery
was written to, for a few rounds; the verdict keeps today's form and parser;
without a recorded delivery or a daemon it is today's single call.
"""

from __future__ import annotations

import json
from types import SimpleNamespace
from typing import ClassVar

import pytest
from config import settings
from services.loop.worker import qa_review, qa_review_reads
from services.tools import developer_tools, review_tools

QA = {"role": "QA", "name": "Quinn", "model": "deepseek-v4-pro"}
READ_ONLY = {
    "workspace__list",
    "workspace__read",
    "workspace__search",
    "workspace__find_file",
}


def _answer(content=None, calls=None):
    message = {"role": "assistant", "content": content}
    if calls:
        message["tool_calls"] = calls
    return {"choices": [{"message": message, "finish_reason": "stop"}]}


def _call(name, arguments, call_id="c1"):
    return {
        "id": call_id,
        "type": "function",
        "function": {"name": name, "arguments": json.dumps(arguments)},
    }


class FakeClient:
    """The proxy client: each ``chat`` returns the next scripted answer."""

    answers: ClassVar[list[dict]] = []
    sent: ClassVar[list[dict]] = []

    def __init__(self, url):
        self.max_tokens, self.company_id, self.structured = None, None, False

    def chat(self, messages, model, role, tools=None, **billing):
        FakeClient.sent.append(
            {
                "messages": [dict(m) for m in messages],
                "tools": tools,
                "structured": self.structured,
                "max_tokens": self.max_tokens,
            }
        )
        return FakeClient.answers.pop(0)


def _reader(subject, payload, timeout):
    assert subject == "workspace.read", subject
    return {
        "path": payload["path"],
        "text": "def total(xs):\n    return sum(xs)",
        "lines": 2,
        "first_line": 1,
        "last_line": 2,
        "sha256": "ab" * 32,
        "bytes": 34,
    }


@pytest.fixture
def client(monkeypatch):
    FakeClient.answers, FakeClient.sent = [], []
    monkeypatch.setattr(qa_review, "LLMProxyClient", FakeClient)
    turns: list[dict] = []

    def record(**kw):
        turns.append(kw)

    monkeypatch.setattr(qa_review, "record_agent_turn", record)
    monkeypatch.setattr(qa_review_reads, "record_agent_turn", record)
    FakeClient.turns = turns
    return FakeClient


@pytest.fixture
def with_tools(monkeypatch):
    """A recorded delivery and a daemon that answers reads."""

    def toolbox(company_id, item_id, *, engine, rounds):
        box = developer_tools.WorkspaceToolbox(_reader, "/work/task-7", can_edit=False)
        box.max_rounds = rounds
        return box

    monkeypatch.setattr(review_tools, "read_only_toolbox", toolbox)


def _review(**ids):
    return qa_review._call_llm_for_review(
        {**QA, "system_prompt": "You review."}, "Review this delivery.", **ids
    )


def test_the_tools_offered_are_read_only_on_the_deliverys_root(monkeypatch):
    monkeypatch.setitem(developer_tools._bridge, "nats", object())
    verification = SimpleNamespace(
        evidence={"git": {"branch": "task-7"}, "workspace_root": "/work/task-7"}
    )
    from services.evidence import delivery_gate

    monkeypatch.setattr(
        delivery_gate, "latest_verification", lambda engine, c, i: verification
    )
    monkeypatch.setattr(settings, "DELIVERY_TESTS_ENABLED", True)

    box = review_tools.read_only_toolbox(3, 7, engine=None, rounds=3)

    assert box.workspace_root == "/work/task-7"
    assert box.max_rounds == 3
    assert {s["function"]["name"] for s in box.schemas()} == READ_ONLY
    for refused in ("workspace__edit", "workspace__run_tests"):
        observation, _ = box.call(_call(refused, {"path": "a.py"}), 1)
        assert observation["is_error"] and observation["reason"] == "unknown tool"


def test_a_read_is_answered_and_recorded_on_the_verdicts_turn(client, with_tools):
    client.answers = [
        _answer(calls=[_call("workspace__read", {"path": "app/calc.py"})]),
        _answer("APPROVED: total sums the list, as the criteria ask."),
    ]

    verdict = _review(company_id=3, sprint_id=5, backlog_item_id=7)

    assert verdict.startswith("APPROVED:")
    first, second = client.sent
    assert {t["function"]["name"] for t in first["tools"]} == READ_ONLY
    assert first["structured"] and first["max_tokens"] == qa_review.REVIEW_MAX_TOKENS
    tool_reply = second["messages"][-1]
    assert tool_reply["role"] == "tool" and "return sum(xs)" in tool_reply["content"]
    reading, final = client.turns
    assert reading["action"] == qa_review_reads.ACTION_QA_REVIEW_READ
    assert final["action"] == qa_review.ACTION_QA_REVIEW
    assert final["result_type"] == "APPROVED"
    (observation,) = final["tool_calls"]
    assert observation["tool"] == "read" and observation["read_only"]
    assert observation["path"] == "app/calc.py"


def test_an_answer_without_a_verdict_falls_back_to_todays_call(client, with_tools):
    client.answers = [
        _answer(calls=[_call("workspace__read", {"path": "app/calc.py"})]),
        _answer("I read the file."),
        _answer("NEEDS WORK: the empty list is not handled."),
    ]

    verdict = _review(company_id=3, sprint_id=5, backlog_item_id=7)

    assert qa_review.classify_review_result(verdict) == "NEEDS_WORK"
    last = client.sent[-1]
    assert last["tools"] is None
    assert last["messages"] == [
        {"role": "system", "content": "You review."},
        {"role": "user", "content": "Review this delivery."},
    ]
    assert client.turns[-1]["tool_calls"][0]["tool"] == "read"


def test_spent_rounds_ask_for_the_verdict_without_tools(
    client, with_tools, monkeypatch
):
    monkeypatch.setattr(settings, "QA_REVIEW_TOOL_ROUNDS", 1)
    client.answers = [
        _answer(calls=[_call("workspace__read", {"path": "app/calc.py"})]),
        _answer("APPROVED: read and checked."),
    ]

    assert _review(company_id=3, sprint_id=5, backlog_item_id=7).startswith("APPROVED:")
    last = client.sent[-1]
    assert last["tools"] is None
    assert last["messages"][-1]["content"] == qa_review_reads.EXHAUSTED_NOTE


def test_without_a_tool_root_the_review_is_todays_single_call(client, monkeypatch):
    monkeypatch.setattr(developer_tools, "_bridge", {})  # No daemon: no toolbox.
    client.answers = [_answer("APPROVED: fine.")]

    assert _review(company_id=3, sprint_id=5, backlog_item_id=7) == "APPROVED: fine."
    (only,) = client.sent
    assert only["tools"] is None
    assert only["messages"] == [
        {"role": "system", "content": "You review."},
        {"role": "user", "content": "Review this delivery."},
    ]
    assert "tool_calls" not in client.turns[0] or not client.turns[0]["tool_calls"]


def test_no_recorded_delivery_means_no_tools(monkeypatch):
    monkeypatch.setitem(developer_tools._bridge, "nats", object())
    from services.evidence import delivery_gate

    monkeypatch.setattr(delivery_gate, "latest_verification", lambda e, c, i: None)

    assert review_tools.read_only_toolbox(3, 7, engine=None, rounds=3) is None
    assert review_tools.read_only_toolbox(3, 7, engine=None, rounds=0) is None
