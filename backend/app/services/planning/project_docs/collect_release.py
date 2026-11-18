"""Reading the records the release and iteration plan reads into the snapshot (``collect``).

Three snapshot keys, each an observation the platform made itself:

- ``iteration_plans``: per sprint of the snapshot, the scope size and who
  chose its team (``Sprint.scope_size``, ``team_source``), as recorded when
  the sprint started. The sprint's goal, team and tasks are already in
  ``sprints``.
- ``carries``: each ``carried_over`` task event leaving a sprint (direction
  ``to``) that the platform wrote (actor ``system``,
  ``services/loop/carry_over.py``), with the task and sprint it went to and
  its chain hash. An event of that kind under any other actor is an account
  of a carry, a claim, and is not read (``docs/POSITIONING.md``).
- ``releases``: each sprint integration a Manager published as one request
  (``SprintIntegration.publication``, ``services/release/integration_publish``):
  the daemon's commit, how it was published and the audit row the backend
  wrote for it, with that row's time and chain hash. The name of the person
  who published it and the request's address are left out.

Plain data only, bounded like the rest of the snapshot.
"""

from __future__ import annotations

from datetime import datetime
from typing import Any

from models import AuditLog, Sprint, SprintIntegration, TaskEvent
from sqlmodel import Session, select

from services.tasks import task_events

LIMIT = 200
CARRY_KIND = "carried_over"
LEAVING = "to"


def _iso(value: datetime | None) -> str | None:
    return value.isoformat() if value else None


def _int(value: Any) -> int | None:
    return value if isinstance(value, int) and not isinstance(value, bool) else None


def iteration_plans(session: Session, sprint_ids: list[int]) -> list[dict[str, Any]]:
    """Each sprint's recorded scope size and the source of its team, in sprint order."""
    if not sprint_ids:
        return []
    rows = session.exec(select(Sprint).where(Sprint.id.in_(sprint_ids))).all()
    return [
        {
            "sprint_id": s.id,
            "scope_size": s.scope_size,
            "team_source": s.team_source,
        }
        for s in sorted(rows, key=lambda s: s.id)
    ]


def carries(session: Session, company_id: int) -> list[dict[str, Any]]:
    """The platform's carries out of a sprint, oldest first."""
    rows = session.exec(
        select(TaskEvent)
        .where(
            TaskEvent.company_id == company_id,
            TaskEvent.event_type == CARRY_KIND,
            TaskEvent.actor == task_events.SYSTEM,
        )
        .order_by(TaskEvent.id.desc())
        .limit(LIMIT * 2)
    ).all()
    out = []
    for event in sorted(rows, key=lambda e: e.id):
        payload = event.payload if isinstance(event.payload, dict) else {}
        if payload.get("direction") != LEAVING:
            continue
        out.append(
            {
                "event_id": event.id,
                "from_task_id": event.task_id,
                "from_sprint_id": event.sprint_id,
                "to_task_id": _int(payload.get("task")),
                "to_sprint_id": _int(payload.get("sprint")),
                "created_at": _iso(event.created_at),
                "chain_hash": event.chain_hash,
            }
        )
    return out[-LIMIT:]


def releases(session: Session, company_id: int) -> list[dict[str, Any]]:
    """The company's published sprint integrations, oldest first."""
    rows = session.exec(
        select(SprintIntegration)
        .where(
            SprintIntegration.company_id == company_id,
            SprintIntegration.publication.is_not(None),
        )
        .order_by(SprintIntegration.id.desc())
        .limit(LIMIT)
    ).all()
    published = [
        r
        for r in rows
        if isinstance(r.publication, dict) and r.publication.get("commit")
    ]
    audit_ids = [a for r in published if (a := _int(r.publication.get("audit_log_id")))]
    audits = (
        {
            a.id: a
            for a in session.exec(
                select(AuditLog).where(
                    AuditLog.id.in_(audit_ids), AuditLog.company_id == company_id
                )
            ).all()
        }
        if audit_ids
        else {}
    )
    out = []
    for row in sorted(published, key=lambda r: r.id):
        publication = row.publication
        audit = audits.get(_int(publication.get("audit_log_id")))
        out.append(
            {
                "integration_id": row.id,
                "sprint_id": row.sprint_id,
                "status": row.status,
                "tested_commit": row.tested_commit,
                "published_commit": str(publication.get("commit"))[:64],
                "how": str(publication.get("how") or "")[:20],
                "provider": str(publication.get("provider") or "")[:20] or None,
                "base": str(publication.get("base") or "")[:120] or None,
                "published_at": _iso(audit.created_at) if audit else None,
                "audit_log_id": audit.id if audit else None,
                "audit_chain_hash": audit.chain_hash if audit else None,
            }
        )
    return out
