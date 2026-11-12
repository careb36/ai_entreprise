"""A delivery's reply: where the files went, and the paths to judge it against.

``deliver`` picks how a delivery lands: with the company's Git workflow, a
commit on the task's branch in its worktree (``git_workspace.deliver``);
without it, in the person's folder with a snapshot that undoes it
(``delivery_snapshot.deliver``). The caller supplies the writer and the path
resolver, since those are the daemon's own (``daemon.write_files``,
``daemon._resolve_target_path``).

The reply then carries ``project_files``: the paths of the project the
delivery was written into, listed by ``workspace_read.project_files`` and
bounded there. They are an observation the Verifier resolves a delivery's
imports against (``services/evidence/grounding.py``); a listing that fails
never blocks the delivery.
"""

from __future__ import annotations

import logging
from typing import Any, Callable, Dict, Optional

import delivery_snapshot
import git_base
import git_workspace
import workspace_read

logger = logging.getLogger("fabrique-daemon")


def deliver(
    workspace_path: str,
    files: list,
    git: Optional[Dict[str, Any]],
    write_files: Callable[[str, list], list],
    resolve: Callable[..., Any],
) -> Dict[str, Any]:
    """``write_files`` in the reply shape the backend reads, plus the project's paths."""
    try:
        if git and not git_base.not_a_repository(workspace_path):
            response = git_workspace.deliver(workspace_path, git, files, write_files)
        else:
            response = {
                **delivery_snapshot.deliver(
                    workspace_path, files, write_files, resolve
                ),
                **({"git_workflow_skipped": "not a repository"} if git else {}),
            }
        try:
            # A Git delivery wrote into its worktree, not the person's checkout.
            root = response.get("workspace_root") or workspace_path
            response["project_files"] = workspace_read.project_files(root)
        except (workspace_read.ReadError, OSError):
            pass
        return response
    except (OSError, git_workspace.GitError) as e:
        logger.error("Writing files under %s failed: %s", workspace_path, e)
        return {
            "ok": False,
            "workspace_root": workspace_path,
            "files": [],
            "error": str(e),
        }
