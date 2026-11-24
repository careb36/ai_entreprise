"""A delivery that deletes an emptied file (``workspace.generate`` with ``delete``).

Design: ``docs/design/move-and-delete-files.md``. The daemon removes a file
only while it still holds the bytes the edit session saw (``sha256_expected``,
from the daemon's own finish report), never a secret, never a protected
existing document. On a task's branch the deletion is in the commit; without
Git the snapshot keeps the file and an undo writes it back.
"""

from __future__ import annotations

import hashlib
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "vscode-daemon"))
import delivery_snapshot
from test_git_workflow import SPEC, daemon, git

OLD = "def test_old():\n    assert True\n"


def _sha(text: str) -> str:
    return hashlib.sha256(text.encode()).hexdigest()


def _delete(path: str, text: str = OLD) -> dict:
    return {"path": path, "delete": True, "sha256_expected": _sha(text)}


@pytest.fixture(params=["plain", "repository"])
def project(request, tmp_path):
    root = tmp_path / "project"
    (root / "tests").mkdir(parents=True)
    (root / "tests" / "test_old.py").write_text(OLD)
    (root / ".env").write_text("TOKEN=x\n")
    if request.param == "repository":
        git(root, "init", "-q", "-b", "main")
        git(root, "add", "tests")
        git(root, "commit", "-q", "-m", "first")
    return root


def test_a_delete_entry_removes_the_file_it_still_matches(project):
    reply = daemon.deliver_files(
        str(project),
        [
            {"path": "tests/test_new.py", "content": OLD.replace("old", "new")},
            _delete("tests/test_old.py"),
        ],
    )

    statuses = {f["path"]: f["status"] for f in reply["files"]}
    assert statuses == {"tests/test_new.py": "written", "tests/test_old.py": "deleted"}
    assert reply["ok"] is True and not (project / "tests" / "test_old.py").exists()
    deleted = next(f for f in reply["files"] if f["status"] == "deleted")
    assert deleted["sha256_before"] == _sha(OLD)


def test_a_file_changed_since_the_session_is_not_deleted(project):
    (project / "tests" / "test_old.py").write_text(
        "def test_old():\n    assert 'a person saved this'\n"
    )

    reply = daemon.deliver_files(str(project), [_delete("tests/test_old.py")])

    assert reply["files"][0]["status"] == "rejected_delete" and reply["ok"] is False
    assert (project / "tests" / "test_old.py").exists()


def test_a_secret_a_missing_file_or_no_expected_hash_is_never_deleted(project):
    entries = [
        _delete(".env", "TOKEN=x\n"),
        _delete("tests/missing.py"),
        {"path": "tests/test_old.py", "delete": True},
    ]

    reply = daemon.deliver_files(str(project), entries)

    assert [f["status"] for f in reply["files"]] == ["rejected_delete"] * 3
    assert (project / ".env").exists() and (project / "tests" / "test_old.py").exists()


def test_undo_writes_a_deleted_file_back(project):
    if (project / ".git").exists():
        pytest.skip(
            "a delivery on a repository without a branch spec is the plain path"
        )
    reply = daemon.deliver_files(str(project), [_delete("tests/test_old.py")])
    kept = reply["snapshot"]["files"]
    assert kept == [
        {
            "path": "tests/test_old.py",
            "existed": True,
            "before_sha256": _sha(OLD),
            "after_sha256": None,
        }
    ]

    undone = delivery_snapshot.undo(
        str(project), reply["snapshot"]["id"], daemon._resolve_target_path
    )

    assert (project / "tests" / "test_old.py").read_text() == OLD
    assert [r["path"] for r in undone["restored"]] == ["tests/test_old.py"]


def test_an_undo_is_refused_when_the_deleted_file_was_created_again(project):
    if (project / ".git").exists():
        pytest.skip("the snapshot is the plain path's")
    reply = daemon.deliver_files(str(project), [_delete("tests/test_old.py")])
    (project / "tests" / "test_old.py").write_text("x = 1\n")

    with pytest.raises(Exception) as refused:
        delivery_snapshot.undo(
            str(project), reply["snapshot"]["id"], daemon._resolve_target_path
        )

    assert "created again" in str(refused.value.message)
    assert (project / "tests" / "test_old.py").read_text() == "x = 1\n"


def test_on_a_task_branch_the_deletion_is_in_the_commit(tmp_path):
    root = tmp_path / "app"
    (root / "tests").mkdir(parents=True)
    (root / "tests" / "test_old.py").write_text(OLD)
    git(root, "init", "-q", "-b", "main")
    git(root, "add", ".")
    git(root, "commit", "-q", "-m", "first")

    reply = daemon.deliver_files(
        str(root),
        [{"path": "tests/test_new.py", "content": OLD}, _delete("tests/test_old.py")],
        SPEC,
    )

    commit = reply["git"]["commit"]
    changes = git(
        root, "show", "--no-renames", "--name-status", "--format=", commit
    ).split("\n")
    assert "D\ttests/test_old.py" in changes and "A\ttests/test_new.py" in changes
    assert reply["ok"] is True
    # The person's checkout is untouched: the deletion is on the task's branch.
    assert (root / "tests" / "test_old.py").exists()


def test_on_a_task_branch_an_untracked_file_deleted_does_not_break_the_commit(tmp_path):
    """``git add`` of a path Git never tracked fails ("pathspec did not match"):
    a deleted ignored file must not fail the whole delivery (sdl-review)."""
    root = tmp_path / "app"
    root.mkdir()
    (root / ".gitignore").write_text("local_*.py\n")
    (root / "a.py").write_text("a = 1\n")
    git(root, "init", "-q", "-b", "main")
    git(root, "add", ".")
    git(root, "commit", "-q", "-m", "first")
    worktree_file = "local_notes.py"
    first = daemon.deliver_files(
        str(root), [{"path": "b.py", "content": "b = 1\n"}], SPEC
    )
    worktree = Path(first["git"]["worktree"])
    (worktree / worktree_file).write_text("")

    reply = daemon.deliver_files(
        str(root),
        [{"path": "c.py", "content": "c = 1\n"}, _delete(worktree_file, "")],
        SPEC,
    )

    assert {f["path"]: f["status"] for f in reply["files"]} == {
        "c.py": "written",
        worktree_file: "deleted",
    }
    assert reply["git"]["commit"] and not (worktree / worktree_file).exists()
