"""
Editing, deleting and archiving a sprint: what may go, and what must stay.

POSITIONING.md's rule is that evidence is never lost. So ``remove`` deletes a
sprint only when nothing was recorded about its work, and archives it
otherwise: an archived sprint leaves the lists, the inbox and carry-over, and
its board, runs, events and audit rows stay as they were, readable by id.
The audit chain is never touched; deleting a sprint deletes no audit row.

A sprint has **evidence** (``evidence_of``) when any of these exist:

- an ``AgentRun`` of the company whose ``external_ref`` is one of its tasks
  (``task-<id>``) or whose ``subject.sprint_id`` names it (so a run's
  verifications, approvals, reviews and publications hang on one of them);
- a ``TaskEvent`` of the sprint other than the System's own bookkeeping: a
  dependency note (``note`` by ``system``) and the block ``stop`` writes
  (``blocked`` by ``system`` with ``stopped_by``). A claim, a delivery, a
  person's note or move, a carry-over, anything else is evidence;
- an ``AgentMessage`` of the sprint or of one of its tasks;
- a ``SprintIntegration`` of the sprint;
- a ``Procedure`` proposed from it (``source_sprint_id``) or measured from
  it (``adopted_after_sprint_id``);
- a ``ManagerDecision`` about one of its tasks;
- an ``audit_log`` row of the company about it or its tasks
  (``interaction_id`` ``task-<id>`` or ``sprint:<id>:…``, or ``details``
  naming its ``sprint_id`` or a task's ``backlog_item_id``), except the rows
  this module writes for the sprint's own administration
  (``sprint:<id>:admin``);
- a ``performance_metrics`` row (the init.sql table, when it exists).

Without evidence, ``remove`` deletes the sprint, its backlog items, their
bookkeeping events, their run secrets (credentials for runs that never
happened) and the outbox rows queued for them (``task.assign`` and the
kickoff), in that order. Webhook deliveries already queued stay.

Only a halted sprint goes: ``planned``, ``cancelled`` or ``completed``. A
running one (``active``, ``needs_split``, ``paused``) is stopped first.
"""

from __future__ import annotations

from datetime import UTC, datetime
from typing import Any

from models import (
    AgentMessage,
    AgentRun,
    AuditLog,
    BacklogItem,
    OutboxEvent,
    Procedure,
    RunSecret,
    Sprint,
    SprintIntegration,
    TaskEvent,
)
from models.manager import ManagerDecision
from sqlalchemy import String, cast, delete, func, inspect, or_
from sqlalchemy import text as sql_text
from sqlmodel import Session, select

from services.agent_service import AgentService
from services.sprint_control import CANCELLED, PLANNED

REMOVABLE_STATUSES = (PLANNED, CANCELLED, "completed")
STOP_FIRST = "Stop the sprint first: only a planned, cancelled or completed sprint can be deleted or archived"
EDITABLE_FIELDS = ("sprint_name", "goal", "duration_days")


class SprintAdminError(Exception):
    """A refusal with its HTTP status and reason."""

    def __init__(self, status: int, detail: str):
        super().__init__(detail)
        self.status = status
        self.detail = detail


def listed():
    """SQL condition: the sprint is not archived (lists, inbox, carry-over)."""
    return Sprint.archived_at.is_(None)  # type: ignore[union-attr]


def _now() -> datetime:
    return datetime.now(UTC).replace(tzinfo=None)


def _task_ids(session: Session, sprint_id: int) -> list[int]:
    return list(
        session.exec(select(BacklogItem.id).where(BacklogItem.sprint_id == sprint_id))
    )


def _admin_ref(sprint_id: int) -> str:
    return f"sprint:{sprint_id}:admin"


def _bookkeeping(event: TaskEvent) -> bool:
    """The System's own notes that record no work: a dependency wait, a stop."""
    if event.actor != "system":
        return False
    if event.event_type == "note":
        return True
    payload = event.payload or {}
    return event.event_type == "blocked" and bool(payload.get("stopped_by"))


