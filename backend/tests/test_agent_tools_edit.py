"""The Developer's edit, checked before it is applied.

A line range, an insertion or a SEARCH/REPLACE is applied with the hashes
before and after; an edit that breaks the syntax (Python, JSON, JavaScript
with ``node --check``) or whose check cannot complete is refused with the file
untouched; an edit outside the project, into VCS data or a secret is refused;
finish hands back the final texts, restores the originals, never follows a
folder swapped for a symlink and keeps a file someone saved after an edit;
the daemon answers the edit subjects over NATS.

The Developer's tools from SWE-agent's agent-computer interface (issue #359).
The daemon's functions (``vscode-daemon/workspace_read.py``,
``workspace_edit.py``) run for real against ``tmp_path``; the shared fakes are
in ``agent_tools_helpers``.
"""

from __future__ import annotations

import asyncio
import json
import shutil

import agent_tools_helpers
import pytest
import workspace_edit  # the daemon's modules, on sys.path via conftest
import workspace_read
from agent_tools_helpers import SESSION, _edit, _sha
from services.tools import workspace_edits

# The shared fixtures.
daemon = agent_tools_helpers.daemon
project = agent_tools_helpers.project


def test_a_line_range_edit_is_applied_with_the_hashes_before_and_after(project, daemon):
    target = project / "src" / "greet.py"
    before = _sha(target)
    reply = _edit(
        daemon,
        project,
        path="src/greet.py",
        start_line=2,
        end_line=2,
        new_text="    return f'Hi, {name}!'",
    )
    assert target.read_text().splitlines()[1] == "    return f'Hi, {name}!'"
    assert reply["sha256_before"] == before and reply["sha256_after"] == _sha(target)
    assert reply["checked"] == "python" and reply["lines"] == 5
    assert "    2|     return f'Hi, {name}!'" in reply["text"].splitlines()
    # One SEARCH/REPLACE block, and an insertion (end_line = start_line - 1).
    _edit(
        daemon,
        project,
        path="src/greet.py",
        search="VERSION = 1",
        replace="VERSION = 2",
    )
    _edit(
        daemon,
        project,
        path="src/greet.py",
        start_line=1,
        end_line=0,
        new_text="# greet",
    )
    assert target.read_text().splitlines()[0] == "# greet"
    assert "VERSION = 2" in target.read_text()


def test_an_edit_that_breaks_the_syntax_is_refused_and_the_file_is_untouched(
    project, daemon
):
    target = project / "src" / "greet.py"
    original = target.read_bytes()
    with pytest.raises(workspace_read.ReadError) as broken:
        _edit(
            daemon,
            project,
            path="src/greet.py",
            start_line=1,
            new_text="def greet(name:",
        )
    assert broken.value.status == 422
    assert "would not parse (python)" in broken.value.message
    assert "unchanged" in broken.value.message
    assert target.read_bytes() == original
    config = project / "src" / "config.json"
    with pytest.raises(workspace_read.ReadError) as bad_json:
        _edit(
            daemon, project, path="src/config.json", search="false}", replace="false,}"
        )
    assert (
        "(json)" in bad_json.value.message
        and config.read_text() == '{"debug": false}\n'
    )
    with pytest.raises(workspace_read.ReadError) as ambiguous:
        _edit(daemon, project, path="src/notes.txt", search="o", replace="0")
    assert ambiguous.value.status == 422 and "appears" in ambiguous.value.message
    # A file type without a checker is applied, and the reply says so.
    assert (
        _edit(daemon, project, path="src/notes.txt", search="two", replace="2")[
            "checked"
        ]
        is None
    )
    # Nothing was kept for a refused edit: only notes.txt has an original.
    assert [
        f["path"]
        for f in workspace_edit.finish(
            str(project), SESSION, daemon._resolve_target_path
        )["files"]
    ] == ["src/notes.txt"]


@pytest.mark.skipif(shutil.which("node") is None, reason="node is not installed")
def test_a_javascript_edit_is_checked_with_node(project, daemon):
    script = project / "src" / "app.js"
    script.write_text("const a = 1;\n")
    with pytest.raises(workspace_read.ReadError) as broken:
        _edit(daemon, project, path="src/app.js", start_line=1, new_text="const a = ;")
    assert "(node)" in broken.value.message and "src/app.js" in broken.value.message
    assert script.read_text() == "const a = 1;\n"
    assert (
        _edit(
            daemon, project, path="src/app.js", start_line=1, new_text="const a = 2;"
        )["checked"]
        == "node"
    )


