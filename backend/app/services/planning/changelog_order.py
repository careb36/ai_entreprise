"""A sprint's changelog task comes last: it describes the sprint's diff.

In sprint 8 (2026-11-24, issue #701) the fragment was written from the plan's
task titles rather than the diff, and claimed a flag the sprint never added.
A task that writes the changelog can only describe what the sprint changed
when every other task of the sprint is done and its branch merged into the
changelog task's worktree (``workspace/git_workflow.task_spec`` merges the
done prerequisites' branches). So, whatever the PO wrote, ``last`` makes each
changelog task depend on every other task of the plan and removes any edge
from another task to it (which would otherwise close a cycle). Deterministic:
applied after planning, in ``sprint_planner.with_dependencies``.

A changelog task is one whose title or description names the changelog
(``changelog``, ``changelog.d/``, "changelog fragment").

Pure: no database, no I/O.
"""

from __future__ import annotations

import re
from typing import Any

_CHANGELOG = re.compile(r"changelog", re.IGNORECASE)


def is_changelog_task(task: dict[str, Any]) -> bool:
    """The task's title or description names the changelog."""
    text = f"{task.get('title') or ''}\n{task.get('description') or ''}"
    return bool(_CHANGELOG.search(text))


def last(tasks: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """*tasks* (with ``depends_on`` as positions) with each changelog task
    depending on every other task, and no other task depending on one."""
    changelog = {i for i, t in enumerate(tasks) if is_changelog_task(t)}
    if not changelog or len(changelog) == len(tasks):
        return tasks
    others = [i for i in range(len(tasks)) if i not in changelog]
    for i, task in enumerate(tasks):
        if i in changelog:
            task["depends_on"] = list(others)
        else:
            deps = task.get("depends_on") or []
            task["depends_on"] = [d for d in deps if d not in changelog]
    return tasks