def _json_is(column: Any, key: str, values: list[int]) -> Any:
    """``column[key]`` is one of *values*, as a JSON number or a JSON string."""
    texts = [str(v) for v in values] + [f'"{v}"' for v in values]
    return cast(column[key], String).in_(texts)


def _count(session: Session, statement: Any) -> int:
    return int(session.exec(statement).one() or 0)


def _performance_rows(session: Session, sprint_id: int) -> int:
    bind = session.get_bind()
    if not inspect(bind).has_table("performance_metrics"):
        return 0
    return int(
        session.connection()
        .execute(
            sql_text("SELECT COUNT(*) FROM performance_metrics WHERE sprint_id = :s"),
            {"s": sprint_id},
        )
        .scalar()
        or 0
    )


def evidence_of(session: Session, sprint: Sprint) -> list[str]:
    """The kinds of evidence recorded about *sprint*; empty when there is none."""
    sid = sprint.id
    tasks = _task_ids(session, sid)
    refs = [f"task-{t}" for t in tasks]
    found: list[str] = []

    run_match = _json_is(AgentRun.subject, "sprint_id", [sid])
    if refs:
        run_match = or_(run_match, AgentRun.external_ref.in_(refs))
    if _count(
        session,
        select(func.count(AgentRun.id)).where(
            AgentRun.company_id == sprint.company_id, run_match
        ),
    ):
        found.append("agent_runs")

    events = session.exec(select(TaskEvent).where(TaskEvent.sprint_id == sid)).all()
    if any(not _bookkeeping(e) for e in events):
        found.append("task_events")

    message_match = AgentMessage.sprint_id == sid
    if tasks:
        message_match = or_(message_match, AgentMessage.backlog_item_id.in_(tasks))
    if _count(session, select(func.count(AgentMessage.id)).where(message_match)):
        found.append("agent_messages")

    if _count(
        session,
        select(func.count(SprintIntegration.id)).where(
            SprintIntegration.sprint_id == sid
        ),
    ):
        found.append("sprint_integrations")

    if _count(
        session,
        select(func.count(Procedure.id)).where(
            or_(
                Procedure.source_sprint_id == sid,
                Procedure.adopted_after_sprint_id == sid,
            )
        ),
    ):
        found.append("procedures")

    if tasks and _count(
        session,
        select(func.count(ManagerDecision.id)).where(
            ManagerDecision.company_id == sprint.company_id,
            ManagerDecision.item_kind == "task",
            ManagerDecision.item_id.in_(tasks),
        ),
    ):
        found.append("manager_decisions")

    about = [
        AuditLog.interaction_id.like(f"sprint:{sid}:%"),
        _json_is(AuditLog.details, "sprint_id", [sid]),
    ]
    if tasks:
        about += [
            AuditLog.interaction_id.in_(refs),
            _json_is(AuditLog.details, "backlog_item_id", tasks),
        ]
    if _count(
        session,
        select(func.count(AuditLog.id)).where(
            AuditLog.company_id == sprint.company_id,
            or_(
                AuditLog.interaction_id.is_(None),
                AuditLog.interaction_id != _admin_ref(sid),
            ),
            or_(*about),
        ),
    ):
        found.append("audit_log")

    if _performance_rows(session, sid):
        found.append("performance_metrics")
    return found


def _audit(
    session: Session,
    company_id: int,
    sprint_id: int,
    actor: str,
    action: str,
    result: dict[str, Any],
) -> None:
    """One audit row for the sprint's administration; commits the session."""
    AgentService().audit_interaction(
        session=session,
        company_id=company_id,
        interaction_id=_admin_ref(sprint_id),
        agent_id=f"human:{actor}",
        action=action,
        details={"company_id": str(company_id), "sprint_id": sprint_id},
        result=result,
        decision_signature=None,
        cost_tokens=0,
    )


