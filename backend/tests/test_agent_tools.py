"""The Developer's reader and searches.

A read is a window of lines the model pages through, and one cut by the
result budget names the lines shown and where to go on; a search over the
bound says how many and where; ``find_file`` by name or glob, bounded and
never a secret; an empty answer says it is empty.

The Developer's tools from SWE-agent's agent-computer interface (issue #359).
The daemon's functions (``vscode-daemon/workspace_read.py``,
``workspace_edit.py``) run for real against ``tmp_path``; the shared fakes are
in ``agent_tools_helpers``.
"""

from __future__ import annotations

import agent_tools_helpers
import pytest
import workspace_read
from agent_tools_helpers import _call, _Requester, _sha
from services.tools import developer_tool_replies, developer_tools

# The shared fixtures.
daemon = agent_tools_helpers.daemon
project = agent_tools_helpers.project


def test_a_read_is_a_window_of_lines_the_model_pages_through(project, daemon):
    resolve = daemon._resolve_target_path
    first = workspace_read.read_file(str(project), "src/big.py", None, resolve)
    assert first["first_line"] == 1 and first["last_line"] == 100
    assert first["lines"] == 250 and first["truncated"] is True
    assert len(first["text"].splitlines()) == workspace_read.READ_MAX_LINES == 100
    assert first["sha256"] == _sha(project / "src" / "big.py")
    text = developer_tool_replies.read_text("src/big.py", first)
    assert text.splitlines()[0] == "[src/big.py: lines 1–100 of 250]"
    assert (
        text.splitlines()[-1] == "[150 more lines below; read on with start_line=101]"
    )
    middle = workspace_read.read_file(str(project), "src/big.py", 101, resolve)
    text = developer_tool_replies.read_text("src/big.py", middle)
    assert text.splitlines()[:3] == [
        "[src/big.py: lines 101–200 of 250]",
        "[lines 1–100 above; start_line=1 goes back]",
        "  101| x_100 = 100",
    ]
    last = workspace_read.read_file(str(project), "src/big.py", 201, resolve)
    assert last["last_line"] == 250 and last["truncated"] is False
    assert "more lines below" not in developer_tool_replies.read_text(
        "src/big.py", last
    )
    with pytest.raises(workspace_read.ReadError) as past:
        workspace_read.read_file(str(project), "src/big.py", 251, resolve)
    assert past.value.status == 400 and "250 lines" in past.value.message
    (project / "src" / "empty.py").write_text("")
    empty = workspace_read.read_file(str(project), "src/empty.py", None, resolve)
    assert developer_tool_replies.read_text("src/empty.py", empty) == (
        "(the file src/empty.py is empty)"
    )


# --- A.2 and A.4: capped searches, find_file, explicit empty answers -------


def test_a_search_over_the_bound_says_how_many_and_where_instead_of_cutting(
    project, daemon
):
    resolve = daemon._resolve_target_path
    many = workspace_read.search(str(project), r"x_\d+", None, resolve)
    assert len(many["matches"]) == workspace_read.SEARCH_MAX_MATCHES
    assert many["match_count"] == 250 and many["files_matched"] == 1
    assert many["truncated"] is True and many["per_file"] == [["src/big.py", 250]]
    text = developer_tool_replies.search_text(r"x_\d+", many)
    summary = (
        "250 matches in 1 file(s), more than the 60 shown at most: refine the "
        "pattern or the path. Matches per file:"
    )
    assert text.splitlines() == [summary, "  src/big.py: 250"]
    few = workspace_read.search(str(project), "def greet", None, resolve)
    assert few["match_count"] == 1 and few["per_file"] == []
    assert developer_tool_replies.search_text("def greet", few) == (
        "src/greet.py:1: def greet(name):"
    )
    none = workspace_read.search(str(project), "nowhere_to_be_found", None, resolve)
    assert developer_tool_replies.search_text("nowhere_to_be_found", none) == (
        f"No line matches `nowhere_to_be_found` ({none['files_scanned']} file(s) searched)."
    )


def test_find_file_by_name_or_glob_bounded_and_never_a_secret(
    project, daemon, monkeypatch
):
    resolve = daemon._resolve_target_path
    by_name = workspace_read.find_files(str(project), "GREET.py", None, resolve)
    assert by_name["files"] == ["src/greet.py"] and by_name["match_count"] == 1
    by_glob = workspace_read.find_files(str(project), "*.py", None, resolve)
    assert by_glob["files"] == ["src/big.py", "src/greet.py"]
    by_path = workspace_read.find_files(str(project), "src/*.json", None, resolve)
    assert by_path["files"] == ["src/config.json"]
    # Hidden files, files that may hold secrets and VCS data are never found.
    assert workspace_read.find_files(str(project), ".env", None, resolve)["files"] == []
    assert (
        workspace_read.find_files(str(project), "config", None, resolve)["files"] == []
    )
    none = workspace_read.find_files(str(project), "missing.rs", "src", resolve)
    assert developer_tool_replies.find_text("missing.rs", none) == (
        "No file matches `missing.rs` under src."
    )
    monkeypatch.setattr(workspace_read, "FIND_MAX_RESULTS", 1)
    over = workspace_read.find_files(str(project), "*", None, resolve)
    assert over["truncated"] is True and over["match_count"] == 4
    assert developer_tool_replies.find_text("*", over) == (
        "4 files match `*` under ., more than the 1 listed at most: refine the "
        "name or the path."
    )
    with pytest.raises(workspace_read.ReadError):
        workspace_read.find_files(str(project), "*.py", "../", resolve)
    with pytest.raises(workspace_read.ReadError):
        workspace_read.find_files(str(project), " ", None, resolve)


def test_an_empty_answer_says_it_is_empty():
    assert developer_tool_replies.tests_text(0, "", 100) == (
        "exit status 0\n(the command finished with no output)"
    )
    assert (
        developer_tool_replies.tests_text(1, "boom\n", 100) == "exit status 1\nboom\n"
    )
    assert developer_tool_replies.list_text({"entries": []}).startswith(
        "(the folder holds no files"
    )


def test_a_window_cut_by_the_budget_names_the_lines_shown_and_where_to_go_on(
    project, daemon
):
    (project / "src" / "wide.py").write_text(
        "".join(f"x_{i} = '{'y' * 60}'\n" for i in range(150))
    )
    box = developer_tools.WorkspaceToolbox(
        _Requester(daemon), str(project), result_max_chars=1200
    )
    observation, text = box.call(_call("workspace__read", path="src/wide.py"), 1)
    lines = text.splitlines()
    shown = [line for line in lines if not line.startswith("[")]
    last = len(shown)
    assert 0 < last < 100 and observation["truncated"] is False
    assert len(text) <= 1200
    assert lines[0] == f"[src/wide.py: lines 1–{last} of 150]"
    assert shown[-1].startswith(f"{last:5d}| ")
    assert lines[-1] == (
        f"[{150 - last} more lines below; read on with start_line={last + 1}]"
    )
    (project / "src" / "one.py").write_text("z = '" + "w" * 3000 + "'\n")
    _obs, text = box.call(_call("workspace__read", path="src/one.py"), 2)
    assert "[line cut]" in text and len(text) <= 1200
