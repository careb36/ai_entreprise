"""
Publishing a task's branch: the push, and its pull request or merge request.

The daemon pushes ``fabrique/task-<id>`` to ``origin`` with the person's own
Git credentials and opens the request with ``gh`` or ``glab`` when they are
installed, else answers with the page where a person opens it
(``vscode-daemon/git_workspace.py``). These tests push to a bare repository
in ``tmp_path`` standing in for GitHub, and put a fake ``gh`` on the PATH.
"""

import os
import stat
import subprocess
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "vscode-daemon"))
import git_workspace  # noqa: E402
from test_git_workflow import SPEC, daemon, git  # noqa: E402

PUBLISH = {"branch": "fabrique/task-7", "base": "", "title": "Add login", "body": "Evidence"}


@pytest.fixture
def published_repo(tmp_path, monkeypatch):
    """A repository whose origin reads as GitHub and pushes to a bare one here."""
    remote = tmp_path / "remote.git"
    subprocess.run(["git", "init", "-q", "--bare", "-b", "main", str(remote)], check=True)
    root = tmp_path / "app"
    root.mkdir()
    git(root, "init", "-q", "-b", "main")
    (root / "README.md").write_text("# App\n", encoding="utf-8")
    git(root, "add", "README.md")
    git(root, "commit", "-q", "-m", "first")
    git(root, "remote", "add", "origin", "https://token@github.com/ana/app.git")
    git(root, "config", "remote.origin.pushurl", str(remote))
    git(root, "push", "-q", "origin", "main")
    # No gh or glab unless a test puts one there.
    bin_dir = tmp_path / "bin"
    bin_dir.mkdir()
    monkeypatch.setenv("PATH", f"{bin_dir}{os.pathsep}{Path(sys.executable).parent}{os.pathsep}/usr/bin:/bin")
    return root, remote, bin_dir


def _fake_cli(bin_dir, name, script):
    path = bin_dir / name
    path.write_text(f"#!/bin/sh\n{script}\n", encoding="utf-8")
    path.chmod(path.stat().st_mode | stat.S_IEXEC)
    return path


def test_without_gh_the_branch_is_pushed_and_the_answer_is_the_compare_page(published_repo):
    root, remote, _ = published_repo
    commit = daemon.deliver_files(str(root), [{"path": "a.py", "content": "a = 1\n"}], SPEC)["git"]["commit"]

    reply = git_workspace.publish(str(root), PUBLISH)

    assert reply == {
        "ok": True,
        "provider": "github",
        "branch": "fabrique/task-7",
        "base": "main",
        "commit": commit,
        "url": "https://github.com/ana/app/compare/main...fabrique%2Ftask-7?expand=1",
        "how": "compare",
    }
    assert git(remote, "rev-parse", "refs/heads/fabrique/task-7") == commit
    assert "token" not in reply["url"]


def test_with_gh_a_draft_pull_request_is_opened_with_the_evidence(published_repo, tmp_path):
    root, _, bin_dir = published_repo
    daemon.deliver_files(str(root), [{"path": "a.py", "content": "a = 1\n"}], SPEC)
    seen = tmp_path / "gh-args"
    _fake_cli(bin_dir, "gh", f'printf "%s\\n" "$@" > {seen}; cat > {seen}.body; echo https://github.com/ana/app/pull/12')

    reply = git_workspace.publish(str(root), {**PUBLISH, "base": "main"})

    assert (reply["how"], reply["url"]) == ("gh", "https://github.com/ana/app/pull/12")
    args = seen.read_text().splitlines()
    assert args[:3] == ["pr", "create", "--draft"]
    assert args[args.index("--head") + 1] == "fabrique/task-7"
    assert args[args.index("--title") + 1] == "Add login"
    assert Path(f"{seen}.body").read_text() == "Evidence"


def test_a_gh_that_fails_still_leaves_the_compare_page(published_repo):
    root, _, bin_dir = published_repo
    daemon.deliver_files(str(root), [{"path": "a.py", "content": "a = 1\n"}], SPEC)
    _fake_cli(bin_dir, "gh", "echo 'not logged in' >&2; exit 1")

    reply = git_workspace.publish(str(root), PUBLISH)

    assert reply["how"] == "compare"
    assert reply["url"].startswith("https://github.com/ana/app/compare/")


def test_what_cannot_be_published_is_refused(published_repo, tmp_path):
    root, _, _ = published_repo
    with pytest.raises(git_workspace.GitError, match="branch name"):
        git_workspace.publish(str(root), {**PUBLISH, "branch": "main"})
    with pytest.raises(git_workspace.GitError):  # never delivered: nothing to push
        git_workspace.publish(str(root), PUBLISH)
    lone = tmp_path / "lone"
    lone.mkdir()
    git(lone, "init", "-q", "-b", "main")
    with pytest.raises(git_workspace.GitError, match="no origin"):
        git_workspace.publish(str(lone), PUBLISH)


@pytest.mark.parametrize(
    ("provider", "web", "expected"),
    [
        ("github", "https://github.com/a/b", "https://github.com/a/b/compare/main...fabrique%2Ftask-1?expand=1"),
        (
            "gitlab",
            "https://gitlab.com/g/p",
            "https://gitlab.com/g/p/-/merge_requests/new?merge_request%5Bsource_branch%5D=fabrique%2Ftask-1"
            "&merge_request%5Btarget_branch%5D=main",
        ),
        ("bitbucket", "https://bitbucket.org/a/b", "https://bitbucket.org/a/b/pull-requests/new?source=fabrique%2Ftask-1&dest=main"),
        ("other", None, None),
    ],
)
def test_the_compare_page_per_platform(provider, web, expected):
    assert git_workspace.compare_url(provider, web, "main", "fabrique/task-1") == expected
