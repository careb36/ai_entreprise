"""Command policy for the daemon: allow/deny lists (issue #357).

The daemon is the only component that runs commands, so the policy lives
here in spirit and is evaluated against each ``workspace.execute`` request.
A policy is either ``allow`` (whitelist: only those commands run) or ``deny``
(blacklist: everything except those). A composite command (``;``, ``&&``,
``|``, ``$(...)``) is accepted only when every part is accepted.
"""

from __future__ import annotations

import re
import shlex

_COMPOSITE = re.compile(r"[;&|]|\$\(")


def _executable(command: str) -> str:
    try:
        parts = shlex.split(command)
    except ValueError:
        parts = command.split()
    return parts[0] if parts else ""


def _matches(command: str, patterns: list[str]) -> bool:
    low = command.strip().lower()
    for pat in patterns:
        if pat and pat.lower() in low:
            return True
    return False


def check_command(
    command: str,
    mode: str = "deny",
    allow: list[str] | None = None,
    deny: list[str] | None = None,
) -> tuple[bool, str]:
    """Return (allowed, reason). ``mode`` is 'allow' or 'deny'."""
    allow = list(allow or [])
    deny = list(deny or [])
    command = (command or "").strip()
    if not command:
        return False, "empty command"

    # Composite commands: every part must be acceptable.
    if _COMPOSITE.search(command):
        # Split on the composite operators and check each fragment.
        fragments = re.split(r"[;&|]+", command)
        for frag in fragments:
            frag = frag.strip()
            if not frag:
                continue
            ok, reason = check_command(frag, mode, allow, deny)
            if not ok:
                return False, f"composite part refused: {reason}"
        return True, "ok"

    if mode == "allow":
        if allow and not _matches(command, allow):
            return False, "not in allow list"
        return True, "ok"
    # deny mode
    if _matches(command, deny):
        return False, "matches deny list"
    return True, "ok"


# Suggested deny list a Manager adopts with one click.
SUGGESTED_DENY = [
    "rm -rf /",
    "curl | sh",
    "wget | sh",
    "sudo",
    "chmod 777",
    "git push --force",
    "git remote set-url",
]
