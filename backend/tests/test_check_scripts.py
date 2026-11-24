"""A check script the project committed, run through ``workspace__run_tests``.

Sprint 6 (2026-11-24): a QA criterion said "run scripts/check_file_sizes.py";
the tool could only run the folder's test runner, and QA at last tried to
overwrite the script. ``services/tools/check_scripts.py`` runs exactly
``python3 scripts/<name>.py [args]`` when the script is committed on the
default branch and unchanged on disk; anything else keeps the substitution.

The daemon's ``workspace.git.read_at`` and ``workspace.hash`` run for real
against a git repository in ``tmp_path``; ``workspace.execute`` is recorded.
"""

from __future__ import annotations

import hashlib
import json
import subprocess
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "app"))

import file_hash  # the daemon's modules, on sys.path via conftest
import git_read_at
from developer_tools_helpers import _call, _load_daemon
from services.tools import check_scripts, developer_tools

SCRIPT = "print('3 files over the limit: 0')\n"
SUITE = "python -m pytest -q"


def _git(root: Path, *args: str) -> None:
    subprocess.run(
        ["git", "-c", "user.name=t", "-c", "user.email=t@example.invalid", *args],
        cwd=root,
        check=True,
        capture_output=True,
    )


@pytest.fixture()
def repo(tmp_path):
    root = tmp_path / "proj"
    (root / "scripts").mkdir(parents=True)
    (root / "scripts" / "check_file_sizes.py").write_text(SCRIPT)
    (root / "tests").mkdir()
    (root / "tests" / "test_x.py").write_text("def test_x():\n    pass\n")
    _git(root, "init", "-q", "-b", "main")
    _git(root, "add", ".")
    _git(root, "commit", "-q", "-m", "init")
    return root


class _Daemon:
    """NATS, synchronously: the daemon's read_at and hash for real."""

    def __init__(self, executes=None):
        self.resolve = _load_daemon()._resolve_target_path
        self.executes = executes or {}
        self.calls = []

    def __call__(self, subject, payload, timeout):
        self.calls.append((subject, payload))
        root = payload["workspace_root"]
        if subject == "workspace.git.read_at":
            return git_read_at.read_at(
                root, payload.get("base") or "", "", payload.get("paths")
            )
        if subject == "workspace.hash":
            return {"files": file_hash.hash_files(root, payload["paths"], self.resolve)}
        if subject == "workspace.execute":
            return self.executes.get(
                payload["command"], {"exit_code": 1, "stdout": "no such command\n"}
            )
        raise AssertionError(subject)

    def executed(self):
        return [p["command"] for s, p in self.calls if s == "workspace.execute"]


def _box(root, daemon, **kwargs):
    return developer_tools.WorkspaceToolbox(
        daemon, str(root), test_command=SUITE, **kwargs
    )


def test_a_committed_unchanged_script_runs_as_asked(repo):
    command = "python3 scripts/check_file_sizes.py --max=600"
    daemon = _Daemon(
        {
            command: {"exit_code": 0, "stdout": "0 over\n", "stderr": ""},
            SUITE: {"exit_code": 0, "stdout": "1 passed\n"},
        }
    )
    box = _box(repo, daemon, sandbox={"mode": "bwrap"})
    observation, text = box.call(_call("workspace__run_tests", command=command), 1)
    assert daemon.executed() == [command]
    execute = next(p for s, p in daemon.calls if s == "workspace.execute")
    assert execute["sandbox"] == {"mode": "bwrap"}
    assert observation["command"] == command and observation["folder"] == ""
    assert observation["exit_code"] == 0 and not observation["is_error"]
    record = observation["check_script"]
    assert record["path"] == "scripts/check_file_sizes.py"
    # Computed here over the committed bytes, equal to the daemon's disk hash.
    assert record["sha256"] == hashlib.sha256(SCRIPT.encode()).hexdigest()
    assert record["sha256_on_disk"] == record["sha256"]
    assert len(record["commit"]) == 40 and "refused" not in record
    assert text.startswith(
        f"[ran `{command}`, the committed scripts/check_file_sizes.py"
    )
    assert text.endswith("exit status 0\n0 over\n")
    json.dumps(observation)  # recorded on the evidence as it is


