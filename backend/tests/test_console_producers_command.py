"""Console producers, the command line and the check.

The command policy runs only the CLI's own command and no prompt escapes its
quotes, a prompt ending in ``$`` passes it, the command stays within one
argument's limit in bytes; the ``console_run`` check names the changes not
delivered, and a missing binary is never read from a run that exited 0.

Console producers: a terminal coding agent produces a role's task, and the
delivery is what the daemon observes (``services/producers/console_producer.py``,
``services/producers/console_clis.py``, ``vscode-daemon/console_run.py``).
Real git in ``tmp_path`` and the real daemon, with a fake CLI on ``PATH`` (as
``test_console_assistants`` and the e2e's ``e2e-copilot`` do); the shared
fixtures are in ``console_producer_helpers``.
"""

import shlex
import subprocess
import sys
from pathlib import Path

from services.producers import console_clis

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "vscode-daemon"))
import command_policy


def test_the_command_policy_runs_only_the_cli_and_the_prompt_cannot_escape(
    tmp_path,
):
    policy = console_clis.command_policy("claude")
    tricky = 'a\'b "c" `id` $(id) ; rm -rf / && echo\nnext line | cat > /etc/x'
    assert (
        command_policy.refusal(console_clis.render_command("claude", tricky), policy)
        is None
    )
    assert command_policy.refusal("rm -rf /", policy)
    assert command_policy.refusal("claude --help; rm -rf /", policy)
    assert command_policy.refusal(console_clis.render_command("codex", "x"), policy)
    # Every CLI's own command passes its own policy.
    for cli in console_clis.CLIS:
        command = console_clis.render_command(cli, tricky)
        assert command_policy.refusal(command, console_clis.command_policy(cli)) is None


def test_changes_not_delivered_are_said_by_the_check():
    check = console_clis.check_console_run(
        {
            "console_run": {
                "cli": "claude",
                "label": "Claude Code",
                "outcome": "changed",
                "exit_code": 0,
                "files": [
                    {"path": "calc.py", "delivered": True},
                    {"path": "logo.png", "delivered": False, "status": "binary"},
                ],
            }
        }
    )
    assert check["outcome"] == "pass"
    assert "1 change(s) not delivered: logo.png (binary)" in check["observation"]
    assert check["not_delivered"] == ["logo.png (binary)"]


def test_a_missing_binary_is_never_read_from_the_output_of_a_run_that_exited_0():
    ok = {
        "success": True,
        "exit_code": 0,
        "stderr": "open x: No such file or directory",
    }
    assert console_clis.missing_binary(ok) is False
    assert console_clis.missing_binary({"exit_code": 127}) is True
    assert console_clis.missing_binary(
        {"success": False, "exit_code": None, "error": "No such file or directory"}
    )
    collected = {"ok": True, "files": [{"path": "a.py"}]}
    assert console_clis.failure(ok, collected, [{"path": "a.py"}]) is None


def test_a_prompt_ending_in_a_dollar_passes_the_policy():
    for prompt in ("costs 5$", "a$", "$", "x$\n"):
        command = console_clis.render_command("claude", prompt)
        assert "$'" not in command and '$"' not in command
        assert (
            command_policy.refusal(command, console_clis.command_policy("claude"))
            is None
        ), (prompt, command)


def test_the_command_line_stays_within_one_arguments_limit_in_bytes():
    """The sandbox passes the command as one ``sh -c`` argument (Linux:
    128 KiB, bytes). 100,000 characters of accents and apostrophes quote to
    far more; the prompt is cut, with a note, until the command fits."""
    prompt = "é'" * 50_000
    old = f"{console_clis.command_prefix('claude')} {shlex.quote(prompt)}"
    assert len(old.encode()) > 128 * 1024  # What the char cap alone allowed.
    for cli in console_clis.CLIS:
        command = console_clis.render_command(cli, prompt)
        assert len(command.encode()) <= console_clis.COMMAND_MAX_BYTES
        sent = console_clis.sent_prompt(cli, prompt)
        assert sent.endswith(console_clis.CUT_NOTE)
        assert shlex.split(command)[-1] == sent
        assert console_clis.sent_prompt(cli, sent) == sent  # A fixed point.
        assert command_policy.refusal(command, console_clis.command_policy(cli)) is None
    # The real exec takes it as one argument.
    done = subprocess.run(
        [
            "/bin/sh",
            "-c",
            console_clis.render_command("claude", prompt).replace(
                console_clis.command_prefix("claude"), "printf %s", 1
            ),
        ],
        capture_output=True,
        check=True,
    )
    assert done.stdout.decode() == console_clis.sent_prompt("claude", prompt)
    # A prompt that fits is sent whole.
    assert console_clis.sent_prompt("claude", "Fix add.") == "Fix add."
