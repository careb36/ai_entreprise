"""What a project delivered and what it leaves open at closure (issue #530, phase 2).

The analysis behind the final report (``final_docs``) and one of the check
results (``checks``). Every figure is counted from a record the factory
observed itself:

- the requirements of the documented specifications (``specs``) and the
  tasks that cover them (``sprints``): a requirement is **delivered** when a
  covering task is done on the board, else **not delivered**, either
  **planned** (tasks cover it, none is done) or **not planned** (no task
  covers it); the Verifier's latest reading of each done task
  (``verifications``) is shown beside it;
- the holds left open: a ``blocked`` or ``risk_hold`` event whose task is
  still blocked, or an ``escalated`` event with no later ``decided`` one,
  when the task is neither done nor carried to a later iteration by the
  platform (``events``, ``carries``); the newest event per task and kind;
- the risks left open: a high-risk reading by the Verifier of a task that is
  neither done nor carried (``verifications``);
- the iterations not closed yet (``sprints``), which make the report interim.

Nothing here reads a retrospective, a procedure or an agent message: those
are claims, and the lessons learned register quotes them as such.

Pure; no I/O.
"""

from __future__ import annotations

from typing import Any

from services.planning.project_docs import iterations, specs

DELIVERED, NOT_DELIVERED = "delivered", "not delivered"
PLANNED, NOT_PLANNED = iterations.PLANNED, iterations.NOT_PLANNED
CLOSED = (iterations.COMPLETED, iterations.CANCELLED)
HOLD_KINDS = ("blocked", "escalated", "risk_hold")
CLOSURE_INPUTS = (
    "specification",
    "sprints",
    "events",
    "verifications",
    "carries",
    "releases",
)


def open_iterations(snapshot: dict[str, Any]) -> list[dict[str, Any]]:
    """The sprints neither completed nor cancelled."""
    return [s for s in snapshot.get("sprints") or [] if s.get("status") not in CLOSED]


def _status_of(snapshot: dict[str, Any]) -> dict[Any, str]:
    return {t.get("id"): str(t.get("status")) for _, t in specs.tasks_of(snapshot)}


def _carried(snapshot: dict[str, Any]) -> set[Any]:
    """The tasks the platform carried to a later iteration."""
    return {c.get("from_task_id") for c in iterations.carries(snapshot)}


def latest_readings(snapshot: dict[str, Any]) -> dict[Any, dict[str, Any]]:
    """The Verifier's newest reading per task (the snapshot is oldest first)."""
    out: dict[Any, dict[str, Any]] = {}
    for item in snapshot.get("verifications") or []:
        if isinstance(item, dict):
            out[item.get("task_id")] = item
    return out


def requirement_outcomes(
    snapshot: dict[str, Any], spec: dict[str, Any]
) -> list[dict[str, Any]]:
    """Per requirement of *spec*: delivered or not, why, the done tasks and their readings."""
    readings = latest_readings(snapshot)
    out = []
    for row in iterations.requirement_rows(snapshot, spec):
        done = [(s, t) for s, t in row["tasks"] if t.get("status") == "done"]
        delivered = row["state"] == iterations.DELIVERED
        out.append(
            {
                "id": row["id"],
                "requirement": row["requirement"],
                "outcome": DELIVERED if delivered else NOT_DELIVERED,
                "why": None if delivered else row["state"],
                "done": done,
                "readings": [
                    readings[t.get("id")] for _, t in done if t.get("id") in readings
                ],
            }
        )
    return out


def _decided_after(events: list[dict[str, Any]], event: dict[str, Any]) -> bool:
    return any(
        e.get("kind") == "decided"
        and e.get("task_id") == event.get("task_id")
        and (e.get("id") or 0) > (event.get("id") or 0)
        for e in events
    )


def open_holds(snapshot: dict[str, Any]) -> list[dict[str, Any]]:
    """The holds left open, the newest event per task and kind, in event order."""
    status_of, carried = _status_of(snapshot), _carried(snapshot)
    events = [e for e in snapshot.get("events") or [] if isinstance(e, dict)]
    newest: dict[tuple[Any, str], dict[str, Any]] = {}
    for event in events:
        kind, task_id = event.get("kind"), event.get("task_id")
        status = status_of.get(task_id, "unknown")
        if kind not in HOLD_KINDS or task_id in carried or status == "done":
            continue
        if kind == "escalated":
            if _decided_after(events, event):
                newest.pop((task_id, kind), None)
                continue
        elif status != "blocked":
            continue
        newest[(task_id, kind)] = event
    held = sorted(newest.values(), key=lambda e: e.get("id") or 0)
    return [
        {"event": e, "status": status_of.get(e.get("task_id"), "unknown")} for e in held
    ]


def open_risks(snapshot: dict[str, Any]) -> list[dict[str, Any]]:
    """The Verifier's newest high-risk reading of each task neither done nor carried."""
    status_of, carried = _status_of(snapshot), _carried(snapshot)
    return [
        item
        for task_id, item in latest_readings(snapshot).items()
        if item.get("tier") == "high"
        and task_id not in carried
        and status_of.get(task_id) not in (None, "done")
    ]


def totals(snapshot: dict[str, Any]) -> dict[str, Any]:
    """The closure figures over every documented specification and iteration."""
    outcomes = [
        row["outcome"]
        for spec in specs.documented(snapshot)
        for row in requirement_outcomes(snapshot, spec)
    ]
    sprints = snapshot.get("sprints") or []
    return {
        "requirements": len(outcomes),
        "delivered": sum(1 for o in outcomes if o == DELIVERED),
        "iterations": len(sprints),
        "closed": sum(1 for s in sprints if s.get("status") in CLOSED),
        "open_iterations": len(open_iterations(snapshot)),
        "open_holds": len(open_holds(snapshot)),
        "open_risks": len(open_risks(snapshot)),
        "releases": len(iterations.releases(snapshot)),
    }
