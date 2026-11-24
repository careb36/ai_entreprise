"""What the Live page says is happening in a sprint, from the records (issue #766).

The Live page derived "working" from the event stream (an ``agent_start``
until an ``agent_complete`` of the same agent, or 90 seconds) and its health
tiles from whatever events reached the browser, so a completed sprint could
show two agents working and an open rework. ``of_sprint`` answers from the
records instead; the stream only animates what it says.

- ``working``: a run in flight for an agent now. Only while the sprint runs
  (``active`` or ``needs_split``): an assignee's run of a claimed task or
  QA's review of a delivery in this process (``in_flight``), and any
  ``in_progress`` task whose lock a replica holds (``locks.TASK``, held for
  as long as a replica works on it).
- ``pause_kind``: why a paused sprint is paused, from the reason the code
  that paused it wrote (``pause_kind``).
- ``closed_at``: when a completed sprint closed, from the progress snapshot
  sealed at its close; events after it are shown as after the close.
- ``tiles``: each health tile, defined in ``docs/contexts/loop.md``.
"""

from __future__ import annotations

from datetime import datetime
from typing import Any

from models import BacklogItem, Sprint, TaskEvent
from models.progress import SprintProgressSnapshot
from sqlalchemy.engine import Engine
from sqlmodel import Session, func, select

from services.loop import daemon_gate, in_flight, platform_hold, preconditions
from services.platform import locks
from services.tasks import task_events, task_projection
from services.workspace import daemon_liveness

RUNNING = ("active", "needs_split")
CLOSED = ("completed", "cancelled")
# The task events that record an agent's failed call: QA's review call, or a
# transient model failure the worker queued again.
FAILURE_KINDS = ("review_failed", "retried")
# The actors that are not agents: the system, a gate and a person.
NOT_AGENTS = (task_events.SYSTEM, task_events.GATE, task_events.PERSON)

# Why a sprint is paused, by the start of the reason its pauser wrote
# (``sprint_control.credit_reason``, ``sprint_close.nothing_done_reason``,
# ``sprint_budget.reason_reached`` and ``reason_unknown``,
# ``preconditions.pause_reason``, ``daemon_gate.REASON``,
# ``platform_hold.reason``).
_PAUSE_PREFIXES = (
    ("No credit at ", "no_credit"),
    ("No task of this sprint is done", "nothing_done"),
    ("The sprint spent ", "budget"),
    ("The sprint has a ", "budget"),
    (preconditions.PAUSE_PREFIX, "acknowledgement"),
    (daemon_gate.REASON[:40], "daemon"),
    (platform_hold.REASON_PREFIX, "tests_not_found"),
)


def pause_kind(status: str | None, reason: str | None) -> str | None:
    """``person`` (a Manager paused it), ``no_credit``, ``nothing_done``,
    ``budget``, ``acknowledgement``, ``daemon``, ``tests_not_found`` or
    ``other``; ``None`` when
    the sprint is not paused."""
    if status != "paused":
        return None
    text = (reason or "").strip()
    if not text or text.startswith("Paused by "):
        return "person"
    for prefix, kind in _PAUSE_PREFIXES:
        if text.startswith(prefix):
            return kind
    return "other"


def _iso(value: datetime | None) -> str | None:
    return value.isoformat() if value is not None else None


def working(
    engine: Engine, sprint: Sprint, items: list[BacklogItem]
) -> list[dict[str, Any]]:
    """The agent runs in flight for *sprint* now; none unless it runs."""
    if sprint.status not in RUNNING:
        return []
    titles = {item.id: item.title for item in items}
    runs = [
        {**run, "task_title": titles.get(run["task_id"])}
        for run in in_flight.of_sprint(sprint.id)
        if run["task_id"] in titles
    ]
    seen = {(run["task_id"], run["kind"]) for run in runs}
    for item in items:
        if item.status != "in_progress" or (item.id, in_flight.TASK) in seen:
            continue
        if locks.is_held(engine, locks.TASK, item.id):
            runs.append(
                {
                    "sprint_id": sprint.id,
                    "task_id": item.id,
                    "task_title": item.title,
                    "role": item.assignee or "",
                    "kind": in_flight.TASK,
                    "since": None,
                }
            )
    return runs


def closed_at(session: Session, sprint: Sprint) -> str | None:
    """When a completed sprint closed: its latest sealed progress snapshot."""
    if sprint.status not in CLOSED:
        return None
    sealed = session.exec(
        select(func.max(SprintProgressSnapshot.created_at)).where(
            SprintProgressSnapshot.sprint_id == sprint.id
        )
    ).first()
    return _iso(sealed)


def _open_reworks(session: Session, items: list[BacklogItem]) -> int:
    """Tasks not done or discarded with a rework request no delivery answered."""
    records = task_projection.records_for(session, items)
    return sum(
        1
        for item in items
        if item.status not in ("done", "discarded")
        and records[item.id].pending_rework_feedback()
    )


def _previous_sprint(session: Session, sprint: Sprint) -> dict[str, Any] | None:
    """The company's latest completed sprint before *sprint*, with its tally."""
    previous = session.exec(
        select(Sprint)
        .where(
            Sprint.company_id == sprint.company_id,
            Sprint.id < sprint.id,
            Sprint.status == "completed",
        )
        .order_by(Sprint.id.desc())  # type: ignore[union-attr]
    ).first()
    if previous is None:
        return None
    statuses = session.exec(
        select(BacklogItem.status).where(BacklogItem.sprint_id == previous.id)
    ).all()
    return {
        "id": previous.id,
        "name": previous.sprint_name,
        "done": sum(1 for s in statuses if s == "done"),
        "total": len(statuses),
    }


def tiles(
    session: Session,
    sprint: Sprint,
    items: list[BacklogItem],
    runs: list[dict[str, Any]],
) -> dict[str, Any]:
    """The Live page's health tiles, each from a record (``docs/contexts/loop.md``)."""
    failures = session.exec(
        select(func.count()).where(
            TaskEvent.sprint_id == sprint.id,
            TaskEvent.event_type.in_(FAILURE_KINDS),  # type: ignore[attr-defined]
        )
    ).one()
    last_agent_event = session.exec(
        select(func.max(TaskEvent.created_at)).where(
            TaskEvent.sprint_id == sprint.id,
            TaskEvent.actor.not_in(NOT_AGENTS),  # type: ignore[attr-defined]
        )
    ).first()
    daemon = daemon_liveness.liveness()
    return {
        "active_agents": len({run["role"] for run in runs}),
        "agent_failures": int(failures or 0),
        "blocked_tasks": sum(1 for item in items if item.status == "blocked"),
        "open_reworks": _open_reworks(session, items),
        "last_agent_event_at": _iso(last_agent_event),
        "daemon": {
            "state": daemon["state"],
            "seconds_since_heartbeat": daemon["seconds_since_last"],
        },
        "previous_sprint": _previous_sprint(session, sprint),
    }


def of_sprint(session: Session, engine: Engine, sprint: Sprint) -> dict[str, Any]:
    """The Live page's state of *sprint*, read from the records now."""
    items = list(
        session.exec(
            select(BacklogItem).where(BacklogItem.sprint_id == sprint.id)
        ).all()
    )
    runs = working(engine, sprint, items)
    return {
        "sprint_id": sprint.id,
        "status": sprint.status,
        "running": sprint.status in RUNNING,
        "pause_reason": sprint.pause_reason,
        "pause_kind": pause_kind(sprint.status, sprint.pause_reason),
        "closed_at": closed_at(session, sprint),
        "working": runs,
        "tiles": tiles(session, sprint, items, runs),
    }
