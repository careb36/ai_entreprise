"""``workspace__git``: read-only git for the roles that read the project (issue #766).

An audit of real runs: Developers asked ``workspace__run_tests`` for ``git
status``/``git log``/``git diff`` (the suite ran instead), and QA, told to run
``git diff --name-only``, wrote that without it it could not know which files
to verify. Here the tool runs against a real git repository in ``tmp_path``:
the daemon's ``git_view`` and ``git_read_at`` for real, through a synchronous
NATS fake. Each operation is offered only when every daemon announces it.
"""

from __future__ import annotations

import asyncio
import json
import subprocess
import sys
from pathlib import Path

import pytest
from jsonschema import Draft202012Validator

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "app"))

import abilities  # the daemon's modules, on sys.path via conftest
import git_diff
import git_read_at
import git_view
from developer_tools_helpers import _call
from services.tools import developer_tools, workspace_edits, workspace_git
from services.workspace import daemon_liveness

SCHEMA = json.loads(
    (
        Path(__file__).resolve().parents[2]
        / "vscode-daemon/protocol/schemas/v1/workspace.git.diff.json"
    ).read_text()
)
GIT_ABILITIES = {
    "abilities_version": 1,
    "subjects": ["workspace.git.diff", "workspace.git.read_at"],
    "features": {"workspace.git.diff": ["forge_policy", "view"]},
}


def _validator(part: str) -> Draft202012Validator:
    return Draft202012Validator({**SCHEMA, "$ref": f"#/$defs/{part}"})


def _git(root: Path, *args: str) -> None:
    subprocess.run(
        ["git", "-c", "user.name=t", "-c", "user.email=t@example.invalid", *args],
        cwd=root,
        check=True,
        capture_output=True,
    )


@pytest.fixture()
def repo(tmp_path):
    """``main`` with two files; ``fabrique/task-1`` changes one in a commit,
    another on disk only, and adds an untracked file."""
    root = tmp_path / "proj"
    (root / "src").mkdir(parents=True)
    (root / "src" / "app.py").write_text("VALUE = 1\n")
    (root / "src" / "same.py").write_text("SAME = 1\n")
    (root / "README.md").write_text("# Proj\n")
    _git(root, "init", "-q", "-b", "main")
    _git(root, "add", ".")
    _git(root, "commit", "-q", "-m", "init")
    _git(root, "checkout", "-q", "-b", "fabrique/task-1")
    (root / "src" / "app.py").write_text("VALUE = 2\n")
    _git(root, "commit", "-q", "-am", "Change the value")
    (root / "README.md").write_text("# Proj\n\npassword = 'hunter2hunter2'\n")
    (root / "src" / "new.py").write_text("NEW = 1\n")
    return root


class _Daemon:
    """NATS, synchronously: the daemon's view and read_at for real."""

    def __init__(self, old: bool = False):
        self.calls: list[tuple[str, dict]] = []
        self.old = old

    def __call__(self, subject, payload, timeout):
        self.calls.append((subject, payload))
        root = payload["workspace_root"]
        if subject == "workspace.git.diff":
            if self.old:  # A daemon from before the views: an integration diff.
                return {"ok": True, "files": []}
            _validator("request").validate(payload)
            reply = git_view.view(root, payload)
            _validator("reply").validate(reply)
            return reply
        if subject == "workspace.git.read_at":
            return git_read_at.read_at(
                root, payload.get("base") or "", "", payload.get("paths")
            )
        raise AssertionError(subject)


@pytest.fixture()
def announced(monkeypatch):
    daemon_liveness.reset()
    daemon_liveness.observe_heartbeat({"daemon_id": "d1", "abilities": GIT_ABILITIES})
    yield
    daemon_liveness.reset()


def _box(repo, daemon=None, base="main"):
    box = developer_tools.WorkspaceToolbox(daemon or _Daemon(), str(repo))
    workspace_git.offer(box)
    box.git_base = base
    return box


def _git_call(box, **arguments):
    return box.call(_call("workspace__git", **arguments), 1)


