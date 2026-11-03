"""The company's Git workflow: each task's deliveries as commits on its own branch.

With ``Company.git_workflow`` on, the task worker asks the workspace daemon
to write a task's files on ``fabrique/task-<id>``, in a worktree under the
repository's git directory, and commit them (``vscode-daemon/git_workspace.py``),
the way a pull request's branch is pushed to: the person's checkout, index
and branch are never touched. The daemon's answer (the branch, the commit and
its parent) is an observation kept on the run's evidence as ``git``, the
tests the daemon runs after a delivery run in that worktree, and the run's
Files changed show the commit. Merging the branch is a person's call, or the
platform's it is published to.
"""

from __future__ import annotations

import re
from typing import Any

from models.company import Company
from sqlalchemy.engine import Engine
from sqlmodel import Session

TASK_BRANCH = "fabrique/task-{id}"
# A base a Manager may name: what git accepts as a branch, without ranges or options.
_BASE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9/_.-]{0,199}$")


def valid_base(name: str | None) -> bool:
    return name is None or (
        bool(_BASE.match(name))
        and ".." not in name
        and not name.endswith((".lock", "/", "."))
    )


def commit_message(item: dict[str, Any], sprint: dict[str, Any]) -> str:
    title = " ".join(str(item.get("title") or f"Task {item['id']}").split())[:72]
    return f"{title}\n\nFabrique-Task: {item['id']}\nFabrique-Sprint: {sprint['id']}\n"


def delivery_spec(
    engine: Engine, sprint: dict[str, Any], item: dict[str, Any]
) -> dict[str, Any] | None:
    """The ``git`` part of a ``workspace.generate`` request, or ``None`` without the workflow."""
    with Session(engine) as session:
        company = session.get(Company, sprint["company_id"])
        if company is None or not company.git_workflow:
            return None
        return {
            "branch": TASK_BRANCH.format(id=item["id"]),
            "base": company.git_base_branch or "",
            "message": commit_message(item, sprint),
        }


def record_reply(evidence: dict[str, Any], reply: Any) -> None:
    """Keep the daemon's Git answer on *evidence*, or why it could not write at all.

    With a branch, the tests run where the files are: the task's worktree.
    """
    if not isinstance(reply, dict):
        return
    git = reply.get("git")
    if isinstance(git, dict):
        evidence["git"] = git
        if reply.get("workspace_root"):
            evidence["workspace_root"] = reply["workspace_root"]
    if reply.get("error") and not reply.get("files"):
        # Not the agent's doing (a branch checked out elsewhere, a disk error):
        # the delivery is unverifiable, with the daemon's reason.
        evidence["workspace_writes"] = None
        evidence["reason"] = (
            f"the workspace daemon could not write the files: {reply['error']}"
        )
