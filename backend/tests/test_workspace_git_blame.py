"""``workspace__git`` ``blame``: when and why lines changed, never who (issue #766).

Documenting a whole project needs to say when and why a part changed
(docs/design/agent-toolsets.md, "Next changes" item 10). The blame operation
runs the daemon's ``git_view.blame`` for real on a repository in ``tmp_path``
through a synchronous NATS fake: the model sees the commit, date and subject of
each group of lines, and no author's or committer's name or e-mail reaches the
model or the run's evidence. Offered only when every daemon announces ``blame``.
"""

from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path

import pytest
from jsonschema import Draft202012Validator

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "app"))

import git_view  # the daemon's module, on sys.path via conftest
from developer_tools_helpers import _call
from services.tools import developer_tools, workspace_git
from services.workspace import daemon_liveness

SCHEMA = json.loads(
    (
        Path(__file__).resolve().parents[2]
        / "vscode-daemon/protocol/schemas/v1/workspace.git.diff.json"
    ).read_text()
)
NAME, MAIL = "Grace Example", "grace.example@example.invalid"
FEATURES = ["forge_policy", "view", "blame"]


def _abilities(features):
    return {
        "abilities_version": 1,
        "subjects": ["workspace.git.diff", "workspace.git.read_at"],
        "features": {"workspace.git.diff": features},
    }


def _validator(part: str) -> Draft202012Validator:
    return Draft202012Validator({**SCHEMA, "$ref": f"#/$defs/{part}"})


def _git(root: Path, *args: str) -> None:
    subprocess.run(
        ["git", "-c", f"user.name={NAME}", "-c", f"user.email={MAIL}", *args],
        cwd=root,
        check=True,
        capture_output=True,
    )


@pytest.fixture()
def repo(tmp_path):
    root = tmp_path / "proj"
    (root / "src").mkdir(parents=True)
    (root / "src" / "app.py").write_text("A = 1\nB = 1\n")
    _git(root, "init", "-q", "-b", "main")
    _git(root, "add", ".")
    _git(root, "commit", "-q", "-m", "Start the app")
    (root / "src" / "app.py").write_text("A = 1\nB = 2\n")
    _git(root, "commit", "-q", "-am", "Raise B, password = 'hunter2hunter2'")
    return root


class _Daemon:
    def __init__(self, extra_fields=None):
        self.calls: list[tuple[str, dict]] = []
        self.extra_fields = extra_fields or {}

    def __call__(self, subject, payload, timeout):
        assert subject == "workspace.git.diff"
        self.calls.append((subject, payload))
        _validator("request").validate(payload)
        reply = git_view.view(payload["workspace_root"], payload)
        _validator("reply").validate(reply)
        for group in reply.get("groups") or []:
            group.update(self.extra_fields)
        return reply


@pytest.fixture()
def heard():
    def hear(features):
        daemon_liveness.reset()
        daemon_liveness.observe_heartbeat(
            {"daemon_id": "d1", "abilities": _abilities(features)}
        )

    yield hear
    daemon_liveness.reset()


def _box(repo, daemon):
    box = developer_tools.WorkspaceToolbox(daemon, str(repo))
    workspace_git.offer(box)
    return box


def test_blame_shows_commit_date_and_subject_per_group(repo, heard):
    heard(FEATURES)
    daemon = _Daemon()
    obs, text = _box(repo, daemon).call(
        _call("workspace__git", operation="blame", path="src/app.py"), 1
    )
    assert not obs["is_error"] and obs["read_only"] and obs["git_operation"] == "blame"
    assert "1-1:" in text and "Start the app" in text
    assert (
        "2-2:" in text and "redacted" in text
    )  # the second subject looks like a secret
    assert daemon.calls[0][1]["start_line"] == 1
    assert obs["blame"][0]["subject"] == "Start the app"
    assert len(obs["blame"][1]["commit"]) == 40 and obs["blame"][1]["date"]


def test_blame_returns_no_author_names_or_emails(repo, heard):
    heard(FEATURES)
    # Even a daemon that sent author fields: only the named fields are kept.
    daemon = _Daemon({"author": NAME, "author_mail": MAIL, "committer": NAME})
    obs, text = _box(repo, daemon).call(
        _call("workspace__git", operation="blame", path="src/app.py"), 1
    )
    assert not obs["is_error"] and len(obs["blame"]) == 2
    recorded = json.dumps(obs)
    for secret in (NAME, MAIL, "Grace"):
        assert secret not in text and secret not in recorded
    # A subject that looks like a secret is redacted, in the text and the record.
    assert "hunter2hunter2" not in text and "hunter2hunter2" not in recorded


def test_blame_is_bounded_before_the_daemon(repo, heard):
    heard(FEATURES)
    daemon = _Daemon()
    _box(repo, daemon).call(
        _call(
            "workspace__git",
            operation="blame",
            path="src/app.py",
            start_line=2,
            end_line=9,
        ),
        1,
    )
    assert (daemon.calls[0][1]["start_line"], daemon.calls[0][1]["end_line"]) == (2, 9)


def test_blame_is_offered_only_when_announced(repo, heard):
    heard(["forge_policy", "view"])  # An older daemon: the views, no blame.
    daemon = _Daemon()
    box = _box(repo, daemon)
    assert "blame" not in box.git_operations and "log" in box.git_operations
    obs, _ = box.call(_call("workspace__git", operation="blame", path="src/app.py"), 1)
    assert obs["is_error"] and daemon.calls == []


@pytest.mark.parametrize("path", ["../x.py", "/etc/passwd", ".git/config", ".env"])
def test_blame_refuses_a_path_outside_or_secret(repo, heard, path):
    heard(FEATURES)
    daemon = _Daemon()
    obs, _ = _box(repo, daemon).call(
        _call("workspace__git", operation="blame", path=path), 1
    )
    assert obs["is_error"] and daemon.calls == []
