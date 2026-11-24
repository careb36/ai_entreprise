"""The blame view (``git_view.blame``, PROTOCOL.md §5.6), on a real repository.

Blame says when and why lines changed: the commit, its date and its subject.
The porcelain also carries the authors' and committers' names and e-mail
addresses; none of them may leave the daemon (the repositories may be public).
"""

from __future__ import annotations

import json
import subprocess

import pytest

import abilities
import git_view

NAME, MAIL = "Ada Example", "ada.example@example.invalid"


def _git(root, *args, env=None):
    subprocess.run(
        ["git", "-c", f"user.name={NAME}", "-c", f"user.email={MAIL}", *args],
        cwd=root, check=True, capture_output=True, env=env,
    )


@pytest.fixture()
def repo(tmp_path):
    root = tmp_path / "proj"
    (root / "docs").mkdir(parents=True)
    (root / "docs" / "guide.md").write_text("one\ntwo\nthree\n")
    _git(root, "init", "-q", "-b", "main")
    _git(root, "add", ".")
    _git(root, "commit", "-q", "-m", "Write the guide")
    (root / "docs" / "guide.md").write_text("one\nTWO\nthree\nfour\n")
    _git(root, "commit", "-q", "-am", "Fix the second step")
    return root


def test_blame_groups_lines_by_commit_with_date_and_subject(repo):
    reply = git_view.view(str(repo), {"view": "blame", "path": "docs/guide.md"})
    assert reply["ok"] and reply["view"] == "blame" and reply["file_lines"] == 4
    groups = [(g["start_line"], g["end_line"], g["subject"]) for g in reply["groups"]]
    assert groups == [(1, 1, "Write the guide"), (2, 2, "Fix the second step"),
                      (3, 3, "Write the guide"), (4, 4, "Fix the second step")]
    assert all(len(g["commit"]) == 40 and g["date"].endswith("Z") for g in reply["groups"])


def test_blame_never_returns_an_authors_name_or_email(repo):
    reply = git_view.view(str(repo), {"view": "blame", "path": "docs/guide.md"})
    text = json.dumps(reply)
    assert NAME not in text and MAIL not in text and "Ada" not in text
    assert all(set(g) == {"commit", "start_line", "end_line", "date", "subject", "uncommitted"}
               for g in reply["groups"])
    # Nor the line contents: the agent reads the file for those.
    assert "TWO" not in text


def test_blame_is_bounded_and_clamped(repo):
    reply = git_view.view(str(repo), {"view": "blame", "path": "docs/guide.md",
                                      "start_line": 2, "end_line": 999})
    assert (reply["start_line"], reply["end_line"]) == (2, 4) and not reply["truncated"]
    (repo / "long.md").write_text("".join(f"line {i}\n" for i in range(500)))
    _git(repo, "add", "long.md")
    _git(repo, "commit", "-q", "-m", "Long file")
    reply = git_view.view(str(repo), {"view": "blame", "path": "long.md", "end_line": 500})
    assert reply["end_line"] - reply["start_line"] + 1 == git_view.MAX_BLAME_LINES
    assert reply["truncated"] and len(reply["groups"]) == 1
    with pytest.raises(ValueError):
        git_view.view(str(repo), {"view": "blame", "path": "docs/guide.md", "start_line": 50})


def test_uncommitted_lines_are_marked_without_a_subject(repo):
    (repo / "docs" / "guide.md").write_text("one\nTWO\nthree\nfour\nfive\n")
    reply = git_view.view(str(repo), {"view": "blame", "path": "docs/guide.md", "start_line": 5})
    [group] = reply["groups"]
    assert group["uncommitted"] and group["subject"] == ""


@pytest.mark.parametrize("path", ["../x", "/etc/hosts", ".git/HEAD", ".env", "", None])
def test_blame_refuses_a_path_outside_or_secret(repo, path):
    with pytest.raises(ValueError):
        git_view.view(str(repo), {"view": "blame", "path": path})


def test_blame_is_announced_as_its_own_feature():
    assert "blame" in abilities.announcement()["features"]["workspace.git.diff"]
