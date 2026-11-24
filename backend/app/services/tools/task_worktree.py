"""The task's own worktree, prepared before the first agent turn (Git workflow).

With the company's Git workflow on, a task's deliveries are commits on
``fabrique/task-<id>`` in a worktree the daemon makes under the repository's
git directory (``vscode-daemon/git_workspace.py``). Until 2026-11-24 that
worktree appeared only at the first delivery, so a task's first attempt ran
its tools in the person's own checkout, where nothing may be written
(``workspace_edits.allowed``): edit, move and delete were never offered on a
first attempt.

Now the task worker asks the daemon for the worktree before the first turn
(``enter``), through ``workspace.console.prepare`` with ``private_git: false``
(the worktree made like a delivery's, the done prerequisites merged, put back
at its ``HEAD``, git's own pointer kept), and the tools of that attempt look
there (``developer_tools.tool_root`` reads ``prepared_for``). The delivery
names the same branch, so the daemon commits in the same worktree as always.

Only when every daemon announces the ``private_git`` feature: an older daemon
would ignore the field and give the worktree a console producer's private
repository, so it gets no request and the attempt runs as before. A reply
that does not name the task's own worktree is not used. The daemon's reply is
kept on the run's evidence (``tools_offered.task_worktree``), apart from
anything the model says. Design: ``docs/design/first-attempt-in-task-worktree.md``.
"""

from __future__ import annotations

import logging
import re
from collections.abc import Callable
from contextvars import ContextVar
from typing import Any

from services.workspace import daemon_liveness, git_workflow

logger = logging.getLogger("fabrique-task-worker")

SUBJECT = "workspace.console.prepare"
FEATURE = "private_git"
# Where the daemon keeps task worktrees (``git_workspace.WORKTREES_DIR``).
WORKTREES_DIR = "fabrique-worktrees"
# The reply's fields kept on the evidence: where the tools ran, and why not.
KEPT = (
    "ok",
    "worktree",
    "branch",
    "head",
    "merged",
    "pointer_restored",
    "private_git",
    "base_commit",
    "base_behind_remote",
    "remote_unreachable",
    "remote_contacted",
    "forge_policy",
    "error",
)

# (company_id, item_id, worktree) for the attempt this context runs. The task
# worker runs an attempt in its own thread (``asyncio.to_thread`` copies the
# context), and ``leave`` clears it when the attempt's tools are done.
_current: ContextVar[tuple[int, int, str] | None] = ContextVar(
    "fabrique_task_worktree", default=None
)


def is_task_worktree(path: Any, branch: str, root: str) -> bool:
    """Whether *path* is the daemon's worktree for *branch*, never *root* itself."""
    if not isinstance(path, str) or not path or path == root:
        return False
    # The daemon's machine may be Windows: either separator.
    parts = re.split(r"[\\/]+", path.rstrip("\\/"))
    return parts[-2:] == [WORKTREES_DIR, branch.replace("/", "__")]


def enter(
    request: Callable[[str, dict[str, Any], float], dict[str, Any]],
    resolve_root: Callable[[int], str | None] | None,
    company_id: int | None,
    item_id: int | None,
    engine: Any,
) -> dict[str, Any] | None:
    """Prepare the task's worktree and make it this attempt's tool root.

    ``None`` when nothing was asked (no company, task or database, the
    workflow off, no project, or a daemon that does not announce the
    feature); else the daemon's reply as kept on the evidence, ``ok`` false
    with why when the worktree could not be used (the tools then run where
    they did before)."""
    leave()
    if company_id is None or item_id is None or engine is None or resolve_root is None:
        return None
    if daemon_liveness.announces(SUBJECT, FEATURE) is not True:
        return None
    try:
        spec = git_workflow.task_spec(engine, company_id, item_id)
        root = resolve_root(company_id) if spec else None
    except Exception as exc:  # noqa: BLE001 - Unknown: the attempt runs as before.
        logger.warning("Could not read the task's Git spec: %s", exc)
        return None
    if not spec or not root:
        return None
    payload = {"workspace_root": root, "git": spec, "private_git": False}
    try:
        reply = request(SUBJECT, payload, git_workflow.delivery_timeout(spec))
    except Exception as exc:  # noqa: BLE001 - Said on the evidence, never raised.
        return {"ok": False, "error": f"{type(exc).__name__}: {exc}"}
    kept = {k: reply.get(k) for k in KEPT if k in reply}
    worktree = reply.get("worktree")
    if not reply.get("ok") or reply.get("private_git") is not False:
        kept["ok"] = False
        kept.setdefault(
            "error", "the daemon did not prepare the worktree for the tools"
        )
        return kept
    if not is_task_worktree(worktree, spec["branch"], root):
        return {
            **kept,
            "ok": False,
            "error": "the reply did not name the task's worktree",
        }
    _current.set((company_id, item_id, worktree))
    return kept


def prepared_for(company_id: int | None, item_id: int | None) -> str | None:
    """The worktree ``enter`` prepared for this task in this attempt, else ``None``."""
    current = _current.get()
    if current is None or item_id is None:
        return None
    return current[2] if current[:2] == (company_id, item_id) else None


def leave() -> None:
    """The attempt's tools are done: ``tool_root`` reads the evidence again."""
    _current.set(None)
