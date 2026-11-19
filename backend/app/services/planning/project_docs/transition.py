"""What each release delivered and what it left as known errors (issue #696).

The analysis behind the ITIL transition set (``transition_docs``), over the
``transition`` snapshot key (``collect_transition``). Every figure is read
from an observation the platform made itself:

- a task is **delivered** by a release when the integration recorded it as
  merged or already in (``INTEGRATED``); the requirements a release delivers
  are those its delivered tasks cover (a criterion id read as its
  requirement, ``iterations.requirement_of``);
- a **known error** is either a delivery the release carries whose Verifier
  reading at integration was refuted or unverifiable, or read as high risk
  (``PROBLEM_VERDICTS``, tier ``high``); or a hold open in the release's
  sprint when it was released: the newest ``blocked`` or ``risk_hold``
  event of a task still blocked then, or an ``escalated`` event with no later
  ``decided`` one, on a task the release does not deliver and the platform
  had not carried to another sprint by then.

Nothing here reads a review's words, a retrospective or an agent message:
those are claims.

Pure; no I/O.
"""

from __future__ import annotations

from typing import Any

from services.planning.project_docs import iterations
from services.planning.project_docs.collect_transition import KEY, moment

INTEGRATED = ("merged", "already_merged")
PROBLEM_VERDICTS = ("refuted", "unverifiable")
HOLD_KINDS = ("blocked", "risk_hold", "escalated")
READING, HOLD = "reading", "hold"


def releases(snapshot: dict[str, Any]) -> list[dict[str, Any]]:
    return [r for r in snapshot.get(KEY) or [] if isinstance(r, dict)]


def delivered(release: dict[str, Any]) -> list[dict[str, Any]]:
    """The tasks the release delivered, in the order the integration recorded them."""
    return [t for t in release.get("tasks") or [] if t.get("state") in INTEGRATED]


def left_out(release: dict[str, Any]) -> list[dict[str, Any]]:
    return [t for t in release.get("tasks") or [] if t.get("state") not in INTEGRATED]


def requirements(release: dict[str, Any]) -> list[str]:
    """The requirements the release's delivered tasks cover, sorted."""
    return sorted(
        {
            iterations.requirement_of(c)
            for t in delivered(release)
            for c in t.get("covers") or []
        }
    )


def _problem(reading: dict[str, Any]) -> str | None:
    if reading.get("verdict") in PROBLEM_VERDICTS:
        return str(reading["verdict"])
    return "high_risk" if reading.get("tier") == "high" else None


def problem_readings(release: dict[str, Any]) -> list[dict[str, Any]]:
    """The delivered tasks' readings at integration that were not clean."""
    shipped = {t.get("task_id") for t in delivered(release)}
    return [
        {"reading": r, "problem": p}
        for r in release.get("readings") or []
        if r.get("task_id") in shipped and (p := _problem(r))
    ]


def open_holds(release: dict[str, Any]) -> list[dict[str, Any]]:
    """The holds open in the release's sprint when it was released, in event order."""
    until = release.get("released_at")
    events = [
        e
        for e in release.get("hold_events") or []
        if until is None or moment(e.get("created_at")) <= moment(until)
    ]
    shipped = {t.get("task_id") for t in delivered(release)}
    carried = {e.get("task_id") for e in events if e.get("kind") == "carried_over"}
    newest: dict[tuple[Any, str], dict[str, Any]] = {}
    for event in events:
        kind, task_id = event.get("kind"), event.get("task_id")
        if kind not in HOLD_KINDS or task_id in shipped or task_id in carried:
            continue
        if kind == "escalated":
            decided = any(
                e.get("kind") == "decided"
                and e.get("task_id") == task_id
                and (e.get("id") or 0) > (event.get("id") or 0)
                for e in events
            )
            if decided:
                newest.pop((task_id, kind), None)
                continue
        elif event.get("task_status_then") != "blocked":
            newest.pop((task_id, kind), None)
            continue
        newest[(task_id, kind)] = event
    return sorted(newest.values(), key=lambda e: e.get("id") or 0)


def known_errors(release: dict[str, Any]) -> list[dict[str, Any]]:
    """The release's known errors: problem readings first, then open holds."""
    return [
        *({"kind": READING, **p} for p in problem_readings(release)),
        *({"kind": HOLD, "event": e} for e in open_holds(release)),
    ]
