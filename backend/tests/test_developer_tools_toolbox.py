"""The Developer's toolbox.

It offers the read tools and the test run only with a command, records a read
with the file's hash, turns errors into results, runs the project's command or
a narrower one on the same runner; the combined toolbox dispatches by name and
caps the rounds; the tools go to the roles that deliver files when the project
is known, and the prompt carries the repository map.

The Developer's workspace tools (``services/developer_tools.py``) and the
daemon's read handlers (``vscode-daemon/workspace_read.py``). The daemon's
functions run for real against ``tmp_path``; the shared fakes are in
``developer_tools_helpers``.
"""

from __future__ import annotations

import hashlib
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "app"))

import developer_tools_helpers
from config import settings
from developer_tools_helpers import (
    _answer_with_calls,
    _call,
    _FakeMcp,
    _load_daemon,
    _Model,
    _Requester,
)
from services.tools import developer_tools, mcp_config, mcp_tools

# The shared fixtures.
project = developer_tools_helpers.project


def test_the_toolbox_offers_the_read_tools_and_the_test_run_only_with_a_command(
    project,
):
    daemon = _load_daemon()
    box = developer_tools.WorkspaceToolbox(_Requester(daemon), str(project))
    assert [s["function"]["name"] for s in box.schemas()] == [
        "workspace__list",
        "workspace__read",
        "workspace__search",
        "workspace__find_file",
    ]
    with_tests = developer_tools.WorkspaceToolbox(
        _Requester(daemon), str(project), test_command="python -m pytest -q"
    )
    assert [s["function"]["name"] for s in with_tests.schemas()][
        -1
    ] == "workspace__run_tests"
    assert all(t["read_only"] for t in with_tests.tools.values())
    assert with_tests.max_rounds == settings.DEVELOPER_TOOL_ROUNDS


def test_a_read_is_recorded_with_the_file_s_hash_and_the_model_sees_the_numbered_text(
    project,
):
    daemon = _load_daemon()
    box = developer_tools.WorkspaceToolbox(_Requester(daemon), str(project))
    observation, text = box.call(_call("workspace__read", path="src/greet.py"), 1)
    raw = (project / "src" / "greet.py").read_bytes()
    assert observation["server"] == "workspace" and observation["tool"] == "read"
    assert (
        observation["path"] == "src/greet.py"
        and observation["sha256"] == hashlib.sha256(raw).hexdigest()
    )
    assert (
        observation["bytes"] == len(raw)
        and observation["read_only"] is True
        and observation["is_error"] is False
    )
    assert observation["result_sha256"] == hashlib.sha256(text.encode()).hexdigest()
    assert text.splitlines()[:2] == [
        "[src/greet.py: lines 1–2 of 2]",
        "    1| def greet(name):",
    ]
    listing, text = box.call(_call("workspace__list"), 1)
    assert listing["tool"] == "list" and "src/greet.py" in text.splitlines()
    search, text = box.call(_call("workspace__search", pattern="greet", path="src"), 2)
    assert search["files_scanned"] == 2 and text == "src/greet.py:1: def greet(name):"


def test_errors_are_results_the_model_sees_and_the_record_keeps(project):
    daemon = _load_daemon()
    box = developer_tools.WorkspaceToolbox(
        _Requester(daemon), str(project), result_max_chars=80
    )
    missing, text = box.call(_call("workspace__read", path="src/none.py"), 1)
    assert missing["is_error"] is True and text.startswith("Error: No such file")
    unknown, text = box.call(_call("workspace__delete", path="x"), 1)
    assert unknown["reason"] == "unknown tool" and text.startswith("Unknown tool")
    malformed, text = box.call(
        {"id": "c", "function": {"name": "workspace__read", "arguments": "{nope"}}, 1
    )
    assert malformed["reason"].startswith("malformed arguments") and text.startswith(
        "Error: malformed"
    )
    (project / "src" / "big.py").write_text("".join(f"x = {i}\n" for i in range(50)))
    cut, text = box.call(_call("workspace__read", path="src/big.py"), 1)
    assert (
        cut["truncated"] is True
        and text.endswith("[result cut here]")
        and cut["result_chars"] > 80
    )


