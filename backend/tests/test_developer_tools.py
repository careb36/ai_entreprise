"""The daemon's read handlers for the Developer's tools.

The daemon lists the tree without dependencies or VCS data, reads a file
numbered with its hash and tells a binary apart, cuts a long file and says how
long it is, searches and refuses a bad pattern, and answers over NATS in the
reply shape.

The Developer's workspace tools (``services/developer_tools.py``) and the
daemon's read handlers (``vscode-daemon/workspace_read.py``). The daemon's
functions run for real against ``tmp_path``; the shared fakes are in
``developer_tools_helpers``.
"""

from __future__ import annotations

import hashlib
import json
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "app"))

import developer_tools_helpers
import workspace_read  # the daemon's module, on sys.path via conftest

# test_research_tools.py imports the fake model's answers from here too.
from developer_tools_helpers import _answer, _answer_with_calls, _load_daemon  # noqa: F401

# The shared fixtures.
project = developer_tools_helpers.project


def test_the_daemon_lists_the_tree_without_dependencies_or_vcs_data(project):
    daemon = _load_daemon()
    listing = workspace_read.list_tree(
        str(project), None, 2, daemon._resolve_target_path
    )
    assert listing["path"] == "." and listing["truncated"] is False
    assert listing["entries"] == [
        "src/",
        "tests/",
        "README.md",
        "logo.png",
        "src/count.py",
        "src/greet.py",
        "tests/test_greet.py",
    ]
    one_level = workspace_read.list_tree(
        str(project), "src", 1, daemon._resolve_target_path
    )
    assert one_level == {
        "path": "src",
        "entries": ["src/count.py", "src/greet.py"],
        "truncated": False,
    }
    with pytest.raises(workspace_read.ReadError) as outside:
        workspace_read.list_tree(str(project), "../", 1, daemon._resolve_target_path)
    assert outside.value.status == 400
    with pytest.raises(workspace_read.ReadError) as missing:
        workspace_read.list_tree(
            str(project / "nope"), None, 1, daemon._resolve_target_path
        )
    assert missing.value.status == 404


def test_the_daemon_reads_a_file_numbered_with_its_hash_and_tells_a_binary_apart(
    project,
):
    daemon = _load_daemon()
    read = workspace_read.read_file(
        str(project), "src/greet.py", None, daemon._resolve_target_path
    )
    raw = (project / "src" / "greet.py").read_bytes()
    assert read["sha256"] == hashlib.sha256(raw).hexdigest() and read["bytes"] == len(
        raw
    )
    assert read["text"].splitlines() == [
        "    1| def greet(name):",
        "    2|     return f'Hello, {name}!'",
    ]
    assert read["lines"] == 2 and read["truncated"] is False and read["binary"] is False
    second = workspace_read.read_file(
        str(project), "src/greet.py", 2, daemon._resolve_target_path
    )
    assert (
        second["text"] == "    2|     return f'Hello, {name}!'"
        and second["first_line"] == 2
    )
    binary = workspace_read.read_file(
        str(project), "logo.png", None, daemon._resolve_target_path
    )
    assert binary["binary"] is True and binary["text"] == "" and binary["sha256"]
    with pytest.raises(workspace_read.ReadError) as missing:
        workspace_read.read_file(
            str(project), "src/none.py", None, daemon._resolve_target_path
        )
    assert missing.value.status == 404
    with pytest.raises(workspace_read.ReadError):
        workspace_read.read_file(
            str(project), "../etc/passwd", None, daemon._resolve_target_path
        )


def test_a_long_file_is_cut_and_the_reply_says_how_long_it_is(project):
    daemon = _load_daemon()
    (project / "src" / "big.py").write_text("".join(f"x = {i}\n" for i in range(1000)))
    read = workspace_read.read_file(
        str(project), "src/big.py", None, daemon._resolve_target_path
    )
    assert read["truncated"] is True and read["lines"] == 1000
    assert len(read["text"].splitlines()) == workspace_read.READ_MAX_LINES


