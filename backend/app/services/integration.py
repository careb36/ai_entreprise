"""Integration of a sprint's task branches into one branch.

With the Git workflow on, each task is a branch (``fabrique/task-<id>``) and
nobody joins them. A sprint is not delivered until its work is integrated
and the whole suite passes on the integrated result. This module plans that
step: which branches, in which order, and whether the dependencies are met.
The actual merge and test run happen on the daemon (real git); here we only
describe the plan and record its result, so the backend never shells out.
"""

from __future__ import annotations

from typing import Any

from models import BacklogItem, Sprint
from sqlmodel import Session, select

BRANCH_PREFIX = "fabrique/task-"
INTEGRATION_BRANCH_PREFIX = "fabrique/sprint-"


def task_branch(item: BacklogItem) -> str:
    return f"{BRANCH_PREFIX}{item.id}"


def integration_branch(sprint: Sprint) -> str:
    return f"{INTEGRATION_BRANCH_PREFIX}{sprint.id}"


def _topological(items: list[BacklogItem]) -> list[BacklogItem]:
    by_id = {i.id: i for i in items}
    done = set()
    result: list[BacklogItem] = []
    remaining = list(items)
    # Repeatedly take items whose dependencies are already placed.
    progress = True
    while remaining and progress:
        progress = False
        still: list[BacklogItem] = []
        for item in remaining:
            deps = []
            for d in item.depends_on or []:
                try:
                    deps.append(int(d))
                except (TypeError, ValueError):
                    continue
            if all(d in done or d not in by_id for d in deps):
                result.append(item)
                done.add(item.id)
                progress = True
            else:
                still.append(item)
        remaining = still
    # Anything left (cycle) goes last, in id order.
    result.extend(sorted(remaining, key=lambda i: i.id or 0))
    return result


def plan(session: Session, sprint_id: int) -> dict[str, Any]:
    sprint = session.get(Sprint, sprint_id)
    if sprint is None:
        raise ValueError("sprint not found")
    items = session.exec(
        select(BacklogItem).where(BacklogItem.sprint_id == sprint_id)
    ).all()
    ordered = _topological(list(items))
    branches = [
        {
            "task_id": i.id,
            "title": i.title,
            "status": i.status,
            "branch": task_branch(i),
        }
        for i in ordered
    ]
    by_id = {i.id: i for i in items}
    unmet = []
    for i in items:
        for d in i.depends_on or []:
            try:
                did = int(d)
            except (TypeError, ValueError):
                continue
            if did not in by_id:
                unmet.append({"task_id": i.id, "missing_dependency": did})
    return {
        "sprint_id": sprint.id,
        "sprint_name": sprint.sprint_name,
        "integration_branch": integration_branch(sprint),
        "task_branches": branches,
        "merge_order": [i.id for i in ordered],
        "unmet_dependencies": unmet,
    }
