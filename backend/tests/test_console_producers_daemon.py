"""Console producers, the daemon: prepare, run, collect.

The daemon reads back what the CLI changed and puts the worktree back; a CLI
that commits itself is collected and undone; hooks, secrets, symlinks out and
binaries are never delivered; only a task worktree is collected.

Console producers: a terminal coding agent produces a role's task, and the
delivery is what the daemon observes (``services/producers/console_producer.py``,
``services/producers/console_clis.py``, ``vscode-daemon/console_run.py``).
Real git in ``tmp_path`` and the real daemon, with a fake CLI on ``PATH``; the shared
fixtures are in ``console_producer_helpers``.
"""

import hashlib
import os
import sys
from pathlib import Path

from services.producers import console_clis

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "vscode-daemon"))
import console_producer_helpers
import console_run
from console_producer_helpers import (
    _DAEMON_DIR,
    EDIT_CALC,
    SPEC,
    _resolve,
    _run,
    fake_cli,
    git,
)

# The shared fixtures.
repo = console_producer_helpers.repo
cli_path = console_producer_helpers.cli_path


def test_the_daemon_reads_back_what_the_cli_changed_and_puts_the_worktree_back(
    repo, cli_path
):
    fake_cli(
        cli_path, "claude", EDIT_CALC + "mkdir -p pkg && echo 'X = 1' > pkg/new.py\n"
    )
    prepared = console_run.prepare(str(repo), {"git": SPEC})
    assert prepared["ok"], prepared
    worktree = prepared["worktree"]
    assert worktree.endswith("fabrique__task-1")
    command = console_clis.render_command(
        "claude", "Fix add. It's 'quoted' $(rm -rf /)\nok"
    )
    result = _run(worktree, command, console_clis.command_policy("claude"))
    assert result["success"], result
    assert result["sandbox"]["mode"] == "host"

    collected = console_run.collect(
        worktree, {"head": prepared["head"], "guard": prepared["guard"]}, _resolve
    )
    assert collected["ok"] and not collected["refused"]
    by_path = {f["path"]: f for f in collected["files"]}
    assert by_path["calc.py"]["change"] == "modified"
    assert by_path["calc.py"]["content"] == "def add(a, b):\n    return a + b\n"
    assert by_path["pkg/new.py"]["change"] == "added"
    assert (
        by_path["calc.py"]["sha256"]
        == hashlib.sha256(b"def add(a, b):\n    return a + b\n").hexdigest()
    )
    # The worktree is back at its HEAD, the person's checkout never touched.
    assert git(worktree, "status", "--porcelain") == ""
    assert (Path(worktree) / "calc.py").read_text().endswith("return 0\n")
    assert (repo / "calc.py").read_text().endswith("return 0\n")
    assert git(repo, "status", "--porcelain") == ""


def test_the_cli_committing_itself_is_collected_and_undone(repo, cli_path):
    fake_cli(
        cli_path,
        "claude",
        EDIT_CALC + "git -c user.name=x -c user.email=x@x commit -qam 'mine'\n",
    )
    prepared = console_run.prepare(str(repo), {"git": SPEC})
    _run(prepared["worktree"], console_clis.render_command("claude", "go"))
    collected = console_run.collect(
        prepared["worktree"],
        {"head": prepared["head"], "guard": prepared["guard"]},
        _resolve,
    )
    assert collected["committed_by_cli"] is True
    assert [f["path"] for f in collected["files"]] == ["calc.py"]
    assert git(prepared["worktree"], "rev-parse", "HEAD") == prepared["head"]


def test_hooks_secrets_symlinks_and_binaries_are_never_delivered(
    repo, cli_path, tmp_path
):
    outside = tmp_path / "outside.txt"
    outside.write_text("secret\n")
    fake_cli(
        cli_path,
        "claude",
        "echo 'TOKEN=x' > .env\n"
        f"ln -s {outside} link.txt\n"
        "printf '\\377\\376' > blob.py\n" + EDIT_CALC,
    )
    prepared = console_run.prepare(str(repo), {"git": SPEC})
    _run(prepared["worktree"], console_clis.render_command("claude", "go"))
    collected = console_run.collect(
        prepared["worktree"],
        {"head": prepared["head"], "guard": prepared["guard"]},
        _resolve,
    )
    by_path = {f["path"]: f for f in collected["files"]}
    assert by_path[".env"]["status"] == "refused" and "content" not in by_path[".env"]
    assert by_path["link.txt"]["status"] == "refused"
    assert by_path["blob.py"]["status"] == "binary"
    assert by_path["calc.py"]["status"] == "changed"
    _, deliver = console_clis.observed_files(collected)
    assert [f["path"] for f in deliver] == ["calc.py"]

    # A hook planted through the git the CLI sees lands in its private
    # repository, which is discarded: the person's repository never gets it.
    fake_cli(
        cli_path,
        "claude",
        EDIT_CALC
        + "echo 'echo pwned' > \"$(git rev-parse --git-common-dir)/hooks/pre-commit\"\n",
    )
    prepared = console_run.prepare(str(repo), {"git": SPEC})
    _run(prepared["worktree"], console_clis.render_command("claude", "go"))
    collected = console_run.collect(
        prepared["worktree"],
        {"head": prepared["head"], "guard": prepared["guard"]},
        _resolve,
    )
    assert collected["ok"] and not (repo / ".git" / "hooks" / "pre-commit").exists()
    assert (Path(prepared["worktree"]) / ".git").is_file()
    # A hook written into the repository's own metadata (reachable only on
    # the host sandbox): nothing is delivered, and it is said.
    fake_cli(
        cli_path,
        "claude",
        EDIT_CALC + f"echo 'echo pwned' > {repo}/.git/hooks/pre-commit\n",
    )
    prepared = console_run.prepare(str(repo), {"git": SPEC})
    _run(prepared["worktree"], console_clis.render_command("claude", "go"))
    collected = console_run.collect(
        prepared["worktree"],
        {"head": prepared["head"], "guard": prepared["guard"]},
        _resolve,
    )
    assert collected["ok"] is False
    assert "metadata" in collected["refused"][0]
    assert all("content" not in f for f in collected["files"])
    # The person keeps working in their checkout while it runs: no sandbox
    # mounts it, so their edits never refuse the run.
    fake_cli(cli_path, "claude", EDIT_CALC + f"echo x > {repo}/stray.py\n")
    prepared = console_run.prepare(str(repo), {"git": SPEC})
    _run(prepared["worktree"], console_clis.render_command("claude", "go"))
    collected = console_run.collect(
        prepared["worktree"],
        {"head": prepared["head"], "guard": prepared["guard"]},
        _resolve,
    )
    assert collected["ok"] and collected["refused"] == []
    assert "checkout" not in prepared["guard"]


def test_collect_reads_only_a_task_worktree(repo):
    head = git(repo, "rev-parse", "HEAD")
    assert console_run.collect(str(repo), {"head": head}, _resolve)["ok"] is False
    assert console_run.collect(str(repo), {"head": "main"}, _resolve)["ok"] is False
    assert console_run.prepare(str(repo), {"git": {"branch": "main"}})["ok"] is False


def test_the_daemon_image_copies_the_module():
    dockerfile = (_DAEMON_DIR / "Dockerfile").read_text()
    assert "console_run.py" in dockerfile
    assert os.path.exists(_DAEMON_DIR / "console_run.py")