def update(
    session: Session, sprint: Sprint, changes: dict[str, Any], actor: str
) -> dict[str, dict[str, Any]]:
    """Apply *changes* (only ``EDITABLE_FIELDS``); the before/after of each change."""
    if sprint.archived_at is not None:
        raise SprintAdminError(
            409, "An archived sprint cannot be edited; unarchive it first"
        )
    changed: dict[str, dict[str, Any]] = {}
    for name in EDITABLE_FIELDS:
        if name not in changes:
            continue
        before, after = getattr(sprint, name), changes[name]
        if before != after:
            changed[name] = {"before": before, "after": after}
            setattr(sprint, name, after)
    if changed:
        session.add(sprint)
        _audit(
            session,
            sprint.company_id,
            sprint.id,
            actor,
            "sprint_updated",
            {"changes": changed},
        )
    return changed


def _hard_delete(session: Session, sprint: Sprint) -> dict[str, int]:
    sid = sprint.id
    tasks = _task_ids(session, sid)
    removed: dict[str, int] = {}
    outbox = (OutboxEvent.aggregate_type == "sprint") & (
        OutboxEvent.aggregate_id == str(sid)
    )
    if tasks:
        outbox = outbox | (
            (OutboxEvent.aggregate_type == "backlog_item")
            & OutboxEvent.aggregate_id.in_([str(t) for t in tasks])
        )
        removed["run_secrets"] = session.exec(
            delete(RunSecret).where(RunSecret.task_id.in_(tasks))
        ).rowcount
    removed["outbox_events"] = session.exec(delete(OutboxEvent).where(outbox)).rowcount
    removed["task_events"] = session.exec(
        delete(TaskEvent).where(TaskEvent.sprint_id == sid)
    ).rowcount
    removed["backlog_items"] = session.exec(
        delete(BacklogItem).where(BacklogItem.sprint_id == sid)
    ).rowcount
    session.exec(delete(Sprint).where(Sprint.id == sid))
    return removed


def remove(
    session: Session, sprint: Sprint, actor: str, reason: str | None = None
) -> dict[str, Any]:
    """Delete *sprint* when it has no evidence, else archive it. Audited; commits."""
    if sprint.archived_at is not None:
        raise SprintAdminError(409, "The sprint is already archived")
    if sprint.status not in REMOVABLE_STATUSES:
        raise SprintAdminError(409, f"{STOP_FIRST}; this one is {sprint.status}")
    found = evidence_of(session, sprint)
    if not found:
        snapshot = {
            "sprint_id": sprint.id,
            "sprint_name": sprint.sprint_name,
            "status": sprint.status,
        }
        # The audit row is written in the same transaction as the delete; it
        # names the sprint by id only, in JSON, so nothing references a row
        # that no longer exists.
        company_id, sid = sprint.company_id, sprint.id
        removed = _hard_delete(session, sprint)
        session.expunge(sprint)
        _audit(
            session,
            company_id,
            sid,
            actor,
            "sprint_deleted",
            {**snapshot, "removed": removed, "reason": reason},
        )
        return {"deleted": True, "sprint_id": sid, "removed": removed}
    sprint.archived_at = _now()
    sprint.archived_by = actor
    sprint.archive_reason = (reason or "").strip() or None
    session.add(sprint)
    _audit(
        session,
        sprint.company_id,
        sprint.id,
        actor,
        "sprint_archived",
        {"evidence": found, "reason": sprint.archive_reason, "status": sprint.status},
    )
    return {
        "archived": True,
        "sprint_id": sprint.id,
        "archived_at": sprint.archived_at.isoformat(),
        "archived_by": actor,
        "evidence": found,
    }


def unarchive(session: Session, sprint: Sprint, actor: str) -> dict[str, Any]:
    """Bring an archived sprint back to the lists. Audited; commits."""
    if sprint.archived_at is None:
        raise SprintAdminError(409, "The sprint is not archived")
    was = {
        "archived_at": sprint.archived_at.isoformat(),
        "archived_by": sprint.archived_by,
    }
    sprint.archived_at = None
    sprint.archived_by = None
    sprint.archive_reason = None
    session.add(sprint)
    _audit(
        session,
        sprint.company_id,
        sprint.id,
        actor,
        "sprint_unarchived",
        {"was": was},
    )
    return {"archived": False, "sprint_id": sprint.id, "status": sprint.status}
