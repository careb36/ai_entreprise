"""What a person's move on the board does: a card moved to To do starts.

``PATCH /v1/backlog/{id}`` used to record the move and wait. A task moved
back to To do from another column now starts, the way the inbox's "Send back
to work" does:

- **Send-back.** Without a comment of the person's, the move is recorded as a
  send-back (``SENT_BACK_ON_BOARD``, which starts with
  ``board.SEND_BACK_MARKER``), so the task's rework budgets restart; a blocked
  task that kept its spent budgets would block again on its first rework.
- **Reopening.** A ``completed`` sprint (closed by the worker once every task
  settled) and a ``cancelled`` one (stopped by a Manager) are reopened to
  ``active``: the person is asking for work. An archived sprint is refused
  (409, unarchive it first). The reopening is a conditional update from the
  status that was read, and an audit row (``sprint_reopened``, under the
  sprint's administration reference) records who, which task and from what.
- **Halted.** A ``planned`` or ``paused`` sprint is not started: the answer
  says so (``queued: false`` with the reason) and the task waits as ``todo``
  until the sprint is started or resumed, which dispatches it.
- **Queued now.** A task whose dependencies are done gets its ``task.assign``
  outbox event at once instead of at the next review cycle. The cycle may
  queue it again; the worker claims a task with a conditional update from
  ``todo`` (``replicas.claim_todo``), so the second assignment is skipped.

**Closing again.** A reopened sprint closes like any other once its tasks
settle. What happens once per sprint happened at its first close: the
retrospective, the procedures measured and proposed, the memory engram and
the follow-up sprint at autonomy 3. ``closed_before`` (a ``sprint_reopened``
row from ``completed``) tells the worker's close it is a second one, and it
skips them; the integration of the sprint's branches runs again, since it
only moves forward. A sprint reopened from ``cancelled`` never closed, so its
first close is a full one.

``carry_to_sprint`` is the board's "Move to sprint…": a blocked task waiting
for a person is carried (``services.carry_over``: once only, the original
marked, the new task told what blocked it) into another open sprint of the
company or into a new one. A ``todo`` task is first blocked with a note
saying a person moved it, and then carried the same way. Refused (409): a task
whose dependencies are not all done (the clone would drop them and start at
once), and a task its sprint's acceptance tests check (a sprint planned from a
specification: the clone would lose the criterion checks and the protection
of those test files, which ``acceptance.evidence_for`` reads from the task's
own sprint). The carry runs under the task's lock and its company's
``SPRINT_CLOSE`` lock (the one ``replicas.close_once`` takes), with the task,
its sprint and the target read again under them and held through the commit:
two requests never carry one task twice, and the worker cannot close the
target between its check and the insertion. A lock held elsewhere is a 409.
"""

from __future__ import annotations

from datetime import UTC, date, datetime
from typing import Any
from uuid import uuid4

from models import AuditLog, BacklogItem, OutboxEvent, Sprint
from schemas import SCOPE_HARD_MAX
from sqlalchemy import update
from sqlmodel import Session, func, select

from services import (
    acceptance,
    carry_over,
    dependencies,
    locks,
    sprint_archive,
    sprint_control,
    task_events,
)
from services.agent_service import AgentService
from services.board import SEND_BACK_MARKER

SENT_BACK_ON_BOARD = f"{SEND_BACK_MARKER} by a person (moved to To do on the board)."
# "manual triage" keeps it blocked for a person (``board.blocked_item_next_step``)
# even when an earlier deliverable is on its thread: the new task carries the work.
MOVED_NOTE = (
    "Moved: a person moved this task to another sprint; it stays here for "
    "manual triage and the new task carries the work."
)
REOPEN_ACTION = "sprint_reopened"
REOPENABLE = ("completed", sprint_control.CANCELLED)
CARRY_SOURCES = ("blocked", "todo")


class BoardMoveError(ValueError):
    """A refused move; ``status`` is the HTTP status (404 or 409)."""

    def __init__(self, status: int, detail: str):
        super().__init__(detail)
        self.status = status
        self.detail = detail


def moves_to_todo(item: BacklogItem, status: str | None) -> bool:
    return status == "todo" and item.status != "todo"


def prepare(
    sprint: Sprint | None, item: BacklogItem, status: str | None, comment: str | None
) -> tuple[bool, str]:
    """Whether the change is a move to To do that starts the task, and the
    comment to record: the person's, or the send-back note for a bare move.

    Refuses (409) a move to To do in an archived sprint, before anything is
    written.
    """
    starts = sprint is not None and moves_to_todo(item, status)
    if starts and sprint.archived_at is not None:
        raise BoardMoveError(
            409, "The sprint is archived: unarchive it before moving its tasks"
        )
    if comment or not moves_to_todo(item, status):
        return starts, comment or ""
    return starts, SENT_BACK_ON_BOARD


