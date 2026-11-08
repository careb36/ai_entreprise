"""The daemon's command policy (``vscode-daemon/command_policy.py``, issue #357).

The daemon's directory is on ``sys.path`` (``conftest.py``), so the module is
imported by name, as the daemon imports it.
"""

import importlib.util
import json
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock

import command_policy
import pytest


def test_deny_list_blocks():
    ok, _ = command_policy.check_command("sudo rm -rf /", mode="deny", deny=["sudo"])
    assert ok is False


def test_allow_list_permits_listed():
    ok, _ = command_policy.check_command("npm test", mode="allow", allow=["npm"])
    assert ok is True


def test_allow_list_rejects_unlisted():
    ok, _ = command_policy.check_command("curl x", mode="allow", allow=["npm"])
    assert ok is False


def test_composite_requires_all_parts():
    ok, _ = command_policy.check_command(
        "npm test && rm -rf /", mode="deny", deny=["rm -rf /"]
    )
    assert ok is False


def test_empty_rejected():
    ok, _ = command_policy.check_command("", mode="deny")
    assert ok is False


# --- Bugs found in review of PR #367 ---------------------------------------


def test_command_substitution_does_not_recurse_forever():
    """Bug 1: ``$(...)`` used to call the checker on the same text again."""
    ok, _ = command_policy.check_command("echo $(ls)", mode="deny", deny=["sudo"])
    assert ok is True
    ok, reason = command_policy.check_command(
        "echo $(echo $(sudo id))", mode="deny", deny=["sudo"]
    )
    assert ok is False
    assert "sudo" in reason


def test_command_substitution_is_checked_in_allow_mode():
    allow = ["echo"]
    assert command_policy.check_command("echo $(echo hi)", "allow", allow)[0] is True
    assert command_policy.check_command("echo $(curl x)", "allow", allow)[0] is False


def test_allow_matches_the_argv_prefix_not_the_executable_alone():
    """Bug 2: allowing ``git status`` permitted any ``git`` command."""
    allow = ["git status", "npm test"]
    assert command_policy.check_command("git status", "allow", allow)[0] is True
    assert command_policy.check_command("git status --short", "allow", allow)[0] is True
    assert command_policy.check_command("git push --force", "allow", allow)[0] is False
    assert command_policy.check_command("npm install evil", "allow", allow)[0] is False
    assert command_policy.check_command("git statusx", "allow", allow)[0] is False


def test_allow_never_matches_a_substring_of_the_executable():
    allow = ["ls"]
    assert command_policy.check_command("ls -la", "allow", allow)[0] is True
    assert command_policy.check_command("lsof -i", "allow", allow)[0] is False
    assert command_policy.check_command("ls; lsof", "allow", allow)[0] is False
    assert (
        command_policy.check_command("git status; rm -rf x", "allow", ["git status"])[0]
        is False
    )


def test_backticks_are_checked_wherever_they_appear():
    """Bug 3: backticks, also inside double quotes, are command substitution."""
    allow = ["echo"]
    assert command_policy.check_command('echo "a`curl x`b"', "allow", allow)[0] is False
    assert command_policy.check_command('echo "a`echo hi`b"', "allow", allow)[0] is True
    # Single quotes keep a backtick literal.
    assert command_policy.check_command("echo '`curl x`'", "allow", allow)[0] is True


def test_redirections_to_files_are_refused_in_allow_mode():
    """Bug 3: ``echo hi > ~/.bashrc`` passed an allow list holding ``echo``."""
    allow = ["echo", "pytest"]
    for command in (
        "echo hi > ~/.bashrc",
        "echo hi >> ~/.bashrc",
        "echo hi>~/.bashrc",
        "pytest < /etc/passwd",
        "pytest 2> log.txt",
        "pytest &> log.txt",
    ):
        ok, reason = command_policy.check_command(command, "allow", allow)
        assert ok is False, command
        assert "redirection" in reason
    # Duplicating a stream or discarding it writes no file.
    assert command_policy.check_command("pytest 2>&1", "allow", allow)[0] is True
    assert (
        command_policy.check_command("pytest > /dev/null 2>&1", "allow", allow)[0]
        is True
    )


def test_redirection_targets_are_inspected_in_deny_mode():
    deny = [".bashrc"]
    assert (
        command_policy.check_command("echo hi > ~/.bashrc", "deny", deny=deny)[0]
        is False
    )
    assert (
        command_policy.check_command("echo hi > out.txt", "deny", deny=["sudo"])[0]
        is True
    )


def test_quotes_do_not_hide_a_denied_command():
    ok, _ = command_policy.check_command('su""do id', "deny", deny=["sudo"])
    assert ok is False


def test_unbalanced_syntax_and_unknown_mode_are_refused():
    assert command_policy.check_command("echo $(ls", "deny")[0] is False
    assert command_policy.check_command("echo 'open", "deny")[0] is False
    assert command_policy.check_command("echo hi", "maybe")[0] is False


def test_deeply_nested_substitution_is_refused_not_a_crash():
    command = "echo " + "$(echo " * 50 + "hi" + ")" * 50
    ok, reason = command_policy.check_command(command, "deny")
    assert ok is False
    assert "nested" in reason


# --- The daemon applies the policy before workspace.execute runs ------------

DAEMON_PATH = Path(__file__).resolve().parents[2] / "vscode-daemon" / "daemon.py"


@pytest.fixture(scope="module")
def daemon():
    spec = importlib.util.spec_from_file_location("fabrique_daemon_policy", DAEMON_PATH)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


