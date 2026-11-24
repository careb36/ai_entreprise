"""The agents' read-only git views (``git_view.py``, PROTOCOL.md §5.6), on a real repository."""

from __future__ import annotations

import subprocess

import pytest

import abilities
import git_view


def _git(root, *args):
    subprocess.run(
        ["git", "-c", "user.name=t", "-c", "user.email=t@example.invalid", *args],
        cwd=root, check=True, capture_output=True,
    )


@pytest.fixture()
def repo(tmp_path):
    root = tmp_path / "proj"
    (root / "pkg").mkdir(parents=True)
    (root / "pkg" / "a.py").write_text("A = 1\n")
    (root / "pkg" / "b.py").write_text("B = 1\n")
    _git(root, "init", "-q", "-b", "main")
    _git(root, "add", ".")
    _git(root, "commit", "-q", "-m", "init")
    _git(root, "checkout", "-q", "-b", "fabrique/task-2")
    _git(root, "mv", "pkg/b.py", "pkg/c.py")
    (root / "pkg" / "a.py").write_text("A = 2\n")
    (root / "notes.txt").write_text("new\n")
    return root


def test_status_lists_renames_changes_and_untracked_files(repo):
    reply = git_view.view(str(repo), {"view": "status"})
    entries = {e["path"]: e for e in reply["entries"]}
    assert reply["ok"] and reply["branch"] == "fabrique/task-2"
    assert entries["pkg/c.py"]["code"][0] == "R" and entries["pkg/c.py"]["from"] == "pkg/b.py"
    assert entries["pkg/a.py"]["code"] == " M" and entries["notes.txt"]["code"] == "??"


def test_changed_is_what_the_task_changed_against_its_base(repo):
    reply = git_view.view(str(repo), {"view": "changed", "base": "main"})
    files = {f["path"]: f["change"] for f in reply["files"]}
    assert files == {"notes.txt": "untracked", "pkg/a.py": "modified",
                     "pkg/b.py": "deleted", "pkg/c.py": "added"}
    assert reply["base"] == "main" and reply["count"] == 4


def test_a_subfolder_sees_its_own_paths(repo):
    reply = git_view.view(str(repo / "pkg"), {"view": "changed"})
    assert {f["path"] for f in reply["files"]} == {"a.py", "b.py", "c.py"}
    assert "+A = 2" in git_view.view(str(repo / "pkg"), {"view": "diff", "path": "a.py"})["patch"]


@pytest.mark.parametrize("path", ["../x", "/etc/hosts", ".git/HEAD", "id_rsa", "", None])
def test_a_path_outside_or_secret_is_refused(repo, path):
    with pytest.raises(ValueError):
        git_view.view(str(repo), {"view": "diff", "path": path})


def test_bad_requests_are_refused(repo, tmp_path):
    with pytest.raises(ValueError):
        git_view.view(str(repo), {"view": "push"})
    with pytest.raises(ValueError):
        git_view.view(str(repo), {"view": "changed", "base": "--all"})
    with pytest.raises(git_view.GitError):
        git_view.view(str(repo), {"view": "changed", "base": "nope"})
    (tmp_path / "plain").mkdir()
    with pytest.raises(git_view.GitError):
        git_view.view(str(tmp_path / "plain"), {"view": "status"})
    with pytest.raises(FileNotFoundError):
        git_view.view(str(tmp_path / "missing"), {"view": "status"})


def test_log_is_bounded_and_the_view_is_announced(repo):
    assert len(git_view.view(str(repo), {"view": "log", "limit": 99})["commits"]) == 1
    assert git_view.view(str(repo), {"view": "log", "limit": 99})["limit"] == 20
    assert "view" in abilities.announcement()["features"]["workspace.git.diff"]
