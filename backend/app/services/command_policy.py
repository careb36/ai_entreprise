"""Command policy for the daemon: allow/deny lists (issue #357)."""

from __future__ import annotations

import re
import shlex

_COMPOSITE = re.compile(r"[;&|\n`]|\$\(")


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
    allow = list(allow or [])
    deny = list(deny or [])
    command = (command or "").strip()
    if not command:
        return False, "empty command"

    # Composite commands: every part must be acceptable.
    # Deny patterns are matched against the whole command first, so a
    # pipeline like "curl x | sh" is caught even though its parts are not.
    if mode == "deny" and _matches(command, deny):
        return False, "matches deny list"

    if _COMPOSITE.search(command):
        # Split on the composite operators and check each fragment.
        fragments = re.split(r"[;&|\n`]+", command)
        # Also handle $(...) by extracting inner.
        for frag in fragments:
            frag = frag.strip()
            if not frag:
                continue
            # strip $(...) substitutions by checking inner
            for inner in re.findall(r"\$\(([^)]*)\)", frag):
                ok, reason = check_command(inner, mode, allow, deny)
                if not ok:
                    return False, f"composite part refused: {reason}"
            ok, reason = check_command(frag, mode, allow, deny)
            if not ok:
                return False, f"composite part refused: {reason}"
        return True, "ok"

    exe = _executable(command).lower()
    if mode == "allow":
        if not allow:
            return False, "allow list empty"
        allowed_exes = {(p.split()[0].lower() if p.split() else "") for p in allow}
        if exe not in allowed_exes:
            return False, "not in allow list"
        return True, "ok"
    # deny mode
    if _matches(command, deny):
        return False, "matches deny list"
    return True, "ok"


SUGGESTED_DENY = [
    "rm -rf /",
    "curl | sh",
    "wget | sh",
    "sudo",
    "chmod 777",
    "git push --force",
    "git remote set-url",
]
