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

A new branch starts from the base as its remote has it when the local copy
is behind (``vscode-daemon/git_base.py``: ``base_commit``,
``base_behind_remote``, ``remote_unreachable`` on the ``git`` evidence). A
folder that is not a repository is delivered without a branch, with a
snapshot, and the evidence says why (``git_workflow_skipped``).

The base is fetched only when the company's forge policy allows it
(``services/platform/forge_policy_setting.py``, sent as ``forge_policy``):
with ``none``, the daemon contacts no remote and the evidence says so
(``remote_contacted: false``).
"""

from __future__ import annotations

import re
from typing import Any

from config import settings
from models.company import Company
from models.sprint import BacklogItem
from sqlalchemy.engine import Engine
from sqlmodel import Session, select

from services.contracts.task_dependencies import depends_on_of
from services.platform import forge_policy_setting

TASK_BRANCH = "fabrique/task-{id}"
# A delivery with a git spec may first fetch the base from its remote, bounded
# by the daemon's ``git_base.FETCH_TIMEOUT_SECONDS`` (10). The request waits
# that long on top of the plain write budget, so a stalled fetch that falls
# back to the local base still answers before the backend gives up and queues
# the delivery a second time. Keep it above the daemon's bound.
FETCH_ALLOWANCE_SECONDS = 15.0
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


def task_spec(engine: Engine, company_id: int, item_id: int) -> dict[str, Any] | None:
    """The task's branch, base, forge policy and done prerequisites' branches,
    or ``None`` without the workflow: what ``workspace.console.prepare`` makes
    the task's worktree from (``services/tools/task_worktree.py``)."""
    with Session(engine) as session:
        company = session.get(Company, company_id)
        if company is None or not company.git_workflow:
            return None
        spec = {
            "branch": TASK_BRANCH.format(id=item_id),
            "base": company.git_base_branch or "",
            # Whether the daemon may fetch the base from its remote (issue #727).
            "forge_policy": forge_policy_setting.effective(session, company.id),
        }
        # The branches of the tasks this one depends on, done: the daemon
        # merges them into the task's worktree, so it builds on their files.
        row = session.get(BacklogItem, item_id)
        deps = depends_on_of(row) if row is not None else []
        if deps:
            done = session.exec(
                select(BacklogItem.id).where(
                    BacklogItem.id.in_(deps), BacklogItem.status == "done"
                )
            ).all()
            spec["merge"] = [TASK_BRANCH.format(id=d) for d in deps if d in set(done)]
        return spec


def delivery_spec(
    engine: Engine, sprint: dict[str, Any], item: dict[str, Any]
) -> dict[str, Any] | None:
    """The ``git`` part of a ``workspace.generate`` request, or ``None`` without the workflow."""
    spec = task_spec(engine, sprint["company_id"], item["id"])
    if spec is not None:
        spec["message"] = commit_message(item, sprint)
    return spec


def record_reply(evidence: dict[str, Any], reply: Any) -> None:
    """Keep the daemon's Git answer on *evidence*, or why it could not write at all.

    With a branch, the tests run where the files are: the task's worktree.
    Without one, the daemon's snapshot that undoes the delivery
    (``services/release/delivery_undo.py``), or why it kept none, is kept too, and
    ``git_workflow_skipped`` when the workflow was on but the daemon found
    the folder is not a repository and delivered without a branch.
    """
    if not isinstance(reply, dict):
        return
    for key in ("snapshot", "snapshot_error", "git_workflow_skipped"):
        if reply.get(key):
            evidence[key] = reply[key]
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


def delivery_timeout(spec: dict[str, Any] | None) -> float:
    """How long the backend waits for ``workspace.generate``: longer when the daemon may fetch the base."""
    base = settings.WORKSPACE_WRITE_TIMEOUT_SECONDS
    return base + (FETCH_ALLOWANCE_SECONDS if spec else 0.0)


def skipped_note(evidence: dict[str, Any] | None) -> str:
    """The board's words when the daemon delivered without the Git workflow it was asked for.

    The undo snapshot is promised only when the evidence holds one; otherwise
    the note says what the daemon recorded instead (``snapshot_error``).
    """
    evidence = evidence or {}
    reason = evidence.get("git_workflow_skipped")
    if not reason:
        return ""
    note = f" — delivered without the Git workflow ({reason}): no task branch, the files are in the folder"
    if evidence.get("snapshot"):
        return note + " with a snapshot to undo them"
    failure = str(
        evidence.get("snapshot_error")
        or "no snapshot was recorded, this delivery cannot be undone"
    )
    return f"{note}; {failure[:300]}"
