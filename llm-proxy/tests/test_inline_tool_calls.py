"""Tool calls DeepSeek writes as text are read back, or asked again without them.

The samples are what ``deepseek-v4-pro`` answered in sprint 3 of La Fabrique's
own run (tasks 12, 13 and 16): the calls inside ``content`` in its ``DSML``
markup, no ``tool_calls``. With tools offered they come back as
``tool_calls``; without tools the request is asked once more, and a second
such answer is a named, retriable error.
"""

import json

import inline_tool_calls
from conftest import FakeResponse
from test_tools import TOOLS

SEARCH_AND_READ = (
    "I need to find where it appears. Let me search.\n\n"
    "<｜｜DSML｜｜tool_calls>\n"
    '<｜｜DSML｜｜invoke name="workspace__search">\n'
    '<｜｜DSML｜｜parameter name="path" string="true">frontend/src</｜｜DSML｜｜parameter>\n'
    '<｜｜DSML｜｜parameter name="pattern" string="true">approved|approval</｜｜DSML｜｜parameter>\n'
    "</｜｜DSML｜｜invoke>\n"
    '<｜｜DSML｜｜invoke name="workspace__read">\n'
    '<｜｜DSML｜｜parameter name="path" string="true">frontend/src/components/LearningTrend.tsx</｜｜DSML｜｜parameter>\n'
    '<｜｜DSML｜｜parameter name="start_line" string="false">144</｜｜DSML｜｜parameter>\n'
    "</｜｜DSML｜｜invoke>\n"
    "</｜｜DSML｜｜tool_calls>"
)
INVENTED_TOOL = (
    "<｜DSML｜tool_calls>\n"
    '<｜DSML｜invoke name="Read">\n'
    '<｜DSML｜parameter name="file" string="true">backend/app/services/platform/sso_domains.py</｜DSML｜parameter>\n'
    "</｜DSML｜invoke>\n"
    "</｜DSML｜tool_calls>"
)


def _deepseek(content):
    return FakeResponse(
        200,
        {"choices": [{"message": {"role": "assistant", "content": content}, "finish_reason": "stop"}],
         "usage": {"total_tokens": 40}},
    )


def _chat(client, **extra):
    payload = {"model": "deepseek-v4-pro", "messages": [{"role": "user", "content": "do it"}], **extra}
    return client.post("/v1/chat/completions", json=payload)


def test_the_markup_parses_into_openai_calls_and_the_prose_before_them():
    prose, calls = inline_tool_calls.parse(SEARCH_AND_READ)
    assert prose == "I need to find where it appears. Let me search."
    assert [c["function"]["name"] for c in calls] == ["workspace__search", "workspace__read"]
    assert json.loads(calls[0]["function"]["arguments"]) == {"path": "frontend/src", "pattern": "approved|approval"}
    # string="false" is a JSON value, string="true" stays text.
    assert json.loads(calls[1]["function"]["arguments"])["start_line"] == 144
    assert len({c["id"] for c in calls}) == 2


def test_a_cut_answer_keeps_the_complete_calls_and_no_markup_in_the_prose():
    cut = SEARCH_AND_READ[: SEARCH_AND_READ.index('<｜｜DSML｜｜parameter name="start_line"')]
    prose, calls = inline_tool_calls.parse(cut)
    assert "DSML" not in prose
    assert [c["function"]["name"] for c in calls] == ["workspace__search", "workspace__read"]


def test_plain_text_is_left_alone(client, upstream):
    assert not inline_tool_calls.has_markup("a <tool_calls> word in prose")
    upstream.set("deepseek", _deepseek("plain answer"))
    body = _chat(client, tools=TOOLS).get_json()
    assert body["choices"][0]["message"]["content"] == "plain answer"
    assert "inline_tool_calls" not in body


def test_with_tools_offered_the_written_calls_come_back_as_tool_calls(client, upstream):
    upstream.set("deepseek", _deepseek(SEARCH_AND_READ))
    resp = _chat(client, tools=TOOLS)
    assert resp.status_code == 200
    body = resp.get_json()
    choice = body["choices"][0]
    assert choice["finish_reason"] == "tool_calls"
    assert [c["function"]["name"] for c in choice["message"]["tool_calls"]] == [
        "workspace__search", "workspace__read"]
    assert "DSML" not in (choice["message"]["content"] or "")
    assert body["inline_tool_calls"] == {"format": "dsml", "calls": 2}
    assert len(upstream.calls) == 1


def test_without_tools_the_call_is_asked_once_more_with_the_note(client, upstream):
    upstream.set("deepseek", [_deepseek(INVENTED_TOOL), _deepseek("The complete answer.")])
    resp = _chat(client)
    assert resp.status_code == 200
    body = resp.get_json()
    assert body["choices"][0]["message"]["content"] == "The complete answer."
    assert body["inline_tool_calls_retry"]["reason"] == inline_tool_calls.INLINE_TOOL_CALLS_WITHOUT_TOOLS
    second = upstream.calls[1]["json"]["messages"]
    assert second[-1] == {"role": "user", "content": inline_tool_calls.NO_TOOLS_NOTE}
    assert "tools" not in upstream.calls[1]["json"]


def test_a_second_answer_with_written_calls_is_a_named_retriable_error(client, upstream):
    for provider in ("qwen", "gemini"):
        upstream.set(provider, FakeResponse(503, {"message": "down"}))
    upstream.set("deepseek", _deepseek(INVENTED_TOOL))
    resp = _chat(client)
    assert resp.status_code == 502
    attempts = resp.get_json()["attempts"]
    assert attempts[0]["reason"] == inline_tool_calls.INLINE_TOOL_CALLS_WITHOUT_TOOLS
    # Retriable: the fallback model was tried too.
    assert len(attempts) > 1
