"""
No git command of the workspace daemon ever prompts (issue #745, ``vscode-daemon/git_env.py``).

A remote reached over SSH runs the person's SSH command with
``-o BatchMode=yes`` right after its program (``GIT_SSH_COMMAND``, else
``core.sshCommand``, else ``GIT_SSH``, else ``ssh``), so a ``BatchMode=no``
of theirs comes second and loses, and every git command runs with
``GIT_TERMINAL_PROMPT=0`` and no askpass. A fake ``ssh`` stands for a host
whose key is unknown, or a key with a passphrase: with ``BatchMode=yes`` it
fails at once, as OpenSSH does; without it, it would wait for an answer
(here: until the test's limit). The sync, the base's fetch before a branch
and the check before publishing all fail at once and name
``remote_needs_interaction``. Temporary repositories only.
"""

import os
import stat
import subprocess
import sys
import time
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "vscode-daemon"))
import git_base
import git_env
import git_sync
from git_workspace import GitError
from test_git_sync import behind  # noqa: F401 - the fixture
from test_git_workflow import git

# Well under the fake ssh's wait: a prompt would have waited it out.
AT_ONCE_SECONDS = 8


@pytest.fixture
def fake_ssh(tmp_path):
    """``(program, log)``: an ssh that refuses at once under BatchMode and otherwise waits for an answer.

    Like OpenSSH, it keeps the first ``BatchMode`` it is given."""
    log = tmp_path / "ssh.log"
    program = tmp_path / "fake-ssh"
    program.write_text(
        "#!/bin/sh\n"
        f'echo "$*" >> "{log}"\n'
        "mode=\n"
        'for a in "$@"; do\n'
        '  case "$a" in BatchMode=*) [ -z "$mode" ] && mode="$a";; esac\n'
        "done\n"
        'if [ "$mode" = "BatchMode=yes" ]; then\n'
        '  echo "Host key verification failed." >&2; exit 255\n'
        "fi\n"
        "sleep 30\n"
        "exit 255\n"
    )
    program.chmod(program.stat().st_mode | stat.S_IEXEC)
    return program, log


@pytest.fixture
def over_ssh(behind):  # noqa: F811
    """The clone 5 commits behind, its origin now an SSH URL no real host answers."""
    clone, _, _ = behind
    git(
        clone,
        "remote",
        "set-url",
        "origin",
        "ssh://git@fabrique-test.invalid/srv/app.git",
    )
    return clone


def _quick(call):
    started = time.monotonic()
    result = call()
    assert time.monotonic() - started < AT_ONCE_SECONDS
    return result


def test_the_persons_ssh_command_is_kept_with_batch_mode_first(
    over_ssh, fake_ssh, monkeypatch
):
    program, log = fake_ssh
    monkeypatch.setenv("GIT_SSH_COMMAND", f"{program} -o ConnectTimeout=7")
    before = git(over_ssh, "rev-parse", "main")

    answer = _quick(lambda: git_sync.sync(str(over_ssh), "main", "fetch"))

    assert (answer["ok"], answer["outcome"]) == (False, "remote_needs_interaction")
    assert "Host key verification failed" in answer["reason"]
    assert git(over_ssh, "rev-parse", "main") == before
    called = log.read_text()
    assert "-o BatchMode=yes -o ConnectTimeout=7" in called


def test_a_persons_batch_mode_no_cannot_bring_a_prompt_back(
    over_ssh, fake_ssh, monkeypatch
):
    """ssh keeps the first value: ours comes before theirs, so it still fails at once."""
    program, log = fake_ssh
    monkeypatch.setenv("GIT_SSH_COMMAND", f"{program} -o BatchMode=no -i key")

    answer = _quick(lambda: git_sync.sync(str(over_ssh), "main", "fetch"))

    assert answer["outcome"] == "remote_needs_interaction"
    assert "-o BatchMode=yes -o BatchMode=no -i key" in log.read_text()


def test_core_ssh_command_is_kept_when_the_environment_names_none(
    over_ssh, fake_ssh, monkeypatch
):
    program, log = fake_ssh
    monkeypatch.delenv("GIT_SSH_COMMAND", raising=False)
    monkeypatch.delenv("GIT_SSH", raising=False)
    git(over_ssh, "config", "core.sshCommand", f"{program} -o Compression=no")

    observed = _quick(lambda: git_base.observe(over_ssh, "main", policy="fetch"))

    assert observed["remote_needs_interaction"] is True
    assert observed["remote_unreachable"].startswith("remote_needs_interaction:")
    assert "base_behind_remote" not in observed
    assert "-o BatchMode=yes -o Compression=no" in log.read_text()


def test_the_check_before_publishing_names_the_reason(over_ssh, fake_ssh, monkeypatch):
    program, _ = fake_ssh
    monkeypatch.setenv("GIT_SSH_COMMAND", str(program))
    with pytest.raises(GitError, match="remote_needs_interaction"):
        _quick(
            lambda: git_base.behind_remote_at_publish(
                over_ssh, "main", git(over_ssh, "rev-parse", "main"), "publish"
            )
        )


def test_without_batch_mode_the_fake_host_would_have_waited(over_ssh, fake_ssh):
    """The control: the same fetch, the option left out, is still waiting at the limit."""
    program, _ = fake_ssh
    env = {**os.environ, "GIT_SSH_COMMAND": str(program), "GIT_TERMINAL_PROMPT": "0"}
    with pytest.raises(subprocess.TimeoutExpired):
        subprocess.run(
            ["git", "fetch", "origin", "main"],
            cwd=over_ssh,
            env=env,
            capture_output=True,
            timeout=2,
            check=False,
        )


def test_the_environment_of_every_command(tmp_path):
    local = git_env.env_for(["status"], tmp_path, {"GIT_SSH_COMMAND": "mine"})
    assert local["GIT_TERMINAL_PROMPT"] == "0" and local["GIT_ASKPASS"] == ""
    assert local["GCM_INTERACTIVE"] == "never"
    assert local["GIT_SSH_COMMAND"] == "mine"  # a local command leaves it as it was
    for verb in ("fetch", "push", "pull", "ls-remote", "clone"):
        remote = git_env.env_for([verb], tmp_path, {})
        assert remote["GIT_SSH_COMMAND"] == "ssh -o BatchMode=yes"
    assert (
        git_env.ssh_command({"GIT_SSH": "/opt/my ssh/plink"}, tmp_path)
        == "'/opt/my ssh/plink' -o BatchMode=yes"
    )
    # Right after the program, the rest kept as written (quotes, variables).
    for command, expected in (
        (
            "ssh -i ~/.ssh/k -o BatchMode=no",
            "ssh -o BatchMode=yes -i ~/.ssh/k -o BatchMode=no",
        ),
        ("'/opt/my ssh/ssh' -p 2222", "'/opt/my ssh/ssh' -o BatchMode=yes -p 2222"),
        (
            "SSH_AUTH_SOCK=$HOME/a ssh -v",
            "SSH_AUTH_SOCK=$HOME/a ssh -o BatchMode=yes -v",
        ),
    ):
        assert git_env.ssh_command({"GIT_SSH_COMMAND": command}, tmp_path) == expected
    assert git_env.needs_interaction("Permission denied (publickey).")
    assert git_env.needs_interaction("fatal: could not read Username for 'https://x'")
    assert not git_env.needs_interaction("Could not resolve host: example.invalid")
