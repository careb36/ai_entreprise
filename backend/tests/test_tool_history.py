"""Compressed history in the tool loop (issue #359, A.5): results older than
the last ``TOOL_HISTORY_KEEP_ROUNDS`` rounds reach the model as one line
each (``services/tool_history.py``), while the observations on the evidence
stay whole.

The daemon's read functions run for real against ``tmp_path``; the model is
a fake that records every request.
"""

from __future__ import annotations

import importlib.util
import json
from pathlib import Path

import pytest
import workspace_read  # the daemon's module, on sys.path via conftest
from config import settings
from services.tools import developer_tools, mcp_tools, tool_history

_DAEMON_PATH = Path(__file__).resolve().parents[2] / "vscode-daemon" / "daemon.py"


def _load_daemon():
    spec = importlib.util.spec_from_file_location("fabrique_daemon_hist", _DAEMON_PATH)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


@pytest.fixture()
def project(tmp_path):
    root = tmp_path / "proj"
    (root / "src").mkdir(parents=True)
    for n in range(1, 6):
        (root / "src" / f"m{n}.py").write_text(
            "".join(f"value_{n}_{i} = {i}\n" for i in range(150))
        )
    return root


class _Requester:
    def __init__(self, daemon):
        self.daemon = daemon

    def __call__(self, subject, payload, timeout):
        root, resolve = payload["workspace_root"], self.daemon._resolve_target_path
        if subject == "workspace.read":
            return workspace_read.read_file(
                root,
                payload.get("path"),
                payload.get("start_line"),
                resolve,
                payload.get("lines"),
            )
        if subject == "workspace.search":
            return workspace_read.search(
                root, payload.get("pattern"), payload.get("path"), resolve
            )
        raise AssertionError(subject)


class _Model:
    def __init__(self, answers):
        self.answers = list(answers)
        self.requests = []

    def chat(self, messages, model, role, bu_id=None, tools=None):
        self.requests.append({"messages": list(messages), "tools": tools})
        return self.answers.pop(0)


def _calls(*calls):
    return {
        "choices": [
            {
                "message": {
                    "role": "assistant",
                    "content": None,
                    "tool_calls": list(calls),
                }
            }
        ]
    }


def _call(n, name, **arguments):
    return {
        "id": f"c{n}",
        "type": "function",
        "function": {"name": name, "arguments": json.dumps(arguments)},
    }


def _final(text="Done."):
    return {"choices": [{"message": {"role": "assistant", "content": text}}]}


def _tool_messages(request):
    return [m for m in request["messages"] if m.get("role") == "tool"]


def test_old_results_go_to_the_model_as_one_line_and_the_evidence_stays_whole(
    project, monkeypatch
):
    monkeypatch.setattr(settings, "TOOL_HISTORY_KEEP_ROUNDS", 2)
    # A budget nothing fits: old reads are summarized too.
    monkeypatch.setattr(settings, "TOOL_HISTORY_MAX_CHARS", 1)
    box = developer_tools.WorkspaceToolbox(_Requester(_load_daemon()), str(project))
    model = _Model(
        [
            _calls(_call(1, "workspace__read", path="src/m1.py")),
            _calls(_call(2, "workspace__search", pattern="value_2_77 ")),
            _calls(_call(3, "workspace__read", path="src/m3.py")),
            _calls(_call(4, "workspace__read", path="src/m4.py")),
            _final(),
        ]
    )
    result, observations = mcp_tools.chat_with_tools(
        model, [{"role": "user", "content": "go"}], "m", "Developer", box
    )
    assert result["choices"][0]["message"]["content"] == "Done."
    # Round 3 sees rounds 1 and 2 whole; round 4 sees round 1 summarized.
    assert all(
        not m["content"].startswith(tool_history.PREFIX)
        for m in _tool_messages(model.requests[2])
    )
    round4 = _tool_messages(model.requests[3])
    first = observations[0]
    assert round4[0]["content"] == (
        tool_history.PREFIX
        + f"read src/m1.py lines 1–150 of 150 (sha256 {first['sha256'][:12]}…); "
        "workspace__read path=src/m1.py start_line=1 lines=150 shows it again"
    )
    assert "src/m2.py:78: value_2_77 = 77" in round4[1]["content"]
    # The final request: round 2 is old too, but its result is shorter than
    # its line would be, so it stays; the last two rounds are whole.
    final = _tool_messages(model.requests[4])
    assert final[0]["content"] == round4[0]["content"]
    assert final[1]["content"] == "src/m2.py:78: value_2_77 = 77"
    assert "value_3_0" in final[2]["content"] and "value_4_0" in final[3]["content"]
    # The calls themselves (the assistant messages) are never changed.
    assistants = [
        m for m in model.requests[4]["messages"] if m.get("role") == "assistant"
    ]
    assert [m["tool_calls"][0]["id"] for m in assistants] == ["c1", "c2", "c3", "c4"]
    # The evidence keeps every observation as the call made it.
    assert [o["tool"] for o in observations] == ["read", "search", "read", "read"]
    assert observations[0]["result_chars"] > len(round4[0]["content"])
    assert observations[0]["sha256"] and observations[0]["last_line"] == 150