def test_a_read_may_ask_for_up_to_400_lines(project):
    """Sprint 6 (2026-11-23): with 100-line windows and five rounds the
    Developer saw at most 500 lines and could not split a 642-line file."""
    daemon = _load_daemon()
    (project / "src" / "big.py").write_text("".join(f"x = {i}\n" for i in range(1000)))

    def read(lines, start=None):
        return workspace_read.read_file(
            str(project), "src/big.py", start, daemon._resolve_target_path, lines
        )

    assert len(read(None)["text"].splitlines()) == workspace_read.READ_MAX_LINES
    wide = read(400)
    assert len(wide["text"].splitlines()) == 400 == workspace_read.READ_LINES_CEILING
    assert (wide["first_line"], wide["last_line"]) == (1, 400)
    assert read(400, 401)["last_line"] == 800
    # Past the ceiling, zero, negative or not a number: bounded, never refused.
    assert len(read(5000)["text"].splitlines()) == 400
    assert len(read(0)["text"].splitlines()) == 1
    assert len(read("many")["text"].splitlines()) == workspace_read.READ_MAX_LINES
    assert len(read(True)["text"].splitlines()) == workspace_read.READ_MAX_LINES


def test_the_read_tool_offers_and_passes_lines():
    from services.tools import developer_tool_schemas

    read = next(
        t
        for t in developer_tool_schemas.SCHEMAS
        if t["function"]["name"].endswith("read")
    )
    assert read["function"]["parameters"]["properties"]["lines"]["type"] == "integer"
    assert "up to 400" in read["function"]["description"]


def test_the_daemon_searches_the_project_and_refuses_a_bad_pattern(project):
    daemon = _load_daemon()
    found = workspace_read.search(
        str(project), r"def greet|COUNT", None, daemon._resolve_target_path
    )
    assert found["matches"] == [
        "src/count.py:1: COUNT = 0",
        "src/greet.py:1: def greet(name):",
    ]
    # Dependencies, VCS data and the binary are never scanned: four text files.
    assert found["files_scanned"] == 4 and found["truncated"] is False
    narrowed = workspace_read.search(
        str(project), "greet", "tests", daemon._resolve_target_path
    )
    assert [m.split(":")[0] for m in narrowed["matches"]] == ["tests/test_greet.py"] * 3
    with pytest.raises(workspace_read.ReadError) as bad:
        workspace_read.search(str(project), "(", None, daemon._resolve_target_path)
    assert bad.value.status == 400 and "pattern" in bad.value.message
    with pytest.raises(workspace_read.ReadError):
        workspace_read.search(str(project), "x", "../", daemon._resolve_target_path)


def test_the_daemon_handlers_answer_over_nats_in_the_reply_shape(project):
    import asyncio

    daemon = _load_daemon()
    replies = []

    class _Msg:
        def __init__(self, body):
            self.data = json.dumps(body).encode()
            self.reply = "inbox"

    async def reply(nc, msg, body):
        replies.append(body)

    async def run():
        for handler, body in (
            (workspace_read.handle_list, {"workspace_root": str(project), "depth": 1}),
            (
                workspace_read.handle_read,
                {"workspace_root": str(project), "path": "README.md"},
            ),
            (
                workspace_read.handle_search,
                {"workspace_root": str(project), "pattern": "Proj"},
            ),
            (
                workspace_read.handle_read,
                {"workspace_root": str(project), "path": "nope"},
            ),
            (workspace_read.handle_read, {"path": "README.md"}),
        ):
            await handler(
                None,
                _Msg(body),
                reply,
                daemon._request_body,
                daemon._resolve_target_path,
            )

    asyncio.run(run())
    assert replies[0]["entries"] == ["src/", "tests/", "README.md", "logo.png"]
    assert replies[1]["text"] == "    1| # Proj"
    assert replies[2]["matches"] == ["README.md:1: # Proj"]
    assert replies[3]["error"]["status"] == 404
    assert replies[4]["error"]["status"] == 400
    assert [s for s, _h in workspace_read.HANDLERS] == [
        "workspace.list",
        "workspace.read",
        "workspace.search",
        "workspace.find",
    ]