def test_an_edit_outside_the_project_into_vcs_data_or_a_secret_is_refused(
    project, daemon, tmp_path
):
    (tmp_path / "outside.py").write_text("X = 1\n")
    refused = {
        "../outside.py": 400,
        ".git/config": 400,
        ".env": 403,
        "src/none.py": 404,
    }
    for path, status in refused.items():
        with pytest.raises(workspace_read.ReadError) as error:
            _edit(daemon, project, path=path, start_line=1, new_text="X = 2")
        assert error.value.status == status, path
    assert (tmp_path / "outside.py").read_text() == "X = 1\n"
    assert (project / ".git" / "config").read_text() == "[core]\n"
    with pytest.raises(workspace_read.ReadError) as no_session:
        workspace_edit.edit_file(
            str(project),
            {
                "session": "../x",
                "path": "src/notes.txt",
                "start_line": 1,
                "new_text": "x",
            },
            daemon._resolve_target_path,
        )
    assert no_session.value.status == 400
    with pytest.raises(workspace_read.ReadError) as out_of_range:
        _edit(daemon, project, path="src/notes.txt", start_line=9, new_text="x")
    assert out_of_range.value.status == 400 and "2 lines" in out_of_range.value.message


def test_a_new_file_is_refused_with_how_to_create_it(project, daemon):
    # Phase D's bench (issue #766): half the edits tried to create a file and
    # the model retried; the refusal now says where a new file goes.
    with pytest.raises(workspace_read.ReadError) as missing:
        _edit(
            daemon,
            project,
            path="tests/test_new.py",
            start_line=1,
            end_line=0,
            new_text="X = 1",
        )
    assert missing.value.status == 404
    assert "write a new file whole in your answer" in missing.value.message
    assert "path=tests/test_new.py" in missing.value.message
    assert not (project / "tests" / "test_new.py").exists()


def test_finish_hands_back_the_final_texts_and_puts_the_originals_back(
    project, daemon, monkeypatch
):
    greet, notes = project / "src" / "greet.py", project / "src" / "notes.txt"
    originals = {greet: greet.read_bytes(), notes: notes.read_bytes()}
    _edit(
        daemon,
        project,
        path="src/greet.py",
        search="VERSION = 1",
        replace="VERSION = 3",
    )
    _edit(
        daemon,
        project,
        path="src/greet.py",
        search="VERSION = 3",
        replace="VERSION = 4",
    )
    _edit(daemon, project, path="src/notes.txt", search="one", replace="uno")
    _edit(daemon, project, path="src/notes.txt", search="uno", replace="one")
    done = workspace_edit.finish(str(project), SESSION, daemon._resolve_target_path)
    by_path = {f["path"]: f for f in done["files"]}
    assert "VERSION = 4" in by_path["src/greet.py"]["content"]
    assert by_path["src/greet.py"]["changed"] is True
    # Edited back to what it was: nothing to deliver.
    assert by_path["src/notes.txt"]["changed"] is False
    assert all(f["restored"] for f in done["files"])
    assert {p: p.read_bytes() for p in originals} == originals
    assert (
        workspace_edit.finish(str(project), SESSION, daemon._resolve_target_path)[
            "files"
        ]
        == []
    )
    # A session edits at most EDIT_MAX_FILES files.
    monkeypatch.setattr(workspace_edit, "EDIT_MAX_FILES", 1)
    _edit(daemon, project, path="src/notes.txt", search="one", replace="1")
    with pytest.raises(workspace_read.ReadError) as limit:
        _edit(
            daemon, project, path="src/greet.py", search="VERSION = 1", replace="V = 1"
        )
    assert limit.value.status == 409
    workspace_edit.finish(str(project), SESSION, daemon._resolve_target_path)


def test_the_daemon_answers_the_edit_subjects_over_nats(project, daemon):
    replies = []

    class _Msg:
        def __init__(self, body):
            self.data = json.dumps(body).encode()
            self.reply = "inbox"

    async def reply(nc, msg, body):
        replies.append(body)

    root = str(project)

    async def run():
        for handler, body in (
            (
                workspace_edit.handle_edit,
                {
                    "workspace_root": root,
                    "session": SESSION,
                    "path": "src/notes.txt",
                    "search": "two",
                    "replace": "2",
                },
            ),
            (
                workspace_edit.handle_edit,
                {
                    "workspace_root": root,
                    "session": SESSION,
                    "path": "src/greet.py",
                    "start_line": 1,
                    "new_text": "def (",
                },
            ),
            (
                workspace_edit.handle_finish,
                {"workspace_root": root, "session": SESSION},
            ),
            (workspace_read.handle_find, {"workspace_root": root, "pattern": "*.txt"}),
        ):
            await handler(
                None,
                _Msg(body),
                reply,
                daemon._request_body,
                daemon._resolve_target_path,
            )

    asyncio.run(run())
    assert replies[0]["path"] == "src/notes.txt" and replies[0]["sha256_after"]
    assert replies[1]["error"]["status"] == 422
    assert replies[2]["files"][0]["content"] == "one\n2\n"
    assert replies[3]["files"] == ["src/notes.txt"]
    assert [s for s, _h in workspace_edit.HANDLERS] == [
        "workspace.edit",
        "workspace.edit.finish",
    ]


