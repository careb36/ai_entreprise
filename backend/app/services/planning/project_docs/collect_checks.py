"""Reading what the domain checks read into the snapshot (``collect``; issue #692).

One snapshot key, ``check_basis``, records the platform wrote itself:

- ``sprint_lengths``: each snapshot sprint's planned length in days
  (``Sprint.duration_days``, which a Manager edits, audited as
  ``sprint_updated``); ``None`` when none is recorded.
- ``intake_tasks``: the tasks that joined a sprint after it was planned,
  accepted from an intake request (``services/planning/intake_queue``): the
  ``note`` task event the platform writes then, under the actor ``system``,
  with its chain hash. The same payload under any other actor is that
  party's account, a claim, and is not read (``docs/POSITIONING.md``).
- ``quotas``: each business unit's token quota and the tokens counted against
  it this period (``BusinessUnit``; the LLM proxy adds the usage the provider
  reported). The unit's name is free text and is left out.

Plain data only, bounded like the rest of the snapshot.
"""

from __future__ import annotations

from typing import Any

from models import BusinessUnit, Sprint, TaskEvent
from sqlmodel import Session, select

from services.planning.project_docs.refs import digest
from services.tasks import task_events

KEY = "check_basis"
LIMIT = 200
# The payload field ``intake_queue`` writes on the task it creates.
INTAKE_FIELD = "intake_item_id"


def _lengths(session: Session, sprint_ids: list[int]) -> list[dict[str, Any]]:
    if not sprint_ids:
        return []
    rows = session.exec(
        select(Sprint.id, Sprint.duration_days).where(Sprint.id.in_(sprint_ids))
    ).all()
    return [
        {"sprint_id": sid, "duration_days": days}
        for sid, days in sorted(rows, key=lambda r: r[0])
    ]


def _intake_tasks(
    session: Session, company_id: int, task_ids: list[int]
) -> list[dict[str, Any]]:
    if not task_ids:
        return []
    rows = session.exec(
        select(TaskEvent)
        .where(
            TaskEvent.company_id == company_id,
            TaskEvent.task_id.in_(task_ids),
            TaskEvent.event_type == "note",
            TaskEvent.actor == task_events.SYSTEM,
        )
        .order_by(TaskEvent.id)
    ).all()
    found: dict[int, dict[str, Any]] = {}
    for event in rows:
        payload = event.payload if isinstance(event.payload, dict) else {}
        if payload.get(INTAKE_FIELD) is None or event.task_id in found:
            continue
        found[event.task_id] = {
            "task_id": event.task_id,
            "sprint_id": event.sprint_id,
            "event_id": event.id,
            "chain_hash": event.chain_hash,
            "created_at": event.created_at.isoformat() if event.created_at else None,
        }
    return sorted(found.values(), key=lambda e: e["task_id"])[-LIMIT:]


def _quotas(session: Session, company_id: int) -> list[dict[str, Any]]:
    rows = session.exec(
        select(BusinessUnit)
        .where(BusinessUnit.company_id == company_id)
        .order_by(BusinessUnit.id)
        .limit(LIMIT)
    ).all()
    out = []
    for unit in rows:
        entry = {
            "unit_id": unit.id,
            "limit": int(unit.token_quota_limit or 0),
            "used": int(unit.token_quota_used or 0),
        }
        out.append({**entry, "sha256": digest(entry)})
    return out


def basis(
    session: Session, company_id: int, sprints: list[dict[str, Any]]
) -> dict[str, Any]:
    """The records the domain checks read for the snapshot's *sprints*."""
    task_ids = [t["id"] for s in sprints for t in s.get("tasks") or []]
    return {
        "sprint_lengths": _lengths(session, [s["id"] for s in sprints]),
        "intake_tasks": _intake_tasks(session, company_id, task_ids),
        "quotas": _quotas(session, company_id),
    }
