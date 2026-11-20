"""Console producers, the host git and the reply's bounds (review of PR #447).

The worktree carries a private repository the sandbox can use; whatever the
CLI configures there never runs on the host, a replaced or redirected ``.git``
refuses the run, every host git run is hardened against the repository's
config, and a deletion, a rename or a reply past its bound refutes the run.

Console producers: a terminal coding agent produces a role's task, and the
delivery is what the daemon observes (``services/producers/console_producer.py``,
``services/producers/console_clis.py``, ``vscode-daemon/console_run.py``).
Real git in ``tmp_path`` and the real daemon, with a fake CLI on ``PATH``; the shared
fixtures are in ``console_producer_helpers``.
"""

import os
import shutil
import subprocess
import sys
from pathlib import Path

from services.producers import console_clis

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "vscode-daemon"))
import console_producer_helpers
import console_run
from console_producer_helpers import (
    EDIT_CALC,
    FSMONITOR_PAYLOAD,
    SPEC,
    _resolve,
    _run,
    daemon,
    fake_cli,
    git,
)

# The shared fixtures.
repo = console_producer_helpers.repo
cli_path = console_producer_helpers.cli_path


def test_the_private_git_is_discarded_and_its_config_never_runs_on_the_host(
    repo, cli_path, tmp_path, monkeypatch
):
    """The CLI may configure its private repository as it likes (here an
    fsmonitor that writes a marker): collect discards it with file
    operations and writes the pointer back before any host git runs."""
    marker = tmp_path / "pwned"
    monkeypatch.setenv("MARKER", str(marker))
    fake_cli(cli_path, "claude", EDIT_CALC + FSMONITOR_PAYLOAD)
    prepared = console_run.prepare(str(repo), {"git": SPEC})
    worktree = Path(prepared["worktree"])
    _run(str(worktree), console_clis.render_command("claude", "go"))
    marker.unlink(missing_ok=True)  # The CLI's own git ran it, in its sandbox.
    collected = console_run.collect(
        str(worktree), {"head": prepared["head"], "guard": prepared["guard"]}, _resolve
    )
    assert collected["ok"], collected
    assert "calc.py" in [f["path"] for f in collected["files"]]
    assert not marker.exists()
    assert (worktree / ".git").is_file()
    assert git(str(worktree), "rev-parse", "HEAD") == prepared["head"]
    assert not marker.exists()


def test_the_worktree_carries_a_private_git_the_sandbox_can_use(repo, tmp_path):
    """A linked worktree's .git names the host's git dir, which no sandbox
    mounts: prepare gives it a repository of its own, at the task's head,
    that names nothing of the host, so git works wherever the folder is
    mounted (copied elsewhere here, as at /workspace)."""
    prepared = console_run.prepare(str(repo), {"git": SPEC})
    assert prepared["ok"] and prepared["private_git"] is True
    worktree = Path(prepared["worktree"])
    assert (worktree / ".git").is_dir()
    common = str((repo / ".git").resolve())
    for path in (worktree / ".git").rglob("*"):
        if path.is_file():
            assert common.encode() not in path.read_bytes(), path
    assert not (worktree / ".git" / "objects" / "info" / "alternates").exists()
    mounted = tmp_path / "workspace"
    shutil.copytree(worktree, mounted, symlinks=True)
    clean = {k: v for k, v in os.environ.items() if not k.startswith("GIT_")}

    def inside(*args):
        return subprocess.run(
            ["git", *args],
            cwd=mounted,
            env=clean,
            capture_output=True,
            text=True,
            check=True,
        ).stdout.strip()

    assert inside("rev-parse", "HEAD") == prepared["head"]
    assert inside("status", "--porcelain") == ""
    assert inside("remote") == ""
    (mounted / "calc.py").write_text("x = 1\n")
    assert "calc.py" in inside("diff", "--name-only", "HEAD")
    # The person's repository gained no object, ref or remote from it.
    assert git(repo, "remote") == ""