def test_a_script_this_task_changed_is_not_run(repo):
    """QA's last attempt: overwrite the check to make it pass."""
    (repo / "scripts" / "check_file_sizes.py").write_text("print('ok')\n")
    daemon = _Daemon({SUITE: {"exit_code": 0, "stdout": "1 passed\n"}})
    observation, text = _box(repo, daemon).call(
        _call("workspace__run_tests", command="python3 scripts/check_file_sizes.py"), 1
    )
    assert daemon.executed() == [SUITE]
    assert observation["command"] == SUITE
    assert "not the committed file" in observation["check_script"]["refused"]
    assert text.startswith(
        "[`python3 scripts/check_file_sizes.py` was not run: "
        "scripts/check_file_sizes.py on disk is not the committed file"
    )
    assert f"it ran `{SUITE}` instead" in text


def test_a_script_not_committed_on_the_default_branch_is_not_run(repo):
    (repo / "scripts" / "new_check.py").write_text("print('mine')\n")
    daemon = _Daemon({SUITE: {"exit_code": 0, "stdout": "1 passed\n"}})
    observation, text = _box(repo, daemon).call(
        _call("workspace__run_tests", command="python scripts/new_check.py"), 1
    )
    assert daemon.executed() == [SUITE]
    assert "not a file committed on the default branch" in text
    assert observation["check_script"]["refused"]


@pytest.mark.parametrize(
    "command",
    [
        "python3 scripts/../backend/app/main.py",
        "python3 tools/check_file_sizes.py",
        "python3 scripts/sub/check.py",
        "python3 /abs/scripts/check_file_sizes.py",
        "python3 scripts/check_file_sizes.py; rm -rf /",
        "python3 scripts/check_file_sizes.py && id",
        "python3 scripts/check_file_sizes.py | tee out",
        "python3 scripts/check_file_sizes.py > out",
        "python3 scripts/check_file_sizes.py $(id)",
        "python3 scripts/check_file_sizes.py `id`",
        "python3 scripts/check_file_sizes.py 'a b'",
        "python3 scripts/check_file_sizes.py %PATH%",
        "python3 scripts/check_file_sizes.py *",
        "python3 scripts/check_file_sizes.py ../secrets",
        "python3 scripts/check_file_sizes.py --root=../..",
        "python3 scripts/check_file_sizes.py --root=/etc",
        "python3 scripts/check_file_sizes.py /etc/passwd",
        "python3 scripts/check_file_sizes.py\nid",
        "python3\tscripts/check_file_sizes.py",
        "python3 -c print(1)",
        "bash scripts/check.sh",
        "python3",
    ],
)
def test_anything_else_is_not_a_check_script_and_keeps_the_substitution(repo, command):
    assert check_scripts.parse(command) is None
    daemon = _Daemon({SUITE: {"exit_code": 0, "stdout": "1 passed\n"}})
    observation, text = _box(repo, daemon).call(
        _call("workspace__run_tests", command=command), 1
    )
    assert daemon.executed() == [SUITE]
    assert "check_script" not in observation
    assert not any(s == "workspace.git.read_at" for s, _p in daemon.calls)
    assert f"it ran `{SUITE}` instead" in text


def test_the_command_line_is_rebuilt_from_plain_words():
    assert check_scripts.parse(
        "  python3  scripts/check_x.py  -q --max=600 a/b.py "
    ) == (
        "python3",
        "scripts/check_x.py",
        ["-q", "--max=600", "a/b.py"],
    )


def test_an_unreadable_sandbox_refuses_the_check(repo):
    daemon = _Daemon()
    observation, text = _box(repo, daemon, sandbox_error="no policy").call(
        _call("workspace__run_tests", command="python3 scripts/check_file_sizes.py"), 1
    )
    assert observation["is_error"] and "no policy" in text
    assert daemon.executed() == []


def test_a_daemon_without_the_hash_does_not_run_the_check(repo, monkeypatch):
    monkeypatch.setattr(
        check_scripts.daemon_liveness,
        "announces",
        lambda subject, feature=None: subject != "workspace.hash",
    )
    daemon = _Daemon({SUITE: {"exit_code": 0, "stdout": "1 passed\n"}})
    observation, _text = _box(repo, daemon).call(
        _call("workspace__run_tests", command="python3 scripts/check_file_sizes.py"), 1
    )
    assert daemon.executed() == [SUITE]
    assert "does not announce workspace.hash" in observation["check_script"]["refused"]
