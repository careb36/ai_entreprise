"""What a task's branch changes against its base, read by the daemon, as text for a reviewer.

QA used to read only the latest delivery: the files the agent wrote in that
attempt. A rework writes only what it touches, so in sprint 3 (issue #766)
QA sent task 17 back for "no implementation change to ``self_host.py`` or
``container_mode.py``" while its branch held both, written by an earlier
attempt; the agent, sent back, wrote the documentation a third time under a
new name. The branch is the delivery: every attempt's commits on
``fabrique/task-<id>`` since it left its base.

The view is the daemon's ``workspace.git.diff`` of the delivery's commit
against the merge base with the company's base (the same request a sprint's
integration review makes): an observation of the repository, never the
agent's account of it. The commit, repository and base come from the
Verifier's ``git`` evidence of the latest delivery, which the daemon
reported when it committed. Without that evidence (no Git workflow), or when
the daemon cannot answer, ``read`` returns ``None`` and the reviewer reads
the delivery as before.

``render`` lists every changed path with its counts, then each file's patch,
sharing ``limit`` characters between them. A patch the limit cuts says how
many lines were left out, so a cut is never read as missing work.
"""

from __future__ import annotations

import logging
import re
from typing import Any

from services.workspace.workspace_daemon import (
    DaemonError,
    DaemonUnavailable,
    daemon_request,
)

logger = logging.getLogger(__name__)

DIFF_SUBJECT = "workspace.git.diff"
DIFF_TIMEOUT_SECONDS = 20.0
HEADER = "Task branch"
MIN_PATCH_CHARS = 200
_SHA = re.compile(r"^[0-9a-f]{40}([0-9a-f]{24})?$")
_OMITTED = {"binary": "binary file, not shown", "too_large": "too large to show"}


def request(git: Any, forge_policy: str) -> dict[str, Any] | None:
    """The ``workspace.git.diff`` request for a delivery's ``git`` evidence, or ``None``."""
    if not isinstance(git, dict):
        return None
    repository, commit = git.get("repository"), git.get("commit")
    if not isinstance(repository, str) or not repository:
        return None
    if not isinstance(commit, str) or not _SHA.match(commit):
        return None
    base = git.get("base")
    return {
        "workspace_root": repository,
        "base": base if isinstance(base, str) else "",
        "commit": commit,
        "forge_policy": forge_policy,
    }


def _excerpt(patch: str, budget: int) -> str:
    if len(patch) <= budget:
        return patch
    kept: list[str] = []
    used = 0
    lines = patch.splitlines()
    for line in lines:
        if used + len(line) + 1 > budget:
            break
        kept.append(line)
        used += len(line) + 1
    left = len(lines) - len(kept)
    return "\n".join(kept) + f"\n[... {left} more line(s) of this patch not shown here]"


def render(reply: dict[str, Any], branch: str, limit: int) -> str | None:
    """Pure: the daemon's diff as a reviewer reads it, within about ``limit`` characters."""
    files = reply.get("files")
    if not reply.get("ok") or not isinstance(files, list):
        return None
    files = [f for f in files if isinstance(f, dict) and isinstance(f.get("path"), str)]
    commit = str(reply.get("commit") or "")[:7]
    merge_base = str(reply.get("merge_base") or "")[:7]
    base = str(reply.get("base") or "the base")
    title = (
        f"{HEADER} {branch or '(unnamed)'} against {base} "
        f"(merge base {merge_base}, commit {commit}), read by the workspace daemon: "
        f"{len(files)} file(s) changed by every attempt, not only the latest."
    )
    lines = [title]
    for f in files:
        lines.append(
            f"- {f['path']} ({f.get('change') or 'modified'}, "
            f"+{f.get('additions') or 0} -{f.get('deletions') or 0})"
        )
    if reply.get("truncated"):
        lines.append("- [more files changed than the daemon lists]")
    index = "\n".join(lines)
    budget = max(limit - len(index), 0)
    parts = [index]
    for position, f in enumerate(files):
        heading = f"\n\n# Artifact: {f['path']} ({f.get('change') or 'modified'})\n"
        omitted = f.get("omitted")
        patch = f.get("patch") if isinstance(f.get("patch"), str) else ""
        if omitted or not patch:
            body = f"[{_OMITTED.get(str(omitted), 'no patch to show')}]"
        else:
            share = max(budget // (len(files) - position), MIN_PATCH_CHARS)
            body = _excerpt(patch.strip("\n"), max(share - len(heading), 0))
        budget -= len(heading) + len(body)
        parts.append(heading + body)
    return "".join(parts)


async def read(
    nats_client: Any, spec: dict[str, Any], branch: str, limit: int
) -> str | None:
    """The rendered diff of *spec*, or ``None`` when the daemon cannot give it."""
    try:
        reply = await daemon_request(
            nats_client, DIFF_SUBJECT, spec, timeout=DIFF_TIMEOUT_SECONDS
        )
    except (DaemonUnavailable, DaemonError) as e:
        logger.warning("branch view of %s unavailable: %s", branch, e)
        return None
    if reply.get("commit") != spec.get("commit"):
        logger.warning("branch view of %s: the daemon read another commit", branch)
        return None
    return render(reply, branch, limit)
