"""What the tool loop records about itself (issue #766, an audit of 108 runs):
a failed call keeps the start of its error text (``error``, redacted), the
run's evidence says how many rounds the loop used and whether it reached its
limit, a final answer that is only a tool call typed out as text is no
deliverable, and a fetch the host allowlist refused says so to the model and
on the observation."""

from __future__ import annotations

import json

import pytest
from services.loop import answers
from services.loop.worker import execute as worker_execute
from services.tools import developer_tools, mcp_tools, research_tools, workspace_edits

MARKUP = '<tool_calls><invoke name="workspace__read">src/a.py</invoke></tool_calls>'


def _call(name, **arguments):
    return {
        "id": f"call_{name}",
        "type": "function",
        "function": {"name": name, "arguments": json.dumps(arguments)},
    }


def _with_calls(*calls):
    message = {"role": "assistant", "content": None, "tool_calls": list(calls)}
    return {"choices": [{"message": message}]}


def _answer(text):
    message = {"role": "assistant", "content": text}
    return {"choices": [{"message": message}], "served_model": "m"}


class _Model:
    """A fake proxy: answers in order (the last one repeated), requests kept."""

    def __init__(self, answers_):
        self.answers = list(answers_)
        self.requests = []

    def chat(self, messages, model, role, bu_id=None, tools=None):
        self.requests.append({"tools": tools})
        return self.answers.pop(0) if len(self.answers) > 1 else self.answers[0]


def _research(http=None, hosts=None):
    def refuse_all(*_a, **_k):
        raise AssertionError("nothing may be sent")

    return research_tools.ResearchToolbox(
        hosts=research_tools.allowed_hosts(hosts or []), http=http or refuse_all
    )


# -- (1) a failed call keeps its error text -----------------------------------


def test_a_failed_workspace_call_records_the_daemon_s_error_text():
    def requester(subject, payload, timeout):
        raise developer_tools.DaemonReplyError(
            "no such folder: docs/missing\npassword = 'hunter2hunter2'"
        )

    box = developer_tools.WorkspaceToolbox(requester, "/project")
    model = _Model(
        [_with_calls(_call("workspace__list", path="docs/missing")), _answer("ok")]
    )
    _result, observations = mcp_tools.chat_with_tools(model, [], "m", "Developer", box)
    observation = observations[0]
    assert observation["is_error"] is True
    assert observation["error"].startswith("Error: no such folder: docs/missing")
    # Secret-looking lines are redacted, as in a read.
    assert "hunter2" not in observation["error"]
    assert "[line redacted" in observation["error"]


def test_the_error_text_is_cut_and_a_success_carries_none():
    def requester(subject, payload, timeout):
        if subject == "workspace.list":
            raise developer_tools.DaemonReplyError("x" * 2000)
        return {
            "path": "a.py",
            "text": "a = 1\n",
            "lines": 1,
            "sha256": "s",
            "bytes": 6,
        }

    box = developer_tools.WorkspaceToolbox(requester, "/project")
    model = _Model(
        [
            _with_calls(
                _call("workspace__list"), _call("workspace__read", path="a.py")
            ),
            _answer("ok"),
        ]
    )
    _result, (failed, read) = mcp_tools.chat_with_tools(
        model, [], "m", "Developer", box
    )
    assert len(failed["error"]) == mcp_tools.ERROR_EXCERPT_CHARS
    assert read["is_error"] is False and "error" not in read


def test_a_failed_research_call_records_its_error_text():
    url = "https://docs.python.org/3/x.html"

    def http(method, url_, body=None, timeout=20.0, allow_private=False):
        return 503, {}, b"down"

    model = _Model([_with_calls(_call("research__fetch", url=url)), _answer("ok")])
    _result, observations = mcp_tools.chat_with_tools(
        model, [], "m", "Architect", _research(http)
    )
    assert observations[0]["error"] == "Error: The page answered 503."


# -- (2) the rounds the loop used ---------------------------------------------


def test_a_loop_that_ends_early_records_its_rounds_not_exhausted():
    model = _Model(
        [
            _with_calls(_call("research__fetch", url="https://x.invalid/")),
            _answer("done"),
        ]
    )
    result, _obs = mcp_tools.chat_with_tools(
        model, [], "m", "Architect", _research(), max_rounds=4
    )
    assert result["tool_loop"] == {
        "rounds_used": 1,
        "rounds_max": 4,
        "rounds_exhausted": False,
    }