def test_every_operation_answers_from_the_real_repository(repo, announced):
    box = _box(repo)
    assert "workspace__git" in box.tools and box.tools["workspace__git"]["read_only"]

    obs, text = _git_call(box, operation="status")
    assert not obs["is_error"] and obs["read_only"] and obs["git_operation"] == "status"
    assert " M README.md" in text and "?? src/new.py" in text

    obs, text = _git_call(box, operation="changed")
    assert obs["base"] == "main" and len(obs["merge_base"]) == 40
    assert "modified: src/app.py" in text  # committed on the task's branch
    assert "modified: README.md" in text  # on disk only
    assert "untracked: src/new.py" in text
    assert "same.py" not in text

    obs, text = _git_call(box, operation="diff", path="src/app.py")
    assert obs["path"] == "src/app.py"
    assert "-VALUE = 1" in text and "+VALUE = 2" in text

    obs, text = _git_call(box, operation="log", count=50)
    assert box.requester.calls[-1][1]["limit"] == 20
    assert "Change the value" in text and "init" in text

    obs, text = _git_call(box, operation="show_base", path="src/app.py")
    assert "VALUE = 1" in text and "VALUE = 2" not in text
    assert obs["base"] == "main" and len(obs["sha256"]) == 64
    assert obs["read_only"] is True


def test_a_diff_is_redacted_and_a_new_file_is_named_untracked(repo, announced):
    box = _box(repo)
    _, text = _git_call(box, operation="diff", path="README.md")
    assert "hunter2hunter2" not in text and "redacted" in text
    _, text = _git_call(box, operation="diff", path="src/new.py")
    assert "does not track yet" in text


@pytest.mark.parametrize(
    "path", ["../outside.py", "/etc/passwd", ".git/config", ".env", "C:/x.py", ""]
)
def test_a_path_outside_the_repository_or_a_secret_is_refused(repo, announced, path):
    daemon = _Daemon()
    box = _box(repo, daemon)
    for operation in ("diff", "show_base"):
        obs, text = _git_call(box, operation=operation, path=path)
        assert obs["is_error"] and text.startswith("Error:")
    assert daemon.calls == []  # refused before the daemon was asked


@pytest.mark.parametrize(
    "path", ["../outside.py", "/etc/passwd", ".git/config", ".env"]
)
def test_the_daemon_refuses_a_path_outside_the_repository_too(repo, path):
    with pytest.raises(ValueError):
        git_view.view(str(repo), {"view": "diff", "base": "main", "path": path})


def test_the_daemon_refuses_a_symlink_that_leads_out(repo, tmp_path):
    (tmp_path / "secret.txt").write_text("x\n")
    (repo / "out").symlink_to(tmp_path)
    with pytest.raises(ValueError):
        git_view.view(str(repo), {"view": "diff", "path": "out/secret.txt"})


def test_nothing_is_offered_until_every_daemon_announces_it(repo):
    daemon_liveness.reset()
    try:
        box = _box(repo)  # Nothing heard yet (right after a restart).
        assert "workspace__git" not in box.tools
        obs, _ = _git_call(box, operation="status")
        assert obs["is_error"] and obs["reason"] == "unknown tool"
        # A daemon that announces read_at but not the views: show_base only.
        older = {**GIT_ABILITIES, "features": {"workspace.git.diff": ["forge_policy"]}}
        daemon_liveness.observe_heartbeat({"daemon_id": "d1", "abilities": older})
        box = _box(repo)
        [schema] = [
            s for s in box.schemas() if s["function"]["name"] == "workspace__git"
        ]
        enum = schema["function"]["parameters"]["properties"]["operation"]["enum"]
        assert enum == ["show_base"]
        obs, text = _git_call(box, operation="status")
        assert obs["is_error"] and "show_base" in text
        # Two daemons that disagree: not announced.
        daemon_liveness.observe_heartbeat(
            {"daemon_id": "d2", "abilities": GIT_ABILITIES}
        )
        assert "status" not in workspace_git.offered_operations()
    finally:
        daemon_liveness.reset()


def test_an_older_daemons_integration_diff_is_never_read_as_a_view(repo, announced):
    box = _box(repo, _Daemon(old=True))
    obs, text = _git_call(box, operation="changed")
    assert obs["is_error"] and "does not serve" in text


def test_the_tool_is_in_the_runs_offered_tools(repo, announced):
    assert "workspace__git" in workspace_edits.offered(_box(repo))["names"]


def test_the_daemon_announces_the_views_and_routes_them(repo):
    assert "view" in abilities.FEATURES["workspace.git.diff"]
    replies = []

    async def reply(_nc, _msg, body):
        replies.append(body)

    request = {"workspace_root": str(repo), "view": "log", "limit": 1}
    asyncio.run(git_diff.handle_diff(None, None, reply, lambda _m: request))
    assert replies[0]["view"] == "log" and len(replies[0]["commits"]) == 1
    _validator("reply").validate(replies[0])
    bad = {"workspace_root": str(repo), "view": "diff", "path": "../x"}
    asyncio.run(git_diff.handle_diff(None, None, reply, lambda _m: bad))
    assert replies[1]["error"]["status"] == 400
