"""Integrating a sprint (issue #314), the daemon's side.

``vscode-daemon/sprint_integration.py`` merges the done tasks' branches into
``fabrique/sprint-<id>`` in dependency order with real git, records a conflict
with its files, reads each task branch once, and gives every attempt a test
worktree of its own that a later integration cannot move or remove while a
command runs in it.

These tests run real git in ``tmp_path``, with a bare repository standing in
for the remote; the shared fakes and rows are in ``sprint_integration_helpers``.
"""

import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "vscode-daemon"))
import git_workspace
import sprint_integration
import sprint_integration_helpers
from sprint_integration_helpers import _deliver
from test_git_workflow import git

# The shared fixtures.
repo = sprint_integration_helpers.repo


def test_two_task_branches_are_merged_in_order_and_the_checkout_is_untouched(repo):
    main_head = git(repo, "rev-parse", "HEAD")
    _deliver(repo, 1, {"a.py": "A = 1\n"})
    _deliver(repo, 2, {"b.py": "from a import A\nB = A + 1\n"}, ["fabrique/task-1"])

    reply = sprint_integration.integrate(
        str(repo),
        {
            "branch": "fabrique/sprint-5",
            "base": "",
            "merge": [
                "fabrique/task-1",
                "fabrique/task-2",
                "fabrique/task-99",
                "main",
            ],
        },
    )

    assert reply["ok"] is True and reply["conflict"] is None
    assert [m["branch"] for m in reply["merged"]] == [
        "fabrique/task-1",
        "fabrique/task-2",
    ]
    assert reply["start"] == main_head
    assert git(repo, "rev-parse", "fabrique/sprint-5") == reply["commit"]
    assert (reply["missing"], reply["refused"]) == (["fabrique/task-99"], ["main"])
    worktree = Path(reply["worktree"])
    assert (worktree / "a.py").exists() and (worktree / "b.py").exists()
    assert git(repo, "status", "--porcelain") == ""
    assert git(repo, "rev-parse", "--abbrev-ref", "HEAD") == "main"
    assert not (repo / "a.py").exists()

    again = sprint_integration.integrate(
        str(repo),
        {
            "branch": "fabrique/sprint-5",
            "merge": ["fabrique/task-1", "fabrique/task-2"],
        },
    )
    assert again["merged"] == []
    assert again["already_merged"] == ["fabrique/task-1", "fabrique/task-2"]
    assert again["commit"] == reply["commit"]


def test_a_conflict_is_observed_with_its_files_and_the_merge_aborted(repo):
    _deliver(repo, 1, {"app.py": "x = 1\n"})
    _deliver(repo, 2, {"app.py": "x = 2\n"})
    _deliver(repo, 3, {"c.py": "c = 3\n"})

    reply = sprint_integration.integrate(
        str(repo),
        {
            "branch": "fabrique/sprint-6",
            "merge": ["fabrique/task-1", "fabrique/task-2", "fabrique/task-3"],
        },
    )

    assert reply["ok"] is False
    [first] = reply["merged"]
    assert first["branch"] == "fabrique/task-1"
    conflict = reply["conflict"]
    assert (conflict["branch"], conflict["files"]) == ("fabrique/task-2", ["app.py"])
    assert "<<<<<<<" in conflict["hunks"] and "x = 2" in conflict["hunks"]
    assert reply["not_attempted"] == ["fabrique/task-3"]
    # The branch stays at the last clean merge, the worktree clean.
    assert reply["commit"] == first["commit"]
    assert git(Path(reply["worktree"]), "status", "--porcelain") == ""


def test_only_a_sprint_branch_is_integrated(repo):
    with pytest.raises(git_workspace.GitError):
        sprint_integration.integrate(str(repo), {"branch": "main", "merge": []})
    with pytest.raises(git_workspace.GitError):
        sprint_integration.integrate(
            str(repo / "missing"), {"branch": "fabrique/sprint-1", "merge": []}
        )


def test_the_daemon_dockerfile_ships_the_module():
    dockerfile = Path(__file__).resolve().parents[2] / "vscode-daemon" / "Dockerfile"
    assert "sprint_integration.py" in dockerfile.read_text(encoding="utf-8")


def test_the_daemon_tests_a_worktree_a_later_integration_cannot_move(repo):
    _deliver(repo, 1, {"a.py": "A = 1\n"})
    first = sprint_integration.integrate(
        str(repo), {"branch": "fabrique/sprint-8", "merge": ["fabrique/task-1"]}
    )
    _deliver(repo, 2, {"b.py": "B = 2\n"})
    second = sprint_integration.integrate(
        str(repo),
        {
            "branch": "fabrique/sprint-8",
            "merge": ["fabrique/task-1", "fabrique/task-2"],
        },
    )

    assert second["commit"] != first["commit"]
    assert first["test_worktree"] != second["test_worktree"]
    assert git(Path(second["test_worktree"]), "rev-parse", "HEAD") == second["commit"]
    # The sprint branch's own worktree moved on; the snapshot is detached at its commit.
    assert git(Path(second["worktree"]), "rev-parse", "HEAD") == second["commit"]
    assert (
        git(Path(second["test_worktree"]), "rev-parse", "--abbrev-ref", "HEAD")
        == "HEAD"
    )
    assert second["sources"]["fabrique/task-2"] == git(
        repo, "rev-parse", "fabrique/task-2"
    )