def test_a_loop_that_reaches_its_limit_records_it_exhausted():
    model = _Model([_with_calls(_call("research__fetch", url="https://x.invalid/"))])
    result, observations = mcp_tools.chat_with_tools(
        model, [], "m", "Architect", _research(), max_rounds=2
    )
    assert len(observations) == 2
    assert result["tool_loop"]["rounds_used"] == 2
    assert result["tool_loop"]["rounds_exhausted"] is True


def test_the_rounds_go_on_the_run_s_evidence():
    agent = {
        "tool_calls": [],
        "tool_loop": {"rounds_used": 3, "rounds_max": 3, "rounds_exhausted": True},
    }
    evidence = workspace_edits.observe_tools({}, agent)
    assert (
        evidence["rounds_used"],
        evidence["rounds_max"],
        evidence["rounds_exhausted"],
    ) == (3, 3, True)
    early = {
        "tool_loop": {"rounds_used": 0, "rounds_max": 3, "rounds_exhausted": False}
    }
    assert workspace_edits.observe_tools({}, early)["rounds_exhausted"] is False
    # A run without the tool loop records nothing about rounds.
    assert "rounds_used" not in (workspace_edits.observe_tools({}, {}) or {})


# -- (3) a tool call written as text is no deliverable --------------------------


def test_a_tool_call_written_as_text_after_the_limit_is_not_a_deliverable():
    model = _Model(
        [
            _with_calls(_call("research__fetch", url="https://x.invalid/")),
            _with_calls(_call("research__fetch", url="https://x.invalid/")),
            _answer(MARKUP),
        ]
    )
    result, _obs = mcp_tools.chat_with_tools(
        model, [], "m", "Developer", _research(), max_rounds=2
    )
    assert model.requests[-1]["tools"] is None
    reason = result["tool_loop"]["not_a_deliverable"]
    assert reason == (
        "the model's last answer was a tool call written as text; "
        "the tool budget had run out"
    )
    assert answers.llm_failure(result, "m") == f"No deliverable from m: {reason}"


def test_a_real_deliverable_after_the_limit_stays_one():
    deliverable = "```python path=src/a.py\nprint('a')\n```"
    model = _Model(
        [
            _with_calls(_call("research__fetch", url="https://x.invalid/")),
            _answer(deliverable),
        ]
    )
    result, _obs = mcp_tools.chat_with_tools(
        model, [], "m", "Developer", _research(), max_rounds=1
    )
    assert "not_a_deliverable" not in result["tool_loop"]
    assert answers.llm_failure(result, "m") is None


def test_the_worker_does_not_deliver_a_tool_call_written_as_text(monkeypatch):
    """``_execute_task`` takes it like an empty answer: an ``LLMCallError``
    with the reason, which the worker puts on the board."""
    box = _research()
    box.max_rounds = 1
    monkeypatch.setattr(developer_tools, "toolbox_for", lambda *a, **k: box)
    model = _Model(
        [
            _with_calls(_call("research__fetch", url="https://x.invalid/")),
            _answer(MARKUP),
        ]
    )
    monkeypatch.setattr(worker_execute, "LLMProxyClient", lambda base_url=None: model)
    agent = {"role": "Developer", "model": "m"}
    with pytest.raises(worker_execute.LLMCallError) as raised:
        worker_execute._execute_task(
            {"id": 7, "title": "Login", "description": "x"}, agent, "S1"
        )
    assert "tool call written as text" in str(raised.value)
    assert "the tool budget had run out" in str(raised.value)
    assert agent["tool_loop"]["rounds_exhausted"] is True


# -- (4) a fetch the allowlist refused says so ---------------------------------


def test_a_fetch_to_a_host_off_the_list_is_refused_and_says_so():
    model = _Model(
        [
            _with_calls(_call("research__fetch", url="https://evil.example/x")),
            _answer("ok"),
        ]
    )
    _result, observations = mcp_tools.chat_with_tools(
        model, [], "m", "Architect", _research()
    )
    observation = observations[0]
    assert observation["refused"] == "host not allowed"
    assert observation["allowed"] is False and observation["is_error"] is True
    assert observation["error"].startswith("Error: Refused: host not allowed")
    assert "'evil.example' is not on the company's list" in observation["error"]


def test_plain_http_to_a_listed_host_is_refused_for_its_scheme():
    box = _research()
    observation, text = box.call(
        _call("research__fetch", url="http://docs.python.org/3/"), 1
    )
    assert observation["refused"] == "host not allowed"
    assert "Refused: host not allowed" in text and "https only" in text
