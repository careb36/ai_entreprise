"""A routine's revisions: each version, whole, numbered (issue #774).

Writing a routine is revision 1; each change that alters it writes the next
number with a snapshot of every field (``SNAPSHOT_FIELDS``) and what
changed. A run records the number of the revision it ran
(``routines.run``), so a sprint a routine opened is attributed to the exact
text, schedule and variables it ran with. A restore puts back a revision's
snapshot as a new revision with ``restored_from``; nothing is rewritten.

The revisions of a deleted routine stay, as its audit rows do. From
Paperclip's ``routine_revisions`` and ``routine_runs.routine_revision_id``
(MIT: ``packages/db/src/schema/routines.ts``); no code taken.
"""

from __future__ import annotations

from typing import Any

from models import Routine, RoutineRevision
from sqlmodel import Session, select

SNAPSHOT_FIELDS = (
    "title",
    "description",
    "assignee",
    "schedule",
    "enabled",
    "variables",
)
SUMMARY_MAX = 300


def snapshot(routine: Routine) -> dict[str, Any]:
    return {field: getattr(routine, field) for field in SNAPSHOT_FIELDS}


def write(
    session: Session,
    routine: Routine,
    summary: str | None,
    actor: dict[str, Any] | None,
    restored_from: int | None = None,
) -> RoutineRevision:
    """Record *routine* as it is now, as revision ``routine.revision``; the
    caller commits."""
    revision = RoutineRevision(
        company_id=routine.company_id,
        routine_id=routine.id,
        number=routine.revision,
        snapshot=snapshot(routine),
        change_summary=(summary or None) and summary[:SUMMARY_MAX],
        restored_from=restored_from,
        created_by_user_id=(actor or {}).get("user_id"),
    )
    session.add(revision)
    return revision


def view(revision: RoutineRevision) -> dict[str, Any]:
    return {
        "number": revision.number,
        "snapshot": revision.snapshot,
        "change_summary": revision.change_summary,
        "restored_from": revision.restored_from,
        "created_by_user_id": revision.created_by_user_id,
        "created_at": revision.created_at.isoformat() if revision.created_at else None,
    }


def listed(session: Session, routine: Routine) -> list[dict[str, Any]]:
    """The routine's revisions, newest first."""
    rows = session.exec(
        select(RoutineRevision)
        .where(
            RoutineRevision.routine_id == routine.id,
            RoutineRevision.company_id == routine.company_id,
        )
        .order_by(RoutineRevision.number.desc())  # type: ignore[attr-defined]
    ).all()
    return [view(row) for row in rows]


def find(session: Session, routine: Routine, number: int) -> RoutineRevision | None:
    return session.exec(
        select(RoutineRevision).where(
            RoutineRevision.routine_id == routine.id,
            RoutineRevision.company_id == routine.company_id,
            RoutineRevision.number == number,
        )
    ).first()