def test_the_suite_never_runs_on_files_a_previous_suite_left(repo):
    _deliver(repo, 1, {"a.py": "A = 1\n"})
    spec = {"branch": "fabrique/sprint-9", "merge": ["fabrique/task-1"]}
    first = sprint_integration.integrate(str(repo), {**spec, "attempt": "one"})
    snapshot = Path(first["test_worktree"])
    (snapshot / "a.py").write_text("A = 'changed by a test'\n")
    (snapshot / "generated_test.py").write_text("assert True\n")

    again = sprint_integration.integrate(str(repo), {**spec, "attempt": "two"})

    assert again["commit"] == first["commit"]
    fresh = Path(again["test_worktree"])
    assert fresh != snapshot and fresh.name.endswith("-two")
    assert (fresh / "a.py").read_text() == "A = 1\n"
    assert not (fresh / "generated_test.py").exists()
    # The first attempt's files are its own: a retry never resets them under it.
    assert (snapshot / "a.py").read_text() == "A = 'changed by a test'\n"


def test_a_task_branch_is_read_once_checked_merged_and_recorded_at_that_commit(
    repo, monkeypatch
):
    _deliver(repo, 1, {"a.py": "A = 1\n"})
    read = git(repo, "rev-parse", "fabrique/task-1")
    real_merge = sprint_integration.merge_one

    def merge_after_a_delivery(worktree, ref, message):
        # A delivery moves the task branch between the read and the merge.
        _deliver(repo, 1, {"late.py": "LATE = 1\n"})
        return real_merge(worktree, ref, message)

    monkeypatch.setattr(sprint_integration, "merge_one", merge_after_a_delivery)
    reply = sprint_integration.integrate(
        str(repo), {"branch": "fabrique/sprint-10", "merge": ["fabrique/task-1"]}
    )

    [merged] = reply["merged"]
    assert reply["sources"]["fabrique/task-1"] == merged["source"] == read
    assert git(repo, "rev-parse", f"{merged['commit']}^2") == read
    assert git(repo, "rev-parse", "fabrique/task-1") != read
    assert not (Path(reply["worktree"]) / "late.py").exists()


def test_a_snapshot_a_command_runs_in_is_never_removed(repo, monkeypatch):
    monkeypatch.setattr(sprint_integration, "SNAPSHOT_GRACE_SECONDS", 0)
    monkeypatch.setattr(sprint_integration, "KEEP_SNAPSHOTS", 1)
    _deliver(repo, 1, {"a.py": "A = 1\n"})
    spec = {"branch": "fabrique/sprint-11", "merge": ["fabrique/task-1"]}
    busy = Path(
        sprint_integration.integrate(str(repo), {**spec, "attempt": "busy"})[
            "test_worktree"
        ]
    )

    def retried_meanwhile():
        # The backend restarted; another replica retries while this suite still runs.
        return Path(
            sprint_integration.integrate(str(repo), {**spec, "attempt": "retry"})[
                "test_worktree"
            ]
        )

    retry = sprint_integration.while_active(busy, retried_meanwhile)

    assert busy.is_dir() and (busy / "a.py").read_text() == "A = 1\n"
    assert retry.is_dir()
    # Once its command ended, the busy snapshot is removed like any other.
    sprint_integration.integrate(str(repo), {**spec, "attempt": "later"})
    assert not busy.exists() and not retry.exists()


def test_a_snapshot_younger_than_the_grace_is_kept(repo, monkeypatch):
    monkeypatch.setattr(sprint_integration, "KEEP_SNAPSHOTS", 1)
    _deliver(repo, 1, {"a.py": "A = 1\n"})
    spec = {"branch": "fabrique/sprint-12", "merge": ["fabrique/task-1"]}
    first = Path(
        sprint_integration.integrate(str(repo), {**spec, "attempt": "a"})[
            "test_worktree"
        ]
    )
    sprint_integration.integrate(str(repo), {**spec, "attempt": "b"})
    # Its suite may not have started yet.
    assert first.is_dir()


def test_the_daemon_marks_each_command_s_folder_in_use():
    seen = []

    def run(folder):
        seen.append(dict(sprint_integration._ACTIVE))
        return "done"

    folder = Path(__file__).parent
    assert sprint_integration.while_active(folder, run, folder) == "done"
    assert seen == [{str(folder.resolve()): 1}]
    assert sprint_integration._ACTIVE == {}
    daemon_source = (
        Path(__file__).resolve().parents[2] / "vscode-daemon" / "daemon.py"
    ).read_text()
    assert (
        "sprint_integration.while_active, workspace_path, execute_shell_command"
        in daemon_source
    )
