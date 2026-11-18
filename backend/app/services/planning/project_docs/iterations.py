"""What was planned in each iteration and what left the factory (issue #530, phase 2).

The analysis behind the release and iteration plan (``release_docs``) and two
of the check results (``checks``). Every figure is counted from a record the
factory observed itself:

- the sprints, their tasks with the requirement ids each covers and their
  board status (``sprints``), the specification each was planned from, and
  the scope size and team source recorded when it started
  (``iteration_plans``);
- the requirements of the documented specifications (``specs``): a
  requirement is **delivered** when a task covering it is done on the board,
  **planned** when tasks cover it and none is done yet, and **not planned**
  when no task covers it;
- the carries the platform wrote between sprints (``carries``);
- the sprint integrations a Manager published as one request, with the
  daemon's commit (``releases``).

The goal and team of a sprint are the plan as recorded when it started, an
intent, not a statement of what happened; the board and the records above
say what happened. Nothing here reads a retrospective or an agent message.

Pure; no I/O.
"""

from __future__ import annotations

import re
from typing import Any

from services.planning.project_docs import specs
from services.planning.project_docs.scope_docs import covering

DELIVERED, PLANNED, NOT_PLANNED = "delivered", "planned", "not planned"
COMPLETED, CANCELLED = "completed", "cancelled"
ITERATION_INPUTS = (
    "specification",
    "sprints",
    "iteration_plans",
    "carries",
    "releases",
)
_REQUIREMENT = re.compile(r"^[A-Za-z]+-\d+")


def requirement_of(cover: Any) -> str:
    """The requirement id a cover names (``REQ-1.2`` -> ``REQ-1``)."""
    text = str(cover)
    found = _REQUIREMENT.match(text)
    return found.group(0) if found else text


def carried_requirements(sprint: dict[str, Any]) -> list[str]:
    """The requirement ids the sprint's tasks cover, in first-seen order."""
    seen: dict[str, None] = {}
    for task in sprint.get("tasks") or []:
        for cover in task.get("covers") or []:
            seen.setdefault(requirement_of(cover), None)
    return list(seen)


def plan_of(snapshot: dict[str, Any]) -> dict[Any, dict[str, Any]]:
    """The recorded scope size and team source per sprint id."""
    return {
        p.get("sprint_id"): p
        for p in snapshot.get("iteration_plans") or []
        if isinstance(p, dict)
    }


def carries(snapshot: dict[str, Any]) -> list[dict[str, Any]]:
    return [c for c in snapshot.get("carries") or [] if isinstance(c, dict)]


def releases(snapshot: dict[str, Any]) -> list[dict[str, Any]]:
    return [r for r in snapshot.get("releases") or [] if isinstance(r, dict)]


def requirement_rows(
    snapshot: dict[str, Any], spec: dict[str, Any]
) -> list[dict[str, Any]]:
    """Per requirement of *spec*: the (sprint, task) pairs that carry it and its state."""
    rows = []
    for req in spec.get("requirements") or []:
        req_id = str(req.get("id"))
        tasks = covering(snapshot, req_id, spec)
        if any(t.get("status") == "done" for _, t in tasks):
            state = DELIVERED
        elif tasks:
            state = PLANNED
        else:
            state = NOT_PLANNED
        rows.append({"id": req_id, "requirement": req, "tasks": tasks, "state": state})
    return rows


def totals(snapshot: dict[str, Any]) -> dict[str, Any]:
    """The plan's figures over every iteration and documented specification."""
    sprints = snapshot.get("sprints") or []
    completed = [s for s in sprints if s.get("status") == COMPLETED]
    released = {r.get("sprint_id") for r in releases(snapshot)}
    states = [
        row["state"]
        for spec in specs.documented(snapshot)
        for row in requirement_rows(snapshot, spec)
    ]
    return {
        "iterations": len(sprints),
        "completed": len(completed),
        "cancelled": sum(1 for s in sprints if s.get("status") == CANCELLED),
        "open": sum(
            1 for s in sprints if s.get("status") not in (COMPLETED, CANCELLED)
        ),
        "requirements": len(states),
        "planned": sum(1 for s in states if s != NOT_PLANNED),
        "delivered": sum(1 for s in states if s == DELIVERED),
        "carries": len(carries(snapshot)),
        "releases": len(releases(snapshot)),
        "completed_released": sum(1 for s in completed if s.get("id") in released),
    }
