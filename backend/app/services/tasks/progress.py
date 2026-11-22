"""A sprint's progress as a time series built from its task events (issue #531).

``build`` replays a sprint's events (``services/tasks/task_events.py``) in the
order they happened and counts, after each day (or each event), the tasks by
status and, apart, by the latest verdict the Verifier gave on their delivery
(``verified``, ``refuted``, ``unverifiable``, or ``none`` before any). Two
lines are kept and never merged:

- ``claimed``: the tasks with at least one ``delivered`` event, the producer's
  claim that the work is done;
- ``verified``: the tasks whose latest ``verified`` event carries the verdict
  ``verified``, the Verifier's reading of what was observed.

``gap`` is the number of tasks claimed and not verified: the distance between
what the agents said and what the evidence showed. A claim and an
observation never share a field (``docs/POSITIONING.md``).

Pure: no session, no clock, no I/O. The same events give the same bytes
(``canonical_bytes``), whatever order they are passed in, so a snapshot can
be rebuilt and compared. Nothing is read from a task's mutable fields (its
current status, its ``updated_at``): history comes from the events only,
unlike Plane's burndown, which rebuilds it from ``completed_at`` (design
studied from ``makeplane/plane`` ``utils/analytics_plot.py`` ``burndown_plot``
and ``cycle_transfer_issues.py`` ``progress_snapshot``; no code taken).

``coverage`` reads the per-task final state the same function produced and
says, per requirement of a specification, how many of the tasks covering it
were claimed, done and verified, or that none covers it.
"""

from __future__ import annotations

import json
from collections.abc import Iterable, Mapping
from datetime import datetime
from typing import Any

SERIES_VERSION = 1
STATUSES = ("todo", "in_progress", "in_review", "blocked", "done", "discarded")
VERDICTS = ("verified", "refuted", "unverifiable")
NONE = "none"
# The key a ``verified`` event's payload carries the Verifier's verdict under.
VERIFICATION_KEY = "verification"
GRANULARITIES = ("day", "event")
# Coverage states, from the strongest.
COVERAGE_STATES = ("verified", "done", "claimed", "open", "uncovered")


def _get(event: Any, name: str, default: Any = None) -> Any:
    if isinstance(event, Mapping):
        return event.get(name, default)
    return getattr(event, name, default)


def _stamp(value: Any) -> str:
    if isinstance(value, datetime):
        return value.replace(tzinfo=None).isoformat()
    return str(value or "")


def verdict_of(payload: Any) -> str | None:
    """The verdict a ``verified`` event's payload carries: ``{"id", "verdict"}``
    under ``VERIFICATION_KEY`` (the shape board_writes records), or the bare
    verdict an earlier event carried."""
    if not isinstance(payload, Mapping):
        return None
    value = payload.get(VERIFICATION_KEY)
    if isinstance(value, Mapping):
        value = value.get("verdict")
    return value if value in VERDICTS else None


def normalise(event: Any) -> dict[str, Any]:
    """The fields ``build`` reads from one event (a row or an ``as_dict`` dict)."""
    payload = _get(event, "payload") or {}
    verdict = verdict_of(payload)
    if verdict is None and isinstance(event, Mapping):
        # Already normalised (``progress_snapshot.normalised``).
        verdict = event.get("verification")
    return {
        "id": int(_get(event, "id") or 0),
        "task_id": _get(event, "task_id"),
        "kind": _get(event, "kind") or _get(event, "event_type"),
        "created_at": _stamp(_get(event, "created_at")),
        "task_status": _get(event, "task_status"),
        "verification": verdict if verdict in VERDICTS else None,
    }


def _empty_counts(keys: Iterable[str]) -> dict[str, int]:
    return {key: 0 for key in keys}


def _counts(states: dict[int, dict[str, Any]]) -> dict[str, Any]:
    status = _empty_counts(STATUSES)
    verification = _empty_counts((*VERDICTS, NONE))
    claimed = verified = gap = 0
    for state in states.values():
        status[state["status"]] = status.get(state["status"], 0) + 1
        verification[state["verification"] or NONE] += 1
        is_verified = state["verification"] == "verified"
        claimed += state["claimed"]
        verified += is_verified
        gap += state["claimed"] and not is_verified
    return {
        "status": status,
        "verification": verification,
        "claimed": claimed,
        "verified": verified,
        "gap": gap,
    }