def _halted_reason(status: str) -> str:
    verb = "start" if status == sprint_control.PLANNED else "resume"
    return f"the sprint is {status}: {verb} it to run its tasks"


def _assign(session: Session, item: BacklogItem, sprint_id: int) -> None:
    session.add(
        OutboxEvent(
            aggregate_type="backlog_item",
            aggregate_id=str(item.id),
            event_type="nats_publish",
            payload={
                "subject": "task.assign",
                "message": {
                    "backlog_item_id": item.id,
                    "interaction_id": str(uuid4()),
                    "agent": item.assignee or "PO",
                    "sprint_id": sprint_id,
                },
            },
        )
    )


def _reopen(session: Session, sprint: Sprint) -> bool:
    """``completed`` or ``cancelled`` → ``active``, only from the status read."""
    result = session.exec(  # type: ignore[call-overload]
        update(Sprint)
        .where(Sprint.id == sprint.id, Sprint.status == sprint.status)
        .values(status=sprint_control.ACTIVE, pause_reason=None)
        .execution_options(synchronize_session=False)
    )
    return result.rowcount == 1


def start_moved_task(
    session: Session,
    item: BacklogItem,
    sprint: Sprint | None,
    user: Any = None,
    starts: bool = True,
) -> dict[str, Any]:
    """Start a task a person just moved to To do; the answer's extra fields.

    Nothing (``{}``) unless *starts* (``prepare``'s answer). The caller has
    recorded the move. Commits when it reopens the sprint (the audit row is
    written with it); otherwise the caller commits.
    """
    if not starts or sprint is None:
        return {}
    actor = getattr(user, "email", None) or "a person"
    reopened_from = None
    if sprint.status in REOPENABLE:
        reopened_from = sprint.status
        if _reopen(session, sprint):
            sprint.status = sprint_control.ACTIVE
            sprint.pause_reason = None
        else:
            session.refresh(sprint)
            reopened_from = None
    answer: dict[str, Any] = {
        "queued": False,
        "reopened": reopened_from is not None,
        "sprint_status": sprint.status,
        "reason": None,
    }
    if sprint_control.is_halted(sprint.status) or sprint.status in REOPENABLE:
        answer["reason"] = _halted_reason(sprint.status)
    elif not dependencies.is_ready(session, item):
        answer["reason"] = "it waits for other tasks of the sprint to be done"
    else:
        _assign(session, item, sprint.id)
        answer["queued"] = True
    if reopened_from is not None:
        AgentService().audit_interaction(  # commits the move with it
            session=session,
            company_id=sprint.company_id,
            interaction_id=f"sprint:{sprint.id}:admin",
            agent_id=f"human:{actor}",
            action=REOPEN_ACTION,
            details={"company_id": str(sprint.company_id), "sprint_id": sprint.id},
            result={"from_status": reopened_from, "backlog_item_id": item.id},
            decision_signature=None,
            cost_tokens=0,
        )
    return answer


def closed_before(session: Session, sprint_id: int) -> bool:
    """Whether a person reopened *sprint_id* after the worker had closed it."""
    rows = session.exec(
        select(AuditLog.result).where(
            AuditLog.interaction_id == f"sprint:{sprint_id}:admin",
            AuditLog.action == REOPEN_ACTION,
        )
    ).all()
    return any(
        isinstance(r, dict) and r.get("from_status") == "completed" for r in rows
    )


def _target(
    session: Session, source: Sprint, target_sprint_id: int | None
) -> tuple[Sprint, bool]:
    """The sprint to carry into, and whether it was created for it."""
    if target_sprint_id is None:
        sprint = Sprint(
            company_id=source.company_id,
            sprint_name=f"Carried from {source.sprint_name}"[:255],
            start_date=date.today().isoformat(),
            scope_size="S",
            status=sprint_control.ACTIVE,
            goal=f'Another attempt at what "{source.sprint_name}" left blocked.',
            specification_id=source.specification_id,
        )
        session.add(sprint)
        session.flush()
        return sprint, True
    target = session.get(Sprint, target_sprint_id)
    if target is None or target.company_id != source.company_id:
        raise BoardMoveError(404, "Target sprint not found in this company")
    session.refresh(target)  # its state as committed, under the company's lock
    if target.id == source.id:
        raise BoardMoveError(409, "The task is already in that sprint")
    if target.archived_at is not None:
        raise BoardMoveError(409, "The target sprint is archived")
    if target.status not in sprint_control.OPEN_STATUSES:
        raise BoardMoveError(
            409, f"The target sprint is {target.status}: pick an open sprint"
        )
    count = session.exec(
        select(func.count(BacklogItem.id)).where(BacklogItem.sprint_id == target.id)
    ).one()
    if int(count or 0) >= SCOPE_HARD_MAX:
        raise BoardMoveError(409, f"The target sprint already has {count} tasks")
    return target, False