def test_with_zero_every_result_stays_whole_and_the_exhausted_loop_compresses_too(
    project, monkeypatch
):
    box = developer_tools.WorkspaceToolbox(_Requester(_load_daemon()), str(project))
    box.max_rounds = 3
    answers = [
        _calls(_call(n, "workspace__read", path=f"src/m{n}.py")) for n in (1, 2, 3)
    ] + [_final()]
    monkeypatch.setattr(settings, "TOOL_HISTORY_KEEP_ROUNDS", 0)
    model = _Model(list(answers))
    mcp_tools.chat_with_tools(
        model, [{"role": "user", "content": "go"}], "m", "QA", box
    )
    assert all(
        not m["content"].startswith(tool_history.PREFIX)
        for m in _tool_messages(model.requests[-1])
    )
    monkeypatch.setattr(settings, "TOOL_HISTORY_KEEP_ROUNDS", 1)
    monkeypatch.setattr(settings, "TOOL_HISTORY_MAX_CHARS", 1)
    model = _Model(list(answers))
    mcp_tools.chat_with_tools(
        model, [{"role": "user", "content": "go"}], "m", "QA", box
    )
    last = model.requests[-1]
    assert last["tools"] is None
    assert last["messages"][-1]["content"] == mcp_tools.ROUNDS_EXHAUSTED_NOTE
    summarized = [
        m["content"].startswith(tool_history.PREFIX) for m in _tool_messages(last)
    ]
    assert summarized == [True, True, False]


def test_each_kind_of_call_has_its_line():
    line = tool_history.summary_line
    assert (
        line(
            {
                "server": "workspace",
                "tool": "edit",
                "path": "a.py",
                "sha256_before": "a" * 64,
                "sha256_after": "b" * 64,
                "lint": {"ran": True, "exit_code": 1},
            }
        )
        == "edited a.py (sha256 aaaaaaaaaaaa…) → bbbbbbbbbbbb…; lint exited 1"
    )
    assert (
        line(
            {
                "server": "workspace",
                "tool": "run_tests",
                "command": "pytest -q",
                "exit_code": 0,
            }
        )
        == "ran `pytest -q`: exit 0"
    )
    assert (
        line(
            {
                "server": "workspace",
                "tool": "find_file",
                "arguments": {"pattern": "*.py"},
                "match_count": 4,
            }
        )
        == "looked for files '*.py': 4 found"
    )
    assert line({"server": "workspace", "tool": "list", "arguments": {}}) == "listed ."
    assert (
        line(
            {
                "server": "research",
                "tool": "fetch",
                "url": "https://docs.python.org/3/",
                "status": 200,
                "sha256": "c" * 64,
            }
        )
        == "research.fetch https://docs.python.org/3/: status 200 (sha256 cccccccccccc…)"
    )
    assert (
        line(
            {
                "server": "github",
                "tool": "get_issue",
                "arguments": {"n": 1},
                "result_chars": 900,
                "result_sha256": "d" * 64,
            }
        )
        == "github.get_issue({'n': 1}): 900 characters (sha256 dddddddddddd…)"
    )
    assert (
        line(
            {
                "server": "workspace",
                "tool": "read",
                "is_error": True,
                "reason": "unknown tool",
            }
        )
        == "workspace.read returned an error: unknown tool"
    )
    # A result already shorter than its line is left as it was.
    history = [{"role": "tool", "content": "ok"}]
    assert (
        tool_history.compress(history, [(0, 1, {"server": "x", "tool": "y"})], 5, 1)
        == 0
    )
    assert history[0]["content"] == "ok"