def test_run_tests_runs_the_project_s_command_or_a_narrower_one_on_the_same_runner(
    project,
):
    daemon = _load_daemon()
    requester = _Requester(
        daemon,
        {
            "python -m pytest -q": {
                "exit_code": 1,
                "stdout": "1 failed\n",
                "stderr": "",
            },
            "python -m pytest tests/test_greet.py": {
                "exit_code": 0,
                "stdout": "1 passed\n",
                "stderr": "",
            },
        },
    )
    box = developer_tools.WorkspaceToolbox(
        requester, str(project), test_command="python -m pytest -q"
    )
    whole, text = box.call(_call("workspace__run_tests"), 1)
    assert (
        whole["command"] == "python -m pytest -q"
        and whole["exit_code"] == 1
        and text.startswith("exit status 1")
    )
    narrowed, text = box.call(
        _call("workspace__run_tests", command="python -m pytest tests/test_greet.py"), 2
    )
    assert (
        narrowed["command"] == "python -m pytest tests/test_greet.py"
        and "1 passed" in text
    )
    # Another runner, or shell syntax, falls back to the project's command.
    for wanted in ("rm -rf /", "python -m pytest; cat /etc/passwd", "npm test"):
        again, _text = box.call(_call("workspace__run_tests", command=wanted), 3)
        assert again["command"] == "python -m pytest -q", wanted
    assert [p["command"] for s, p in requester.calls if s == "workspace.execute"][
        :2
    ] == [
        "python -m pytest -q",
        "python -m pytest tests/test_greet.py",
    ]
    timed = developer_tools.WorkspaceToolbox(
        _Requester(
            daemon,
            {"python -m pytest -q": {"timed_out": True, "stdout": "...", "stderr": ""}},
        ),
        str(project),
        test_command="python -m pytest -q",
    )
    out, text = timed.call(_call("workspace__run_tests"), 1)
    assert out["timed_out"] is True and text.startswith(
        "The tests ran past the time limit"
    )


def test_the_combined_toolbox_dispatches_by_name_and_caps_the_rounds(
    project, monkeypatch
):
    monkeypatch.setattr(settings, "DEVELOPER_TOOL_ROUNDS", 2)
    daemon = _load_daemon()
    mcp = _FakeMcp()
    box = developer_tools.CombinedToolbox(
        developer_tools.WorkspaceToolbox(_Requester(daemon), str(project)), mcp
    )
    assert [s["function"]["name"] for s in box.schemas()] == [
        "workspace__list",
        "workspace__read",
        "workspace__search",
        "workspace__find_file",
        "tracker__search",
    ]
    assert box.unavailable == {"gone": "unreachable"} and box.max_rounds == 2
    model = _Model(
        [
            _answer_with_calls(
                _call("workspace__read", path="README.md"),
                _call("tracker__search", q="x"),
            )
        ]
    )
    _result, observations = mcp_tools.chat_with_tools(
        model, [{"role": "user", "content": "go"}], "m", "Developer", box
    )
    # Two rounds of calls, then the request without tools.
    assert [o["server"] for o in observations] == ["workspace", "tracker"] * 2
    assert [r["tools"] is not None for r in model.requests] == [True, True, False]
    assert len(mcp.calls) == 2
    # A name that is not a built-in tool goes to the MCP side, whatever its prefix.
    box.call(_call("workspace__other", x=1), 3)
    assert len(mcp.calls) == 3
    box.close()
    assert mcp.closed
    # The built-in namespace is reserved: no MCP server may take the name.
    with pytest.raises(mcp_config.McpConfigError, match="reserved"):
        mcp_config.normalize_server("workspace", {"url": "https://x.example/mcp"})


