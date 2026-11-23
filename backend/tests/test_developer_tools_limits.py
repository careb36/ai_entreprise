"""The limits of the Developer's tools (the review's findings).

A narrower test command must be the same runner exactly; files that may hold
secrets are never read nor searched and a secret-looking line is redacted; a
symlink out of the project is never opened; a large file is hashed whole but
only its head read; a search is bounded in time and files; a test run with no
sprint is reported to nobody; the tools look at the task's worktree once it
delivered there.

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
from developer_tools_helpers import _call, _load_daemon, _Requester
from models.agent_run import AgentRun
from models.company import Company
from services.evidence import context_read
from services.tools import developer_tools

# The shared fixtures.
project = developer_tools_helpers.project


def test_a_narrower_test_command_must_be_the_same_runner_exactly(project):
    daemon = _load_daemon()
    requester = _Requester(
        daemon,
        {"python -m pytest -q": {"exit_code": 0, "stdout": "ok\n", "stderr": ""}},
    )
    box = developer_tools.WorkspaceToolbox(
        requester, str(project), test_command="python -m pytest -q"
    )
    for wanted in (
        "python -m pytest_custom",
        "python -m pytestx tests",
        "python -m pytest\ttests",
    ):
        observation, _text = box.call(_call("workspace__run_tests", command=wanted), 1)
        assert observation["command"] == "python -m pytest -q", wanted
    exact, _text = box.call(
        _call("workspace__run_tests", command="python -m pytest"), 1
    )
    assert exact["command"] == "python -m pytest"
    assert developer_tools._runs_with("npx vitest run x", "npx vitest")
    assert not developer_tools._runs_with("npx vitest-custom run", "npx vitest")


def test_files_that_may_hold_secrets_are_never_read_nor_searched(project):
    daemon = _load_daemon()
    (project / ".env").write_text(
        "OPENAI_API_KEY=" + "sk-live-" + "abcdefghijklmnopqrstuvwxyz123456\n"
    )
    (project / "deploy.pem").write_text(
        "-----BEGIN RSA "
        + "PRIVATE KEY-----\nabc\n-----END RSA "
        + "PRIVATE KEY-----\n"
    )
    (project / ".ssh").mkdir()
    (project / ".ssh" / "config").write_text("Host x\n")
    (project / "config").mkdir()
    (project / "config" / "secrets.yaml").write_text("token: abc\n")
    for name in (".env", "deploy.pem", ".ssh/config", "config/secrets.yaml"):
        with pytest.raises(workspace_read.ReadError) as refused:
            workspace_read.read_file(
                str(project), name, None, daemon._resolve_target_path
            )
        assert refused.value.status == 403, name
    found = workspace_read.search(
        str(project), "PRIVATE KEY|OPENAI|token", None, daemon._resolve_target_path
    )
    assert found["matches"] == []
    # An explicit search on the file itself is refused the same way: nothing is opened.
    direct = workspace_read.search(
        str(project), "KEY", "deploy.pem", daemon._resolve_target_path
    )
    assert direct["matches"] == [] and direct["files_scanned"] == 0


# Built at run time so the source holds no credential-shaped literal.
_FAKE_AWS_KEY = "AKIA" + "ABCDEFGHIJKLMNOP"


def test_a_secret_looking_line_in_a_readable_file_is_redacted_for_the_model(project):
    daemon = _load_daemon()
    (project / "src" / "settings.py").write_text(
        f"DEBUG = True\nAWS_KEY = '{_FAKE_AWS_KEY}'\nNAME = 'x'\n"
    )
    box = developer_tools.WorkspaceToolbox(_Requester(daemon), str(project))
    observation, text = box.call(_call("workspace__read", path="src/settings.py"), 1)
    assert (
        _FAKE_AWS_KEY not in text and "[line redacted: it looks like a secret]" in text
    )
    assert "DEBUG = True" in text and observation["sha256"]
    _search, text = box.call(_call("workspace__search", pattern="AKIA"), 1)
    assert _FAKE_AWS_KEY not in text and "redacted" in text
    assert context_read.redact_secrets("a.py", "x = 1\ny = 2") == "x = 1\ny = 2"


def test_a_symlink_that_leads_out_of_the_project_is_never_opened(project, tmp_path):
    daemon = _load_daemon()
    outside = tmp_path / "outside.txt"
    outside.write_text("the secret word is pelican\n")
    (project / "src" / "link.txt").symlink_to(outside)
    found = workspace_read.search(
        str(project), "pelican", None, daemon._resolve_target_path
    )
    assert found["matches"] == []
    with pytest.raises(workspace_read.ReadError) as refused:
        workspace_read.read_file(
            str(project), "src/link.txt", None, daemon._resolve_target_path
        )
    assert refused.value.status == 400
    # A symlink inside the project is fine.
    (project / "src" / "alias.py").symlink_to(project / "src" / "greet.py")
    read = workspace_read.read_file(
        str(project), "src/alias.py", None, daemon._resolve_target_path
    )
    assert read["text"].startswith("    1| def greet")


def test_a_large_file_is_hashed_whole_but_only_its_head_is_read(project, monkeypatch):
    daemon = _load_daemon()
    monkeypatch.setattr(workspace_read, "READ_SCAN_BYTES", 64)
    body = "".join(f"line {i}\n" for i in range(100))
    (project / "src" / "big.txt").write_text(body)
    read = workspace_read.read_file(
        str(project), "src/big.txt", None, daemon._resolve_target_path
    )
    assert read["sha256"] == hashlib.sha256(body.encode()).hexdigest() and read[
        "bytes"
    ] == len(body)
    assert read["truncated"] is True and read["lines_indexed_to_byte"] == 64
    assert len(read["text"].splitlines()) <= 10
    beyond = workspace_read.read_file(
        str(project), "src/big.txt", 90, daemon._resolve_target_path
    )
    assert beyond["text"] == "" and beyond["truncated"] is True


def test_a_search_that_backtracks_forever_is_stopped_at_the_deadline(project):
    daemon = _load_daemon()
    (project / "src" / "evil.txt").write_text("a" * 40 + "b\n")
    with pytest.raises(workspace_read.ReadError) as stopped:
        workspace_read.search_bounded(
            str(project), r"(a+)+$", None, daemon._resolve_target_path, timeout=1.5
        )
    assert stopped.value.status == 408 and "stopped" in stopped.value.message
    # A sane search through the same path answers.
    found = workspace_read.search_bounded(
        str(project), "def greet", None, daemon._resolve_target_path
    )
    assert found["matches"] == ["src/greet.py:1: def greet(name):"]
    with pytest.raises(workspace_read.ReadError) as bad:
        workspace_read.search_bounded(
            str(project), "(", None, daemon._resolve_target_path
        )
    assert bad.value.status == 400


def test_a_search_over_more_files_than_the_bound_still_scans_the_bound(
    project, monkeypatch
):
    daemon = _load_daemon()
    # Three files within the bound: README.md, the binary logo (skipped) and
    # src/count.py; the walk found more, so the answer says so.
    monkeypatch.setattr(workspace_read, "SEARCH_MAX_FILES", 3)
    found = workspace_read.search(
        str(project), "greet|COUNT|Proj", None, daemon._resolve_target_path
    )
    assert found["truncated"] is True and found["files_scanned"] == 2
    assert found["matches"] == ["README.md:1: # Proj", "src/count.py:1: COUNT = 0"]


def test_the_daemon_reports_a_test_run_with_no_sprint_to_nobody(project):
    import asyncio

    daemon = _load_daemon()
    published = []

    class _NC:
        async def publish(self, subject, payload):
            published.append(subject)

    class _Msg:
        def __init__(self, body):
            self.data = json.dumps(body).encode()
            self.subject = "workspace.execute"
            self.reply = "inbox"

    asyncio.run(
        daemon.handle_msg(
            _NC(), _Msg({"command": "echo hi", "workspace_root": str(project)})
        )
    )
    assert published == ["inbox"]
    asyncio.run(
        daemon.handle_msg(
            _NC(),
            _Msg(
                {"command": "echo hi", "workspace_root": str(project), "sprint_id": 4}
            ),
        )
    )
    assert published == ["inbox", "glassroom.4", "inbox"]


def test_the_tools_look_at_the_task_s_worktree_once_it_delivered_there(
    project, sqlite_session, monkeypatch
):
    from models.agent_run import Verification

    company = Company(company_name="Worktree Co")
    sqlite_session.add(company)
    sqlite_session.commit()
    run = AgentRun(
        company_id=company.id,
        external_ref="task-9",
        producer="le-fabrique-task-worker",
        artifact_digest="d",
        artifact_size=1,
        claims={},
    )
    sqlite_session.add(run)
    sqlite_session.commit()
    developer_tools.bind(object(), object(), lambda company_id: str(project))
    try:
        engine = sqlite_session.get_bind()
        assert developer_tools.tool_root(company.id, 9, engine=engine) == str(project)
        sqlite_session.add(
            Verification(
                company_id=company.id,
                agent_run_id=run.id,
                verdict="verified",
                checks=[],
                evidence={
                    "git": {"branch": "fabrique/task-9"},
                    "workspace_root": "/repo/.git/fabrique-worktrees/task-9",
                },
            )
        )
        sqlite_session.commit()
        assert (
            developer_tools.tool_root(company.id, 9, engine=engine)
            == "/repo/.git/fabrique-worktrees/task-9"
        )
        # Without the Git workflow the evidence names the project itself.
        sqlite_session.add(
            Verification(
                company_id=company.id,
                agent_run_id=run.id,
                verdict="verified",
                checks=[],
                evidence={"workspace_root": "/elsewhere"},
            )
        )
        sqlite_session.commit()
        assert developer_tools.tool_root(company.id, 9, engine=engine) == str(project)
        assert developer_tools.tool_root(company.id, None, engine=engine) == str(
            project
        )
    finally:
        developer_tools.unbind()


def test_the_toolbox_passes_the_window_a_read_asks_for(project):
    """A Developer reads 400 lines in one call, within its result budget."""
    daemon = _load_daemon()
    (project / "src" / "long.py").write_text(
        "".join(f"v{i} = {i}\n" for i in range(642))
    )
    requester = _Requester(daemon)
    box = developer_tools.WorkspaceToolbox(requester, str(project))
    box.result_max_chars = 16000
    _observation, text = box.call(
        _call("workspace__read", path="src/long.py", lines=400), 1
    )
    assert requester.calls[-1][1]["lines"] == 400
    assert "[src/long.py: lines 1–400 of 642]" in text
    assert "read on with start_line=401" in text
    _rest, text = box.call(
        _call("workspace__read", path="src/long.py", start_line=401, lines=400), 1
    )
    assert "[src/long.py: lines 401–642 of 642]" in text and "v641 = 641" in text
