"""The environment of every git command this daemon runs: never a prompt.

The daemon runs git on the person's computer with nobody at its terminal. A
command that asks for something (a password, an SSH key's passphrase, whether
to trust an unknown host key) would wait until its time limit and then fail
with no reason worth reading. So every git command runs with:

- ``GIT_TERMINAL_PROMPT=0``: git asks nothing on the terminal;
- ``GIT_ASKPASS`` empty: no graphical askpass either (git skips the askpass
  programs when the variable is set and empty, and with no terminal it
  fails at once with ``terminal prompts disabled``);
- ``GCM_INTERACTIVE=never``: Git Credential Manager uses what it holds and
  shows no window;

and a command that can reach a remote (``fetch``, ``push``, ``pull``,
``ls-remote``, ``clone``) also with ``GIT_SSH_COMMAND`` giving
``-o BatchMode=yes``: ssh asks for no passphrase and no host-key
confirmation, and fails at once instead (``Host key verification failed``,
``Permission denied (publickey)``). The person's own SSH command is kept
(``GIT_SSH_COMMAND`` when set, else the repository's ``core.sshCommand``,
else ``GIT_SSH``, else ``ssh``) with the option put right after its program,
before the person's own options: ssh keeps the first value of an option, so
a ``-o BatchMode=no`` there cannot bring a prompt back. With no terminal, a
prompt could never be answered, only waited out. Credential
helpers, the ssh agent and keys without a passphrase work as before.

``needs_interaction`` names such a failure, so the answer says
``remote_needs_interaction`` rather than a timeout or a bare "unreachable".
"""

from __future__ import annotations

import os
import re
import shlex
import subprocess
from pathlib import Path
from typing import Mapping, Sequence

# The git verbs that can contact a remote.
REMOTE_VERBS = frozenset({"fetch", "push", "pull", "ls-remote", "clone"})
BATCH_MODE = "-o BatchMode=yes"
_ASSIGNMENT = re.compile(r"^[A-Za-z_][A-Za-z0-9_]*=")
NEEDS_INTERACTION = "remote_needs_interaction"
# What git or ssh write when they stopped because they would have asked.
_INTERACTION_SIGNS = (
    "host key verification failed",
    "terminal prompts disabled",
    "could not read username",
    "could not read password",
    "permission denied (publickey",
    "passphrase",
    "no more authentication methods",
)


def _core_ssh_command(cwd: Path) -> str:
    try:
        found = subprocess.run(  # noqa: S603 - fixed argv, no shell.
            ["git", "config", "--get", "core.sshCommand"],
            cwd=str(cwd),
            env={**os.environ, "LC_ALL": "C"},
            capture_output=True,
            text=True,
            timeout=10,
            check=False,
        )
    except (OSError, subprocess.SubprocessError):
        return ""
    return found.stdout.strip() if found.returncode == 0 else ""


def _with_batch_mode(command: str) -> str:
    """*command* with ``-o BatchMode=yes`` right after its program, before the person's options.

    ssh keeps the first value it is given for an option, so a ``-o
    BatchMode=no`` of the person's, which here could only turn a refusal into
    a wait nobody can answer, comes second and loses. The rest of the command
    is kept as written (quoting, variables); leading ``NAME=value``
    assignments stay in front of the program.
    """
    lexer = shlex.shlex(command, posix=True)
    lexer.whitespace_split = True
    while True:
        token = lexer.get_token()
        if token is None:
            return f"{command} {BATCH_MODE}"
        if not _ASSIGNMENT.match(token):
            break
    rest = lexer.instream.read()
    head = command[: len(command) - len(rest)].rstrip()
    return f"{head} {BATCH_MODE} {rest}".rstrip()


def ssh_command(environ: Mapping[str, str], cwd: Path) -> str:
    """The person's SSH command for *cwd*, with ``-o BatchMode=yes`` given first."""
    base = (environ.get("GIT_SSH_COMMAND") or "").strip() or _core_ssh_command(cwd)
    if not base:
        program = (environ.get("GIT_SSH") or "").strip()
        base = shlex.quote(program) if program else "ssh"
    return _with_batch_mode(base)


def env_for(args: Sequence[str], cwd: Path, environ: Mapping[str, str] = os.environ) -> dict:
    """The environment for ``git <args>`` in *cwd*: nothing may prompt."""
    env = {
        **environ,
        "GIT_TERMINAL_PROMPT": "0",
        "GIT_ASKPASS": "",
        "GCM_INTERACTIVE": "never",
        "LC_ALL": "C",
    }
    if args and args[0] in REMOTE_VERBS:
        env["GIT_SSH_COMMAND"] = ssh_command(environ, cwd)
    return env


def needs_interaction(reason: str) -> bool:
    """Whether git's or ssh's words say the remote wanted a person (a passphrase, a host key, a password)."""
    lowered = (reason or "").lower()
    return any(sign in lowered for sign in _INTERACTION_SIGNS)
