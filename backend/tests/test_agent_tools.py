"""The Developer's tools from SWE-agent's agent-computer interface (issue #359):
the windowed reader, the capped search and ``find_file``, explicit empty
answers, and the edit applied only when the file still parses.

The daemon's functions (``vscode-daemon/workspace_read.py``,
``workspace_edit.py``) run for real against ``tmp_path``; NATS is a
synchronous fake that hands each request to them.
"""

from __future__ import annotations

import asyncio
import hashlib
import importlib.util
import json
import shutil
from pathlib import Path

import pytest
import workspace_edit  # the daemon's modules, on sys.path via conftest
import workspace_read
from config import settings
from models.agent_run import AgentRun, Verification
from models.company import Company, ProjectDiscovery
from models.sprint import BacklogItem, Sprint
from services import (
    developer_tool_replies,
    developer_tools,
    mcp_tools,
    risk,
    task_worker,
    workspace_edits,
)
from sqlmodel import select

_DAEMON_PATH = Path(__file__).resolve().parents[2] / "vscode-daemon" / "daemon.py"
SESSION = "ab" * 16


def _load_daemon():
    spec = importlib.util.spec_from_file_location("fabrique_daemon_aci", _DAEMON_PATH)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


@pytest.fixture(scope="module")
def daemon():
    return _load_daemon()


@pytest.fixture()
def project(tmp_path):
    root = tmp_path / "proj"
    (root / "src").mkdir(parents=True)
    (root / ".git").mkdir()
    (root / ".git" / "config").write_text("[core]\n")
    (root / "src" / "greet.py").write_text(
        "def greet(name):\n    return f'Hello, {name}!'\n\n\nVERSION = 1\n"
    )
    (root / "src" / "config.json").write_text('{"debug": false}\n')
    (root / "src" / "notes.txt").write_text("one\ntwo\n")
    (root / ".env").write_text("TOKEN=x\n")
    (root / "src" / "big.py").write_text("".join(f"x_{i} = {i}\n" for i in range(250)))
    return root


