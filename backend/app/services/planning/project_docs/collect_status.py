"""Reading the records the status report counts into the snapshot (``collect``).

Two snapshot keys, both observations the backend made itself:

- ``effort``: per task of the company, the latest ``effort_observed`` event
  (``services/models/effort.py``) written by the system (actor ``system``,
  ``observed_by`` ``backend``), with its chain hash. An event of that kind
  under any other actor or observer is an account of the work, a claim, and
  is not read (``docs/POSITIONING.md``).
- ``costs``: per sprint of the snapshot, the model usage the LLM proxy
  reported and its price (``services/models/run_cost.sprint_costs``): turns,
  reported tokens and cents, or ``None`` with the reason; the model names are
  left out.

Plain data only, bounded like the rest of the snapshot.
"""

from __future__ import annotations

from typing import Any

from models import TaskEvent
from sqlmodel import Session, select

from services.models import effort as effort_record
from services.models import run_cost
from services.tasks import task_events

LIMIT = 200
OBSERVED_FIELDS = (
    "status",
    "done",
    "tokens",
    "wall_seconds",
    "deliveries",
    "rework_cycles",
    "rework_requests",
    "send_backs",
    "review_failures",
)
COST_FIELDS = (
    "turns",
    "reported_turns",
    "estimated_turns",
    "unavailable_turns",
    "reported_tokens",
    "cost_cents",
    "reason",
)


def effort(session: Session, company_id: int) -> list[dict[str, Any]]:
    """The backend's latest effort observation per task, oldest task first."""
    rows = session.exec(
        select(TaskEvent)
        .where(
            TaskEvent.company_id == company_id,
            TaskEvent.event_type == effort_record.KIND,
            TaskEvent.actor == task_events.SYSTEM,
        )
        .order_by(TaskEvent.id.desc())
        .limit(LIMIT * 4)
    ).all()
    latest: dict[int, dict[str, Any]] = {}
    for event in rows:
        payload = event.payload if isinstance(event.payload, dict) else {}
        observed = payload.get("observed")
        if payload.get("observed_by") != "backend" or not isinstance(observed, dict):
            continue
        if event.task_id in latest:
            continue
        latest[event.task_id] = {
            "event_id": event.id,
            "task_id": event.task_id,
            "sprint_id": payload.get("sprint_id") or event.sprint_id,
            "chain_hash": event.chain_hash,
            "created_at": event.created_at.isoformat() if event.created_at else None,
            "observed": {k: observed.get(k) for k in OBSERVED_FIELDS if k in observed},
        }
    return sorted(latest.values(), key=lambda e: e["task_id"])[-LIMIT:]


def costs(
    session: Session, company_id: int, sprint_ids: list[int]
) -> list[dict[str, Any]]:
    """Each sprint's reported model usage and its price, in sprint order."""
    found = run_cost.sprint_costs(session, company_id, sprint_ids)
    return [
        {"sprint_id": sid, **{k: found[sid].get(k) for k in COST_FIELDS}}
        for sid in sorted(found)
    ]