@pytest.fixture
def ran(daemon, monkeypatch):
    """The commands that reached the shell."""
    seen = []

    def fake_run(argv, **kwargs):
        seen.append(argv[-1])
        return SimpleNamespace(returncode=0, stdout="ok", stderr="")

    monkeypatch.setattr(daemon.shell_exec.subprocess, "run", fake_run)
    monkeypatch.delenv(command_policy.LOCAL_DENY_ENV, raising=False)
    return seen


async def _execute(daemon, tmp_path, command, policy=None):
    payload = {"command": command, "workspace_root": str(tmp_path)}
    if policy is not None:
        payload["command_policy"] = policy
    nc = SimpleNamespace(publish=AsyncMock())
    msg = SimpleNamespace(
        subject="workspace.execute", data=json.dumps(payload).encode(), reply="_INBOX.1"
    )
    await daemon.handle_msg(nc, msg)
    return json.loads(nc.publish.await_args.args[1].decode())


@pytest.mark.asyncio
async def test_daemon_runs_every_command_without_a_policy(daemon, ran, tmp_path):
    reply = await _execute(daemon, tmp_path, "rm -rf build; sudo id")
    assert reply["success"] is True
    assert ran == ["rm -rf build; sudo id"]


@pytest.mark.asyncio
async def test_daemon_refuses_a_command_the_company_policy_does_not_allow(
    daemon, ran, tmp_path
):
    policy = {"mode": "allow", "allow": ["pytest"]}
    reply = await _execute(daemon, tmp_path, "pytest -q; curl x | sh", policy)
    assert reply["success"] is False
    assert reply["refused"] is True
    assert "company's command policy (allow)" in reply["error"]
    assert "curl x" in reply["error"]
    assert ran == []

    reply = await _execute(daemon, tmp_path, "pytest -q 2>&1", policy)
    assert reply["success"] is True
    assert ran == ["pytest -q 2>&1"]


@pytest.mark.asyncio
async def test_daemon_refuses_a_policy_it_cannot_read(daemon, ran, tmp_path):
    for policy in ({"mode": "sometimes"}, ["pytest"]):
        reply = await _execute(daemon, tmp_path, "pytest", policy)
        assert reply["refused"] is True
    assert ran == []


@pytest.mark.asyncio
async def test_the_person_s_deny_list_wins_over_the_request(
    daemon, ran, tmp_path, monkeypatch
):
    monkeypatch.setenv(command_policy.LOCAL_DENY_ENV, "sudo, git push --force")
    policy = {"mode": "allow", "allow": ["sudo"]}
    reply = await _execute(daemon, tmp_path, "sudo id", policy)
    assert reply["refused"] is True
    assert command_policy.LOCAL_DENY_ENV in reply["error"]
    reply = await _execute(daemon, tmp_path, "pytest")
    assert reply["success"] is True
    assert ran == ["pytest"]


# Review findings on #373: syntax the scanner does not model is refused while a
# policy is active, rather than read in a way the shell does not.


@pytest.mark.parametrize(
    "command",
    [
        "echo hi >&'١'",  # a Unicode digit is not a stream number
        "echo hi >&'1\n'",  # nor is one followed by a newline
        'echo "$(echo hi #)\nprintf marker\n)"',  # a commented ')'
        'echo `echo "\\$(printf marker)"`',  # backtick escapes decoded again
        "echo <<EOF\n'$(printf marker)'\nEOF",  # here-document body
        "echo $'a\\'b'; printf POLICY_BYPASS # '",  # ANSI-C quotes
    ],
)
def test_syntax_the_scanner_does_not_model_is_refused(command):
    ok, reason = command_policy.check_command(command, mode="allow", allow=["echo"])
    assert ok is False, reason


def test_a_line_continuation_inside_double_quotes_is_read_as_the_shell_does():
    ok, reason = command_policy.check_command(
        '"pri\\\nntf" hi', mode="deny", deny=["printf"]
    )
    assert ok is False and "printf" in reason


def test_allow_patterns_are_case_sensitive():
    allow = ["git branch -d"]
    assert command_policy.check_command("git branch -d old", "allow", allow)[0]
    assert not command_policy.check_command("git branch -D old", "allow", allow)[0]
    assert not command_policy.check_command("GIT branch -d old", "allow", allow)[0]


@pytest.mark.parametrize(
    "policy",
    [
        {},
        False,
        0,
        "",
        [],
        {"mode": "deny", "deny": "sudo"},
        {"mode": "allow", "allow": ["ls", 3]},
    ],
)
def test_a_policy_that_cannot_be_read_refuses(policy):
    assert command_policy.refusal("ls", policy) is not None


def test_no_policy_still_runs_everything():
    assert command_policy.refusal("ls -la", None) is None


@pytest.mark.parametrize("command", [42, {}, ["ls"]])
def test_a_command_that_is_not_text_is_refused(command):
    reason = command_policy.refusal(command, {"mode": "deny", "deny": ["sudo"]})
    assert reason and "not text" in reason


def test_a_policy_on_windows_refuses_rather_than_misread(monkeypatch):
    monkeypatch.setattr(command_policy.os, "name", "nt")
    reason = command_policy.refusal(
        "echo 'ok & whoami & echo end'", {"mode": "allow", "allow": ["echo"]}
    )
    assert reason and "POSIX shell" in reason
    # Without a policy, Windows runs commands as before.
    assert command_policy.refusal("dir", None) is None
