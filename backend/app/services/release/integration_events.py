"""A sprint's integration on each task's own record (issue #324).

``services/integration.record`` keeps what the daemon did with the sprint's
task branches as a ``SprintIntegration``; here the part each task played in
it (merged, skipped because its branch was already in, stopped at its
conflict, its branch missing or refused) becomes that task's ``integration``
event, written by the System through ``task_events.record`` and rendered as
its board comment like every other transition. The task's status does not
change: an integration moves branches, not tasks. A task the request never
reached (``not_attempted``) gets no event.
"""

from __future__ import annotations

from typing import Any

from models import BacklogItem
from models.integration import SprintIntegration
from sqlmodel import Session

from services.tasks import task_events
from services.tasks.board import INTEGRATION_PREFIX

# The states a task's event is written for, and how its comment says it.
_SAID = {
    "merged": "merged into {branch} (merge commit {merge}).",
    "already_merged": "skipped: its branch was already in {branch}.",
    "conflict": "stopped at a conflict merging its branch into {branch}{files}.",
    "missing": "its branch {task_branch} was not found.",
    "refused": "its branch {task_branch} was refused.",
}


def _short(commit: Any) -> str:
    return str(commit or "")[:12] or "unknown"


def text_for(row: SprintIntegration, task: dict[str, Any]) -> str | None:
    """The comment for *task*'s part in the integration *row*, or ``None``."""
    template = _SAID.get(task.get("state"))
    if template is None:
        return None
    files = (row.conflict or {}).get("files") or []
    said = template.format(
        branch=row.branch,
        merge=_short(task.get("merge_commit")),
        task_branch=task.get("branch") or "",
        files=f" ({', '.join(str(f) for f in files[:10])})" if files else "",
    )
    return f"{INTEGRATION_PREFIX}{row.id} of sprint {row.sprint_id}: {said}"


def record_for_tasks(session: Session, row: SprintIntegration) -> list[int]:
    """Write the ``integration`` event of each task *row* reached; returns their ids.

    *row* must be flushed (its id names it). The caller commits.
    """
    written: list[int] = []
    for task in row.tasks or []:
        text = text_for(row, task)
        item = session.get(BacklogItem, task.get("task_id"))
        if text is None or item is None:
            continue
        task_events.record(
            session,
            item,
            "integration",
            task_events.SYSTEM,
            text=text,
            company_id=row.company_id,
            payload={
                "integration_id": row.id,
                "branch": row.branch,
                "state": task.get("state"),
                "source_commit": task.get("source_commit"),
                "merge_commit": task.get("merge_commit"),
                "integration_status": row.status,
            },
        )
        written.append(item.id)
    return written