def test_a_compressed_history_keeps_each_tool_call_s_extra_content(
    project, monkeypatch
):
    """The proxy carries Gemini's thought signature inside each tool call
    (``extra_content.google.thought_signature``); a replayed call without it
    is refused, so compression never touches the assistant messages."""
    monkeypatch.setattr(settings, "TOOL_HISTORY_KEEP_ROUNDS", 1)
    monkeypatch.setattr(settings, "TOOL_HISTORY_MAX_CHARS", 1)
    box = developer_tools.WorkspaceToolbox(_Requester(_load_daemon()), str(project))
    signed = []
    answers = []
    for n in (1, 2, 3):
        call = _call(n, "workspace__read", path=f"src/m{n}.py")
        call["extra_content"] = {"google": {"thought_signature": f"sig-{n}=="}}
        answer = _calls(call)
        signed.append(json.loads(json.dumps(answer["choices"][0]["message"])))
        answers.append(answer)
    model = _Model(answers + [_final()])
    mcp_tools.chat_with_tools(
        model, [{"role": "user", "content": "go"}], "m", "Developer", box
    )
    last = model.requests[-1]["messages"]
    assert [
        m["content"].startswith(tool_history.PREFIX)
        for m in _tool_messages(model.requests[-1])
    ] == [True, True, False]
    assistants = [m for m in last if m.get("role") == "assistant"]
    assert assistants == signed
    assert assistants[0]["tool_calls"][0]["extra_content"] == {
        "google": {"thought_signature": "sig-1=="}
    }


def test_old_reads_stay_whole_within_the_budget_and_other_results_go_first(
    project, monkeypatch
):
    """Issue #766: 16% of 1063 reads re-read the very window the history had
    summarized. A read that is still the latest view of its lines stays whole
    while every result fits ``TOOL_HISTORY_MAX_CHARS``; a search does not."""
    monkeypatch.setattr(settings, "TOOL_HISTORY_KEEP_ROUNDS", 1)
    box = developer_tools.WorkspaceToolbox(_Requester(_load_daemon()), str(project))
    answers = [
        _calls(_call(1, "workspace__read", path="src/m1.py")),
        _calls(_call(2, "workspace__search", pattern="value_2_")),
        _calls(_call(3, "workspace__read", path="src/m3.py")),
        _final(),
    ]
    model = _Model(list(answers))
    mcp_tools.chat_with_tools(
        model, [{"role": "user", "content": "go"}], "m", "Developer", box
    )
    final = _tool_messages(model.requests[-1])
    assert "value_1_149 = 149" in final[0]["content"]
    assert final[1]["content"].startswith(tool_history.PREFIX + "searched 'value_2_'")
    assert "value_3_149 = 149" in final[2]["content"]
    # Past the budget the oldest read goes first, its line saying how to
    # read it again; the next one fits and stays whole.
    whole = sum(len(m["content"]) for m in final)
    monkeypatch.setattr(settings, "TOOL_HISTORY_MAX_CHARS", whole - 1)
    model = _Model(list(answers))
    mcp_tools.chat_with_tools(
        model, [{"role": "user", "content": "go"}], "m", "Developer", box
    )
    final = _tool_messages(model.requests[-1])
    assert final[0]["content"].startswith(tool_history.PREFIX + "read src/m1.py")
    assert final[0]["content"].endswith(
        "workspace__read path=src/m1.py start_line=1 lines=150 shows it again"
    )
    assert "value_3_149 = 149" in final[2]["content"]


def test_a_read_made_stale_or_repeated_by_a_later_call_is_summarized():
    def read(path, first, last):
        return {
            "server": "workspace",
            "tool": "read",
            "path": path,
            "first_line": first,
            "last_line": last,
            "lines": 300,
        }

    observations = [
        (1, read("a.py", 1, 100)),
        (1, read("b.py", 1, 50)),
        (1, read("c.py", 1, 80)),
        (1, read("d.py", 1, 80)),
        (2, {"server": "workspace", "tool": "edit", "path": "a.py"}),
        (2, {"server": "workspace", "tool": "edit", "path": "c.py", "is_error": True}),
        (2, read("b.py", 1, 200)),
        (2, read("d.py", 50, 200)),
        (2, {"server": "workspace", "tool": "move", "path": "e.py", "to_path": "d.py"}),
    ]
    history = [{"role": "tool", "content": "x" * 5000} for _ in observations]
    results = [(i, r, o) for i, (r, o) in enumerate(observations)]
    assert tool_history.compress(history, results, 4, 1, 10**9) > 0
    texts = [m["content"] for m in history]
    assert texts[0].endswith("shows it again (the file changed after this read)")
    assert texts[1].endswith("shows it again (a later read shows these lines)")
    # A failed edit changes nothing: c.py's read is still the latest view.
    assert texts[2] == "x" * 5000
    # d.py lines 50–200 do not cover 1–80, but the move into d.py does.
    assert texts[3].endswith("(the file changed after this read)")
    assert texts[6] == "x" * 5000
    assert texts[4].startswith(tool_history.PREFIX + "edited a.py")