def build(
    events: Iterable[Any],
    task_ids: Iterable[int] = (),
    *,
    sprint_id: int | None = None,
    granularity: str = "day",
) -> dict[str, Any]:
    """The sprint's series: one point per day (or per event), its final counts
    and each task's final state.

    *task_ids* names the sprint's tasks, so a task with no event yet counts
    as ``todo`` with no verdict; a task named only by its events counts too.
    A ``verified`` event without a readable verdict leaves the task's verdict
    as it was. Events of the sprint's own chain (no task) are skipped.
    """
    if granularity not in GRANULARITIES:
        raise ValueError(f"unknown granularity {granularity!r}")
    rows = sorted(
        (normalise(e) for e in events if _get(e, "task_id") is not None),
        key=lambda e: (e["created_at"], e["id"]),
    )
    states: dict[int, dict[str, Any]] = {
        int(task_id): {"status": "todo", "claimed": False, "verification": None}
        for task_id in {*task_ids, *(e["task_id"] for e in rows)}
    }
    points: list[dict[str, Any]] = []

    def point(at: str, seen: int, event_id: int | None) -> dict[str, Any]:
        entry = {"at": at, "events": seen, **_counts(states)}
        if event_id is not None:
            entry["event_id"] = event_id
        return entry

    for seen, event in enumerate(rows, start=1):
        state = states[int(event["task_id"])]
        if event["task_status"]:
            state["status"] = str(event["task_status"])
        if event["kind"] == "delivered":
            state["claimed"] = True
        elif event["kind"] == "verified" and event["verification"]:
            state["verification"] = event["verification"]
        if granularity == "event":
            points.append(point(event["created_at"], seen, event["id"]))
            continue
        day = event["created_at"][:10]
        if points and points[-1]["at"] == day:
            points[-1] = point(day, seen, None)
        else:
            points.append(point(day, seen, None))
    return {
        "v": SERIES_VERSION,
        "sprint_id": sprint_id,
        "granularity": granularity,
        "tasks": len(states),
        "events": len(rows),
        "points": points,
        "final": _counts(states),
        "by_task": [
            {
                "task_id": task_id,
                "status": state["status"],
                "claimed": state["claimed"],
                "verification": state["verification"],
            }
            for task_id, state in sorted(states.items())
        ],
    }


def canonical_bytes(value: Any) -> bytes:
    """The one encoding a series (or a snapshot) is hashed and compared in."""
    return json.dumps(
        value, sort_keys=True, separators=(",", ":"), ensure_ascii=False
    ).encode("utf-8")


def requirement_of(cover: str, requirement_ids: Iterable[str]) -> str | None:
    """The requirement a criterion id names: ``REQ-1.2`` and ``REQ-1`` name ``REQ-1``."""
    for requirement in requirement_ids:
        if cover == requirement or cover.startswith(f"{requirement}."):
            return requirement
    return None


def coverage(
    requirement_ids: Iterable[str],
    covers_by_task: Mapping[int, Iterable[str]],
    by_task: Iterable[Mapping[str, Any]],
) -> dict[str, Any]:
    """Per requirement, the tasks covering it and how many were claimed, done
    and verified, from the per-task states ``build`` returned (``by_task``,
    from one or several sprints).

    ``state`` is ``verified`` when a covering task is done and its latest
    verdict is ``verified``, ``done`` when one is done, ``claimed`` when one
    delivered, ``open`` otherwise, and ``uncovered`` with no task.
    """
    requirements = list(dict.fromkeys(str(r) for r in requirement_ids))
    states = {int(s["task_id"]): s for s in by_task}
    tasks_of: dict[str, list[int]] = {r: [] for r in requirements}
    for task_id, covers in sorted(covers_by_task.items()):
        if int(task_id) not in states:
            continue
        named = {requirement_of(str(c), requirements) for c in covers or []}
        for requirement in sorted(r for r in named if r):
            tasks_of[requirement].append(int(task_id))
    rows = []
    for requirement in requirements:
        covering = [states[t] for t in tasks_of[requirement]]
        done = [s for s in covering if s["status"] == "done"]
        verified = [s for s in covering if s["verification"] == "verified"]
        claimed = [s for s in covering if s["claimed"]]
        if not covering:
            state = "uncovered"
        elif any(s["verification"] == "verified" for s in done):
            state = "verified"
        elif done:
            state = "done"
        elif claimed:
            state = "claimed"
        else:
            state = "open"
        rows.append(
            {
                "requirement": requirement,
                "tasks": tasks_of[requirement],
                "claimed": len(claimed),
                "done": len(done),
                "verified": len(verified),
                "state": state,
            }
        )
    summary = _empty_counts(COVERAGE_STATES)
    for row in rows:
        summary[row["state"]] += 1
    return {"requirements": rows, "summary": summary}
