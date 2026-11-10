"""What the task worker does once even when several replicas run it (issue #328).

The JetStream consumer ``task-worker`` already hands each stored assignment
to one replica. What remained were the places that assumed one process:

- **Claiming a task.** Two assignments for one task (the outbox's and a
  later re-dispatch, with different message ids) could reach two replicas
  that both read ``todo`` and both ran it. :func:`claim_todo` moves the task
  to ``in_progress`` with one conditional ``UPDATE … WHERE status = 'todo'``;
  only the replica whose update matched works on it.
- **Recovering a dead replica's tasks.** A replica that starts returned
  *every* ``in_progress`` task to ``todo``, including the ones another live
  replica was working on, and a replica that died left its task
  ``in_progress`` until some replica restarted (a survivor acknowledges the
  redelivered assignment, since the task is no longer ``todo``). A replica
  holds the task's lock while it works (:func:`task_lock`): the lock is the
  lease, and it goes with the dead replica's connection.
  :func:`recover_inflight` returns only the tasks whose lock nobody holds,
  and :func:`recover_stale` runs it at the start of every review cycle,
  under the cycle's lock, so a survivor dispatches the task again within a
  cycle.
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

import asyncio
from collections.abc import Callable, Iterator
from contextlib import contextmanager
from datetime import UTC, datetime
from typing import Any

from logging_config import setup_logger
from models import AgentMessage, BacklogItem, Sprint
from sqlalchemy import update
from sqlalchemy.engine import Engine
from sqlmodel import Session, select

from services.learning import scrum_master
from services.platform import locks
from services.platform.glassroom import publish_glassroom
from services.tasks import task_events, task_projection

logger = setup_logger("fabrique-replicas")

RECOVERY_NOTE = (
    "Worker recovery — unfinished in-progress task no live worker holds "
    "(a backend restarted or stopped) returned to todo for redispatch."
)


def claim_todo(engine: Engine, item_id: int) -> bool:
    """Move a ``todo`` task to ``in_progress``; ``True`` only for the caller whose update matched.

    The claim is the task's ``claimed`` event (issue #324), written in the
    same transaction as the update that won it.
    """
    with Session(engine) as session:
        result = session.exec(  # type: ignore[call-overload]
            update(BacklogItem)
            .where(BacklogItem.id == item_id, BacklogItem.status == "todo")
            .values(status="in_progress", updated_at=datetime.now(UTC))
        )
        if result.rowcount != 1:
            session.commit()
            return False
        item = session.get(BacklogItem, item_id)
        task_events.record(session, item, "claimed", "system", status="in_progress")
        session.commit()
        return True


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
                # Every transition is an event first (issue #324).
                if task_projection.record_of(session, item).frozen():
                    task_events.record(
                        session, item, "blocked", "system", status="blocked"
                    )
                    session.commit()
                    continue
                task_events.record(
                    session,
                    item,
                    "recovered",
                    "system",
                    status="todo",
                    text=RECOVERY_NOTE,
                )
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


async def recover_stale(engine: Engine, nats_client: Any) -> list[dict[str, Any]]:
    """Return the tasks no live replica holds to ``todo`` and tell their Glassroom.

    Called at the start of each review cycle (startup's first cycle
    included), whose dispatch then queues them again.
    """
    recovered = await asyncio.to_thread(recover_inflight, engine)
    for item in recovered:
        logger.info("Recovered item=%s: no live worker held it", item["id"])
        await publish_glassroom(
            nats_client,
            item["sprint_id"],
            {
                "type": "status",
                "semantic_type": "task.recovered",
                "source": "task_worker",
                "agent": "System",
                "role": "System",
                "task_id": item["id"],
                "task_status": "todo",
                "phase": "restart_recovery",
                "task": item["title"],
                "message": f"♻️ Recovered: no live worker held {item['title']}; returned to todo for autonomous redispatch.",
            },
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