def test_the_tools_go_to_every_executing_role_when_the_project_is_known(
    project, monkeypatch
):
    daemon = _load_daemon()
    monkeypatch.setattr(
        mcp_tools.Toolbox, "load", classmethod(lambda cls, *a, **k: None)
    )
    monkeypatch.setattr(settings, "RESEARCH_TOOLS_ENABLED", False)  # Workspace alone.
    developer_tools.unbind()
    assert developer_tools.toolbox_for(1, "Developer") is None  # not bound: no daemon
    developer_tools.bind(
        object(), object(), lambda company_id: str(project) if company_id == 1 else None
    )
    try:
        monkeypatch.setattr(developer_tools, "_request_from_thread", _Requester(daemon))
        # The step sets the ceiling (contracts/tool_steps): a PO executing a
        # task reads too.
        assert isinstance(
            developer_tools.toolbox_for(1, "PO"), developer_tools.CombinedToolbox
        )
        assert developer_tools.toolbox_for(2, "Developer") is None  # no project path
        box = developer_tools.toolbox_for(1, "Developer")
        assert isinstance(box, developer_tools.CombinedToolbox) and box.mcp is None
        assert [s["function"]["name"] for s in box.schemas()] == [
            "workspace__list",
            "workspace__read",
            "workspace__search",
            "workspace__find_file",
        ]
        for role in ("QA", "dev", "Developer"):
            assert developer_tools.offered_to(role)
        monkeypatch.setattr(settings, "DEVELOPER_TOOLS_ENABLED", False)
        assert (
            developer_tools.toolbox_for(1, "Developer") is None
            and developer_tools.repository_section(1, "Developer") == ""
        )
    finally:
        developer_tools.unbind()


def test_the_prompt_carries_the_top_of_the_tree_as_the_repository_map(
    project, monkeypatch
):
    daemon = _load_daemon()
    developer_tools.bind(object(), object(), lambda company_id: str(project))
    try:
        monkeypatch.setattr(developer_tools, "_request_from_thread", _Requester(daemon))
        section = developer_tools.repository_section(1, "Developer")
        assert (
            section.startswith("\n\n## Repository\n")
            and "src/greet.py" in section
            and "node_modules" not in section
        )
        # Every executing role has the tools, so every one gets the map.
        assert "## Repository" in developer_tools.repository_section(1, "ScrumMaster")
        assert developer_tools.repository_section(None, "Developer") == ""
        monkeypatch.setattr(developer_tools, "MAP_MAX_CHARS", 20)
        assert developer_tools.repository_section(1, "Developer").endswith(
            "[listing cut here]"
        )

        def boom(*a, **k):
            raise RuntimeError("daemon down")

        monkeypatch.setattr(developer_tools, "_request_from_thread", boom)
        assert developer_tools.repository_section(1, "Developer") == ""
    finally:
        developer_tools.unbind()


def test_a_read_without_lines_shows_as_many_lines_as_fit_the_result_budget(project):
    """Issue #766: 601 of 1063 reads stopped at the 100-line default and the
    Developers never got past line 500 of a 642-line file. Without ``lines``
    the toolbox asks for 400 and keeps the whole lines that fit; an older
    daemon that ignores ``lines`` still answers its 100."""
    daemon = _load_daemon()
    (project / "src" / "long.py").write_text(
        "".join(f"v{i} = {i}\n" for i in range(642))
    )
    requester = _Requester(daemon)
    box = developer_tools.WorkspaceToolbox(requester, str(project))
    box.result_max_chars = 16000
    observation, text = box.call(_call("workspace__read", path="src/long.py"), 1)
    assert requester.calls[-1][1]["lines"] == 400
    assert "[src/long.py: lines 1–400 of 642]" in text and "v399 = 399" in text
    assert observation["last_line"] == 400
    # A smaller budget: fewer whole lines, and the observation names the last
    # one the model saw, not the daemon's.
    box.result_max_chars = 2000
    observation, text = box.call(_call("workspace__read", path="src/long.py"), 1)
    shown = observation["last_line"]
    assert 100 < shown < 400 and len(text) <= 2000
    assert f"[src/long.py: lines 1–{shown} of 642]" in text
    assert f"read on with start_line={shown + 1}" in text

    def older(subject, payload, timeout):
        return requester(subject, {**payload, "lines": None}, timeout)

    box = developer_tools.WorkspaceToolbox(older, str(project))
    box.result_max_chars = 16000
    observation, text = box.call(_call("workspace__read", path="src/long.py"), 1)
    assert "[src/long.py: lines 1–100 of 642]" in text
    assert observation["last_line"] == 100
