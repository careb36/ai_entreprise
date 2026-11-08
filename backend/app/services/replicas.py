"""What the task worker does once even when several replicas run it (issue #328).

The JetStream consumer ``task-worker`` already hands each stored assignment
to one replica. What remained were the places that assumed one process:

- **Claiming a task.** Two assignments for one task (the outbox's and a
  later re-dispatch, with different message ids) could reach two replicas
  that both read ``todo`` and both ran it. :func:`claim_todo` moves the task
  to ``in_progress`` with one conditional ``UPDATE … WHERE status = 'todo'``;
  only the replica whose update matched works on it.
- **Recovering after a restart.** A replica that starts returned *every*
  ``in_progress`` task to ``todo``, including the ones another live replica
  was working on. A replica holds the task's lock while it works
  (:func:`task_lock`), and :func:`recover_inflight` returns only the tasks
  whose lock nobody holds.
- **Closing a sprint.** Two replicas could both read ``active``, both mark
  it completed and both post the retrospective, measure the procedures,
  ask for proposals and open a follow-up. :func:`close_once` runs the close
  under the company's lock, and :func:`mark_completed` closes with a
  conditional ``UPDATE … WHERE status = 'active'``: one caller gets the
  close, and with it everything that follows.
- **The ScrumMaster's triage.** :func:`triage_slot` takes the escalation's
  lock and says whether a decision already answers it.

The review cycle and the Support and Reviewer agents' cycles run under a
lock of their own (``locks.REVIEW_CYCLE`` and the others): one replica at a
time, the others skip that round.
"""

from __future__ import annotations

from collections.abc import Callable, Iterator
from contextlib import contextmanager
from datetime import UTC, datetime
from typing import Any

from logging_config import setup_logger
from models import AgentMessage, BacklogItem, Sprint
from sqlalchemy import update
from sqlalchemy.engine import Engine
from sqlmodel import Session, select

from services import locks, scrum_master
from services.board import comment_list, has_manual_freeze_marker

logger = setup_logger("fabrique-replicas")

RECOVERY_NOTE = (
    "Worker recovery after backend restart — unfinished in-progress task "
    "returned to todo for redispatch."
)


def claim_todo(engine: Engine, item_id: int) -> bool:
    """Move a ``todo`` task to ``in_progress``; ``True`` only for the caller whose update matched."""
    with Session(engine) as session:
        result = session.exec(  # type: ignore[call-overload]
            update(BacklogItem)
            .where(BacklogItem.id == item_id, BacklogItem.status == "todo")
            .values(status="in_progress", updated_at=datetime.now(UTC))
        )
        session.commit()
        return result.rowcount == 1


@contextmanager
def task_lock(engine: Engine, item_id: Any) -> Iterator[bool]:
    """Held by the replica working on a task, for as long as it works on it."""
    if item_id is None:
        yield True
        return
    with locks.held(engine, locks.TASK, item_id) as mine:
        yield mine


def recover_inflight(engine: Engine) -> list[dict[str, Any]]:
    """Return to ``todo`` the ``in_progress`` tasks no live replica is working on."""
    recovered: list[dict[str, Any]] = []
    now = datetime.now(UTC)
    with Session(engine) as session:
        inflight = session.exec(
            select(BacklogItem).where(BacklogItem.status == "in_progress")
        ).all()
        for item in inflight:
            with locks.held(engine, locks.TASK, item.id) as free:
                if not free:
                    continue  # another replica holds it: it is working on it
                session.refresh(item)
                if item.status != "in_progress":
                    continue
                comments = comment_list(item.comments)
                item.updated_at = now
                if has_manual_freeze_marker(comments):
                    item.status = "blocked"
                    session.add(item)
                    session.commit()
                    continue
                comments.append(
                    {
                        "author": "System",
                        "text": RECOVERY_NOTE,
                        "created_at": now.isoformat(),
                    }
                )
                item.comments = comments
                item.status = "todo"
                session.add(item)
                session.commit()
                recovered.append(
                    {
                        "id": item.id,
                        "sprint_id": item.sprint_id,
                        "title": item.title,
                        "assignee": item.assignee or "Dev",
                    }
                )
    return recovered


def mark_completed(session: Session, sprint: Sprint) -> bool:
    """Close *sprint* if it is still active; ``False`` when another caller closed it first.

    The conditional update is what a second replica waits on and then finds
    nothing to match, whatever it read before.
    """
    result = session.exec(  # type: ignore[call-overload]
        update(Sprint)
        .where(Sprint.id == sprint.id, Sprint.status == "active")
        .values(status="completed")
        .execution_options(synchronize_session=False)
    )
    if result.rowcount != 1:
        session.commit()
        return False
    sprint.status = "completed"
    return True


CLOSING_ELSEWHERE = {
    "completed": False,
    "auto_opened": False,
    "reason": "closing_elsewhere",
}


def close_once(
    engine: Engine,
    sprint_id: int,
    close: Callable[..., dict[str, Any]],
    *args: Any,
) -> dict[str, Any]:
    """Run *close* for *sprint_id* under its company's lock.

    The company's, not the sprint's: two of its sprints closing at once must
    not each open a follow-up. A replica that finds the lock taken returns
    ``closing_elsewhere``; the next review cycle looks again.
    """
    with Session(engine) as session:
        sprint = session.get(Sprint, sprint_id)
        company_id = sprint.company_id if sprint else None
    if company_id is None:
        return close(sprint_id, *args)
    with locks.held(engine, locks.SPRINT_CLOSE, company_id) as mine:
        if not mine:
            return dict(CLOSING_ELSEWHERE)
        return close(sprint_id, *args)


def escalation_answered(session: Session, item_id: int, escalation_id: int) -> bool:
    """Whether a ScrumMaster decision on the task already names this escalation."""
    bodies = session.exec(
        select(AgentMessage.body).where(
            AgentMessage.backlog_item_id == item_id,
            AgentMessage.kind == "decision",
        )
    ).all()
    return any(
        scrum_master.answered_escalation(body or "") == escalation_id for body in bodies
    )


@contextmanager
def triage_slot(
    engine: Engine, item_id: int, escalation_id: int | None
) -> Iterator[bool]:
    """``True`` when this caller should triage: it holds the lock and nothing answered yet."""
    ident = escalation_id if escalation_id is not None else f"item-{item_id}"
    with locks.held(engine, locks.TRIAGE, ident) as mine:
        if mine and escalation_id is not None:
            with Session(engine) as session:
                mine = not escalation_answered(session, item_id, escalation_id)
        yield mine