def test_a_private_git_left_by_a_run_never_collected_is_removed_before_host_git(
    repo, cli_path, tmp_path, monkeypatch
):
    """A run whose collect never came (the backend stopped) leaves the
    private repository, configured by the CLI; the next delivery in that
    worktree puts git's pointer back before any git runs there."""
    marker = tmp_path / "pwned"
    monkeypatch.setenv("MARKER", str(marker))
    fake_cli(
        cli_path,
        "claude",
        EDIT_CALC
        + "printf '#!/bin/sh\\ntouch %s\\nexit 1\\n' \"$MARKER\" > fsm.sh\n"
        + "chmod +x fsm.sh\n"
        + 'git config core.fsmonitor "$PWD/fsm.sh"\n',
    )
    prepared = console_run.prepare(str(repo), {"git": SPEC})
    worktree = Path(prepared["worktree"])
    _run(str(worktree), console_clis.render_command("claude", "go"))
    marker.unlink(missing_ok=True)
    result = daemon.deliver_files(
        str(repo), [{"path": "other.py", "content": "Y = 2\n"}], SPEC
    )
    assert result["ok"], result
    assert result["git"]["commit"] != prepared["head"]
    assert not marker.exists()
    assert (worktree / ".git").is_file()


def test_a_worktree_with_a_command_still_running_keeps_its_private_git(
    repo, cli_path, tmp_path
):
    """A backend that restarted re-queues the task while the daemon still runs
    its CLI: the next attempt in that worktree is refused and the running
    CLI's private repository is left alone."""
    import sprint_integration

    fake_cli(cli_path, "claude", EDIT_CALC)
    prepared = console_run.prepare(str(repo), {"git": SPEC})
    worktree = Path(prepared["worktree"])
    assert (worktree / ".git").is_dir()

    def again():
        return daemon.deliver_files(
            str(repo), [{"path": "other.py", "content": "Y = 2\n"}], SPEC
        )

    result = sprint_integration.while_active(str(worktree), again)
    assert not result["ok"]
    assert "still running" in str(result)
    assert (worktree / ".git").is_dir()


def test_a_replaced_private_git_is_refused_and_no_git_runs_on_the_host(
    repo, cli_path, tmp_path, monkeypatch
):
    marker = tmp_path / "pwned"
    monkeypatch.setenv("MARKER", str(marker))
    payload = FSMONITOR_PAYLOAD.replace("rm -f .git", "rm -rf .git")
    planted = tmp_path / "planted"
    payload += f"mv .git {planted} && ln -s {planted} .git\n"
    fake_cli(cli_path, "claude", EDIT_CALC + payload)
    prepared = console_run.prepare(str(repo), {"git": SPEC})
    worktree = Path(prepared["worktree"])
    _run(str(worktree), console_clis.render_command("claude", "go"))
    assert (worktree / ".git").is_symlink()  # The CLI did replace it.
    marker.unlink(missing_ok=True)
    collected = console_run.collect(
        str(worktree), {"head": prepared["head"], "guard": prepared["guard"]}, _resolve
    )
    assert collected["ok"] is False and collected["files"] == []
    assert ".git" in collected["refused"][0]
    assert not marker.exists()
    assert (planted / "config").is_file()  # The symlink removed, never its target.
    # The pointer is La Fabrique's again (a file write), and the next prepare
    # puts the worktree back without ever running the payload.
    assert (worktree / ".git").is_file()
    again = console_run.prepare(str(repo), {"git": SPEC})
    assert again["ok"], again
    assert not (worktree / "fsm.sh").exists()
    assert (worktree / "calc.py").read_text().endswith("return 0\n")
    assert not marker.exists()
    # The daemon's alarm wins over every other outcome and is kept on the run.
    failure = console_clis.failure(
        {"timed_out": True, "timeout_seconds": 5}, collected, []
    )
    assert failure == ("failed", collected["refused"][0])


def test_a_dot_git_pointing_at_another_git_dir_is_refused(repo, cli_path, tmp_path):
    evil = tmp_path / "evil.git"
    git(tmp_path, "init", "-q", "--bare", str(evil))
    fake_cli(
        cli_path,
        "claude",
        EDIT_CALC + f"rm -rf .git && printf 'gitdir: {evil}\\n' > .git\n",
    )
    prepared = console_run.prepare(str(repo), {"git": SPEC})
    _run(prepared["worktree"], console_clis.render_command("claude", "go"))
    collected = console_run.collect(
        prepared["worktree"],
        {"head": prepared["head"], "guard": prepared["guard"]},
        _resolve,
    )
    assert collected["ok"] is False
    assert "private .git was replaced" in collected["refused"][0]
    assert (
        (Path(prepared["worktree"]) / ".git")
        .read_text()
        .strip()
        .endswith(Path(console_run.admin_dir(Path(prepared["worktree"]))).name)
    )


