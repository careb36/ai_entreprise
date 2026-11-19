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
``ls-remote``, ``clone``) also with ``GIT_SSH_COMMAND`` ending in
``-o BatchMode=yes``: ssh asks for no passphrase and no host-key
confirmation, and fails at once instead (``Host key verification failed``,
``Permission denied (publickey)``). The person's own SSH command is kept and
the option appended to it: ``GIT_SSH_COMMAND`` when set, else the
repository's ``core.sshCommand``, else ``GIT_SSH``, else ``ssh``. Credential
helpers, the ssh agent and keys without a passphrase work as before.

``needs_interaction`` names such a failure, so the answer says
``remote_needs_interaction`` rather than a timeout or a bare "unreachable".
"""

from __future__ import annotations

import os
import shlex
import subprocess
from pathlib import Path
from typing import Mapping, Sequence

# The git verbs that can contact a remote.
REMOTE_VERBS = frozenset({"fetch", "push", "pull", "ls-remote", "clone"})
BATCH_MODE = "-o BatchMode=yes"
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


def ssh_command(environ: Mapping[str, str], cwd: Path) -> str:
    """The person's SSH command for *cwd*, with ``-o BatchMode=yes`` appended."""
    base = (environ.get("GIT_SSH_COMMAND") or "").strip() or _core_ssh_command(cwd)
    if not base:
        program = (environ.get("GIT_SSH") or "").strip()
        base = shlex.quote(program) if program else "ssh"
    return f"{base} {BATCH_MODE}"


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