def _checked_by_acceptance_tests(
    session: Session, item: BacklogItem, source: Sprint
) -> bool:
    """Whether a test task of *source* covers one of *item*'s criteria."""
    covers = {str(c) for c in item.covers or []}
    if source.specification_id is None or not covers:
        return False
    tests = session.exec(
        select(BacklogItem).where(
            BacklogItem.sprint_id == source.id,
            BacklogItem.kind == acceptance.ACCEPTANCE_KIND,
        )
    ).all()
    return any(covers & {str(c) for c in t.covers or []} for t in tests)


def carry_to_sprint(
    session: Session,
    item: BacklogItem,
    source: Sprint,
    target_sprint_id: int | None,
    actor: str,
) -> dict[str, Any]:
    """Carry *item* into another sprint (a new one when no id); the answer.

    Raises ``BoardMoveError`` before writing. Commits, under the task's and
    the company's locks.
    """
    engine = session.get_bind()
    with locks.held(engine, locks.TASK, item.id) as task_free:
        if not task_free:
            raise BoardMoveError(
                409, f"Task {item.id} is being worked on or moved; try again"
            )
        with locks.held(engine, locks.SPRINT_CLOSE, source.company_id) as free:
            if not free:
                raise BoardMoveError(
                    409,
                    "A sprint of this company is closing or a task is moving; try again",
                )
            # What another request or the worker committed before the locks.
            session.refresh(item)
            session.refresh(source)
            answer = _carry(session, item, source, target_sprint_id, actor)
            session.commit()
            return answer


def _carry(
    session: Session,
    item: BacklogItem,
    source: Sprint,
    target_sprint_id: int | None,
    actor: str,
) -> dict[str, Any]:
    if source.archived_at is not None:
        raise BoardMoveError(409, "The sprint is archived: unarchive it first")
    if item.status not in CARRY_SOURCES:
        raise BoardMoveError(409, "Only a blocked or To do task can move to a sprint")
    if carry_over.carried_over(item):
        raise BoardMoveError(409, f"Task {item.id} was already carried over")
    if item.status == "blocked" and not carry_over.waits_for_a_person(item):
        raise BoardMoveError(409, f"Task {item.id} is not blocked waiting for a person")
    if not dependencies.is_ready(session, item):
        raise BoardMoveError(
            409,
            f"Task {item.id} waits for tasks of its sprint that are not done; "
            "moving it would drop them",
        )
    if _checked_by_acceptance_tests(session, item, source):
        raise BoardMoveError(
            409,
            f"Task {item.id} is checked by the acceptance tests of its sprint; "
            "moving it would leave them behind: send it back to work here instead",
        )
    target, created = _target(session, source, target_sprint_id)
    if item.status == "todo":
        # A person decided; the System signs the block, as a stop does.
        task_events.record(
            session,
            item,
            "blocked",
            task_events.SYSTEM,
            status="blocked",
            text=MOVED_NOTE,
            payload={"moved_by": "person"},
            created_at=datetime.now(UTC).isoformat(),
            company_id=source.company_id,
        )
    try:
        (clone,) = carry_over.carry_over(
            session, source.company_id, [item.id], target, actor, "Manager"
        )
    except carry_over.CarryOverError as exc:
        raise BoardMoveError(exc.status, exc.detail) from exc
    running = not sprint_control.is_halted(target.status)
    return {
        "backlog_item_id": clone.id,
        "sprint_id": target.id,
        "sprint_name": target.sprint_name,
        "sprint_status": target.status,
        "created_sprint": created,
        "queued": running and not clone.depends_on,
        "reason": None if running else _halted_reason(target.status),
    }


def open_targets(session: Session, source: Sprint) -> list[dict[str, Any]]:
    """The company's open sprints a task of *source* may move to."""
    rows = session.exec(
        select(Sprint)
        .where(
            Sprint.company_id == source.company_id,
            Sprint.id != source.id,
            Sprint.status.in_(sprint_control.OPEN_STATUSES),  # type: ignore[attr-defined]
            sprint_archive.listed(),
        )
        .order_by(Sprint.id.desc())  # type: ignore[union-attr]
    ).all()
    return [
        {"id": s.id, "sprint_name": s.sprint_name, "status": s.status} for s in rows
    ]