def _sha(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _edit(daemon, root, **data):
    return workspace_edit.edit_file(
        str(root), {"session": SESSION, **data}, daemon._resolve_target_path
    )


# --- A.1: the windowed reader ----------------------------------------------


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


# --- A.3: the edit, checked before it is applied ----------------------------


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
        f["path"] for f in workspace_edit.finish(str(project), SESSION)["files"]
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
    done = workspace_edit.finish(str(project), SESSION)
    by_path = {f["path"]: f for f in done["files"]}
    assert "VERSION = 4" in by_path["src/greet.py"]["content"]
    assert by_path["src/greet.py"]["changed"] is True
    # Edited back to what it was: nothing to deliver.
    assert by_path["src/notes.txt"]["changed"] is False
    assert all(f["restored"] for f in done["files"])
    assert {p: p.read_bytes() for p in originals} == originals
    assert workspace_edit.finish(str(project), SESSION)["files"] == []
    # A session edits at most EDIT_MAX_FILES files.
    monkeypatch.setattr(workspace_edit, "EDIT_MAX_FILES", 1)
    _edit(daemon, project, path="src/notes.txt", search="one", replace="1")
    with pytest.raises(workspace_read.ReadError) as limit:
        _edit(
            daemon, project, path="src/greet.py", search="VERSION = 1", replace="V = 1"
        )
    assert limit.value.status == 409
    workspace_edit.finish(str(project), SESSION)


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


# --- the toolbox -------------------------------------------------------------


class _Requester:
    """NATS, synchronously: each subject handed to the daemon's function."""

    def __init__(self, daemon):
        self.daemon = daemon
        self.subjects = []

    def __call__(self, subject, payload, timeout):
        self.subjects.append(subject)
        root, resolve = payload["workspace_root"], self.daemon._resolve_target_path
        try:
            if subject == "workspace.read":
                return workspace_read.read_file(
                    root, payload.get("path"), payload.get("start_line"), resolve
                )
            if subject == "workspace.search":
                return workspace_read.search(
                    root, payload.get("pattern"), payload.get("path"), resolve
                )
            if subject == "workspace.find":
                return workspace_read.find_files(
                    root, payload.get("pattern"), payload.get("path"), resolve
                )
            if subject == "workspace.edit":
                return workspace_edit.edit_file(root, payload, resolve)
            if subject == "workspace.edit.finish":
                return workspace_edit.finish(root, payload.get("session"))
        except workspace_read.ReadError as exc:
            raise developer_tools.DaemonReplyError(exc.message) from exc
        raise AssertionError(subject)


def _call(name, **arguments):
    return {
        "id": f"c_{name}",
        "type": "function",
        "function": {"name": name, "arguments": json.dumps(arguments)},
    }


def test_the_toolbox_offers_the_edit_only_where_edits_may_go(project, daemon):
    plain = developer_tools.WorkspaceToolbox(_Requester(daemon), str(project))
    names = [s["function"]["name"] for s in plain.schemas()]
    assert "workspace__find_file" in names and "workspace__edit" not in names
    refused, text = plain.call(_call("workspace__edit", path="src/notes.txt"), 1)
    assert refused["reason"] == "unknown tool" and text == mcp_tools.NO_TOOL_NOTE
    editing = developer_tools.WorkspaceToolbox(
        _Requester(daemon), str(project), can_edit=True
    )
    assert [s["function"]["name"] for s in editing.schemas()][-1] == "workspace__edit"
    assert editing.tools["workspace__edit"]["read_only"] is False
    assert editing.tools["workspace__read"]["read_only"] is True


def test_an_edit_through_the_toolbox_is_an_observation_and_comes_back_to_deliver(
    project, daemon
):
    requester = _Requester(daemon)
    box = developer_tools.WorkspaceToolbox(
        requester, str(project), can_edit=True, edit_suffixes={".py", ".json"}
    )
    target = project / "src" / "greet.py"
    original = target.read_bytes()
    observation, text = box.call(
        _call(
            "workspace__edit",
            path="src/greet.py",
            search="VERSION = 1",
            replace="VERSION = 9",
        ),
        1,
    )
    assert observation["tool"] == "edit" and observation["read_only"] is False
    assert observation["is_error"] is False and observation["path"] == "src/greet.py"
    assert observation["sha256_before"] == hashlib.sha256(original).hexdigest()
    assert (
        observation["sha256_after"] == _sha(target)
        and observation["checked"] == "python"
    )
    assert text.startswith("Applied to src/greet.py (checked: python)")
    # A later read sees the edit; the record keeps that file's hash.
    read, text = box.call(_call("workspace__read", path="src/greet.py"), 2)
    assert "VERSION = 9" in text and read["sha256"] == observation["sha256_after"]
    broken, text = box.call(
        _call("workspace__edit", path="src/greet.py", start_line=1, new_text="def ("), 3
    )
    assert broken["is_error"] is True and "would not parse" in text
    wrong_type, text = box.call(
        _call("workspace__edit", path="src/notes.txt", search="one", replace="1"), 3
    )
    assert wrong_type["is_error"] is True and "does not write .txt" in text
    found, text = box.call(_call("workspace__find_file", pattern="*.json"), 4)
    assert found["match_count"] == 1 and text == "src/config.json"
    # The workspace edit never raises the tier: its change reaches the disk
    # only through the delivery, classified by its paths.
    low = {"tier": "low", "reasons": []}
    assert risk.raise_for_tool_calls(low, [observation]) == low
    box.close()
    assert target.read_bytes() == original
    assert requester.subjects[-1] == "workspace.edit.finish"
    assert [f["path"] for f in box.edited_files] == ["src/greet.py"]
    assert "VERSION = 9" in box.edited_files[0]["content"]
    targets = workspace_edits.with_tool_edits(
        [{"path": "README.md", "content": "# x\n"}], box.edited_files
    )
    assert [(t["path"], t.get("source")) for t in targets] == [
        ("README.md", None),
        ("src/greet.py", "workspace_edit"),
    ]
    # A file the answer delivers itself keeps its own block.
    own = [{"path": "src/greet.py", "content": "X = 1\n"}]
    assert workspace_edits.with_tool_edits(own, box.edited_files) == own
    # Nothing edited: close asks the daemon nothing.
    quiet = developer_tools.WorkspaceToolbox(requester, str(project), can_edit=True)
    calls = len(requester.subjects)
    quiet.close()
    assert len(requester.subjects) == calls and quiet.edited_files == []


def test_edits_go_to_the_task_s_worktree_or_a_project_without_the_git_workflow(
    sqlite_session, tmp_path
):
    engine = sqlite_session.get_bind()
    company = Company(company_name=f"Edits Co {tmp_path.name}", git_workflow=False)
    branches = Company(company_name=f"Branches Co {tmp_path.name}", git_workflow=True)
    sqlite_session.add_all([company, branches])
    sqlite_session.commit()
    bridge = {"resolve_root": lambda company_id: "/proj"}
    assert workspace_edits.allowed(bridge, company.id, "/proj", engine) is True
    # The person's checkout of a company that delivers on branches: never.
    assert workspace_edits.allowed(bridge, branches.id, "/proj", engine) is False
    # Its task's own worktree: yes.
    assert (
        workspace_edits.allowed(
            bridge, branches.id, "/proj/.git/fabrique-worktrees/t", engine
        )
        is True
    )
    assert workspace_edits.allowed(bridge, company.id, "/proj", None) is False
    assert ".py" in workspace_edits.delivery_suffixes()


# --- the worker: the edits are delivered and verified like any file --------


class _DaemonOverNATS:
    jetstream_enabled = False

    def __init__(self, daemon):
        self.daemon = daemon

    async def request(self, subject, payload, timeout=5.0):
        assert subject == "workspace.generate", subject
        return {
            "ok": True,
            "files": self.daemon.write_files(
                payload["workspace_root"], payload["files"]
            ),
        }

    async def publish(self, subject, payload, msg_id=None):
        pass


def test_the_worker_delivers_the_tool_edits_and_the_daemon_sees_a_modified_file(
    project, daemon, monkeypatch, sqlite_session, tmp_path
):
    bind = sqlite_session.get_bind()
    ProjectDiscovery.__table__.create(bind, checkfirst=True)
    monkeypatch.setattr(task_worker, "engine", bind)
    monkeypatch.setenv("AUDIT_LOG_PATH", str(tmp_path / "audit"))
    monkeypatch.setattr(settings, "DELIVERY_TESTS_ENABLED", False)
    company = Company(company_name=f"Deliver Co {tmp_path.name}")
    sqlite_session.add(company)
    sqlite_session.commit()
    sqlite_session.add(
        ProjectDiscovery(company_id=company.id, project_path=str(project))
    )
    sprint = Sprint(company_id=company.id, sprint_name="T", scope_size="S")
    sqlite_session.add(sprint)
    sqlite_session.commit()
    item = BacklogItem(sprint_id=sprint.id, title="Version", status="in_progress")
    sqlite_session.add(item)
    sqlite_session.commit()

    box = developer_tools.WorkspaceToolbox(
        _Requester(daemon), str(project), can_edit=True
    )
    edit, _text = box.call(
        _call(
            "workspace__edit",
            path="src/greet.py",
            search="VERSION = 1",
            replace="VERSION = 2",
        ),
        1,
    )
    box.close()
    agent = {
        "role": "Developer",
        "served_model": "m",
        "tool_calls": [edit],
        "tool_edits": box.edited_files,
    }
    asyncio.run(
        task_worker._deliver_artifacts(
            _DaemonOverNATS(daemon),
            {"id": item.id, "title": item.title},
            agent,
            {"id": sprint.id, "company_id": company.id},
            "Bumped the version with the edit tool.",
        )
    )
    assert "VERSION = 2" in (project / "src" / "greet.py").read_text()
    sqlite_session.expire_all()
    run = sqlite_session.exec(
        select(AgentRun).where(
            AgentRun.company_id == company.id,
            AgentRun.external_ref == f"task-{item.id}",
        )
    ).first()
    verification = sqlite_session.exec(
        select(Verification).where(Verification.agent_run_id == run.id)
    ).first()
    writes = {w["path"]: w for w in verification.evidence["workspace_writes"]}
    # Restored before the delivery, so the daemon's diff shows the change.
    assert writes["src/greet.py"]["status"] == "written"
    assert writes["src/greet.py"]["change"] == "modified"
    assert writes["src/greet.py"]["sha256"] == edit["sha256_after"]
    assert verification.verdict == "verified"


def test_the_history_check_refuses_an_edit_recorded_without_its_hashes():
    from history_check import _run_facts
    from models.agent_run import Verification

    def facts(*calls):
        verification = Verification(
            company_id=1,
            agent_run_id=1,
            verdict="verified",
            checks=[],
            evidence={"tool_calls": list(calls)},
        )
        return _run_facts(verification)["reads_without_hash"]

    edit = {"server": "workspace", "tool": "edit", "path": "src/a.py"}
    assert facts(edit | {"sha256_before": "aa", "sha256_after": "bb"}) == []
    assert facts(edit | {"sha256_before": "aa"}) == ["src/a.py"]
    assert facts(edit | {"is_error": True}) == []