def test_every_host_git_run_is_hardened_against_the_repositorys_config(
    repo, cli_path, tmp_path, monkeypatch
):
    """An fsmonitor configured in the repository itself never runs either:
    every command names the git dir and turns fsmonitor and hooks off."""
    marker = tmp_path / "fsmonitor-ran"
    hook = tmp_path / "fsm.sh"
    hook.write_text(f"#!/bin/sh\ntouch {marker}\nexit 1\n")
    hook.chmod(0o755)
    fake_cli(cli_path, "claude", EDIT_CALC)
    prepared = console_run.prepare(str(repo), {"git": SPEC})
    worktree = Path(prepared["worktree"])
    _run(str(worktree), console_clis.render_command("claude", "go"))
    git(repo, "config", "core.fsmonitor", str(hook))
    monkeypatch.setenv("GIT_DIR", str(tmp_path / "nowhere"))  # Never inherited.
    collected = console_run.collect(
        str(worktree), {"head": prepared["head"], "guard": prepared["guard"]}, _resolve
    )
    assert [f["path"] for f in collected["files"]] == ["calc.py"]
    assert not marker.exists()
    assert "core.fsmonitor=false" in console_run.SAFE_CONFIG
    assert "core.hooksPath=/dev/null" in console_run.SAFE_CONFIG


def test_a_deletion_or_a_rename_refutes_the_run(repo, cli_path):
    fake_cli(cli_path, "claude", EDIT_CALC + "mv README.md README.txt\n")
    prepared = console_run.prepare(str(repo), {"git": SPEC})
    _run(prepared["worktree"], console_clis.render_command("claude", "go"))
    collected = console_run.collect(
        prepared["worktree"],
        {"head": prepared["head"], "guard": prepared["guard"]},
        _resolve,
    )
    by_path = {f["path"]: f for f in collected["files"]}
    assert by_path["README.md"]["change"] == "deleted"
    _, deliver = console_clis.observed_files(collected)
    kind, reason = console_clis.failure(
        {"success": True, "exit_code": 0}, collected, deliver
    )
    assert kind == "failed" and "README.md" in reason and "cannot apply" in reason
    # A listing cut at its bound is never delivered in part.
    cut = dict(collected, files=[by_path["calc.py"]], truncated=True)
    assert (
        "partial listing"
        in console_clis.failure({"success": True, "exit_code": 0}, cut, [1])[1]
    )


def test_the_reply_bound_counts_encoded_bytes_and_the_worktree_waits_for_it(
    repo, cli_path, monkeypatch
):
    monkeypatch.setattr(console_run, "MAX_REPLY_BYTES", 8 * 1024)
    # 2000 characters, 4000 UTF-8 bytes, 12000 bytes once json escapes them.
    fake_cli(
        cli_path,
        "claude",
        EDIT_CALC + "python3 -c \"open('wide.py','w').write('# ' + chr(233)*2000)\"\n",
    )
    prepared = console_run.prepare(str(repo), {"git": SPEC})
    _run(prepared["worktree"], console_clis.render_command("claude", "go"))
    collected = console_run.collect(
        prepared["worktree"],
        {"head": prepared["head"], "guard": prepared["guard"]},
        _resolve,
    )
    import json

    assert len(json.dumps(collected)) <= 8 * 1024
    by_path = {f["path"]: f for f in collected["files"]}
    assert by_path["wide.py"]["status"] == "past_bound"
    assert "content" not in by_path["wide.py"]
    assert git(prepared["worktree"], "status", "--porcelain") == ""
    _, deliver = console_clis.observed_files(collected)
    reason = console_clis.failure({"success": True, "exit_code": 0}, collected, deliver)
    assert reason and "reply's bound" in reason[1]