def test_finish_never_follows_a_folder_swapped_for_a_symlink(project, daemon, tmp_path):
    """After an edit, ``src`` becomes a symlink to a folder outside the
    project: finish reads nothing there, writes nothing there, and keeps the
    original in the journal."""
    _edit(daemon, project, path="src/notes.txt", search="two", replace="2")
    outside = tmp_path / "outside"
    outside.mkdir()
    (outside / "notes.txt").write_text("private\n")
    (project / "src").rename(project / "src_moved")
    (project / "src").symlink_to(outside, target_is_directory=True)
    done = workspace_edit.finish(str(project), SESSION, daemon._resolve_target_path)
    [report] = done["files"]
    assert report["content"] is None and report["changed"] is False
    assert "no longer leads" in report["error"] and report["restored"] is False
    assert (outside / "notes.txt").read_text() == "private\n"
    journal = workspace_edit.state_dir() / SESSION
    assert (journal / "manifest.json").is_file() and (
        journal / "0.orig"
    ).read_text() == "one\ntwo\n"
    assert workspace_edits.delivered([report]) == []


def test_a_file_saved_by_someone_after_an_edit_is_kept_and_not_delivered(
    project, daemon
):
    notes = project / "src" / "notes.txt"
    _edit(daemon, project, path="src/notes.txt", search="two", replace="2")
    notes.write_text("one\nthe person's own line\n")
    with pytest.raises(workspace_read.ReadError) as later:
        _edit(daemon, project, path="src/notes.txt", search="one", replace="1")
    assert later.value.status == 409 and "outside the edit tool" in later.value.message
    done = workspace_edit.finish(str(project), SESSION, daemon._resolve_target_path)
    [report] = done["files"]
    assert report["conflict"] is True and report["content"] is None
    assert notes.read_text() == "one\nthe person's own line\n"
    assert (workspace_edit.state_dir() / SESSION / "0.orig").read_text() == "one\ntwo\n"
    # Not delivered, and recorded apart on the evidence.
    agent = {"tool_calls": [], "tool_edits": [report]}
    evidence = workspace_edits.observe_tools({"workspace_writes": []}, agent)
    assert "tool_edit_files" not in evidence
    assert evidence["tool_edit_conflicts"] == [
        {"path": "src/notes.txt", "error": report["error"], "conflict": True}
    ]
    assert workspace_edits.with_tool_edits([], [report]) == []


def test_a_javascript_check_that_cannot_complete_refuses_the_edit(
    project, daemon, monkeypatch
):
    import subprocess

    script = project / "src" / "app.js"
    script.write_text("const a = 1;\n")
    monkeypatch.setattr(workspace_edit.shutil, "which", lambda name: "/usr/bin/node")

    def hang(*args, **kwargs):
        raise subprocess.TimeoutExpired(args[0], 15)

    monkeypatch.setattr(workspace_edit.subprocess, "run", hang)
    with pytest.raises(workspace_read.ReadError) as refused:
        _edit(daemon, project, path="src/app.js", start_line=1, new_text="const a = 2;")
    assert refused.value.status == 422 and "could not complete" in refused.value.message
    assert script.read_text() == "const a = 1;\n"
    # Without node at all the edit goes unchecked, and says so.
    monkeypatch.setattr(workspace_edit.shutil, "which", lambda name: None)
    assert (
        _edit(
            daemon, project, path="src/app.js", start_line=1, new_text="const a = 2;"
        )["checked"]
        is None
    )


def test_appending_after_a_last_line_without_a_newline_keeps_the_lines_apart(
    project, daemon
):
    tail = project / "src" / "tail.txt"
    tail.write_text("a\nb")
    _edit(daemon, project, path="src/tail.txt", start_line=3, end_line=2, new_text="c")
    assert tail.read_text() == "a\nb\nc\n"
