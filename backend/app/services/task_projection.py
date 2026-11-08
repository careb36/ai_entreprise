"""
The board's questions answered from a task's events (issue #324).

``services/task_events.record`` writes every routed transition as an event
and renders it as the board comment. Here the same questions
``services/board.py`` answers from the thread (what is pending, why the task
is blocked, whether it waits for a person) are answered from the events, by
their kind, actor, verdict and flags, never by reading comment text.

The events answer only for a task whose thread they cover: every comment
carries the id of one of the task's events, and the migration found nothing it
could not read back (no ``legacy`` event). Any other task (a thread written
before events existed, or by a writer not routed yet) is read from its
comments as before, so a task never gets a half-informed answer.

The public functions take a session and the task and pick the source;
the ``*_from_events`` functions are the projections themselves.
"""

from __future__ import annotations

from typing import Any

from models import BacklogItem, TaskEvent
from sqlmodel import Session

from services import board, task_events

_ADVICE_KINDS = {"carried_over", "advised"}


def _payload(event: TaskEvent) -> dict[str, Any]:
    return event.payload or {}


def _text(event: TaskEvent) -> str:
    return str(_payload(event).get("text") or "").strip()


def _verdict_events(events: list[TaskEvent]) -> list[TaskEvent]:
    """The events that may say something about this task's state."""
    return [e for e in events if e.event_type not in _ADVICE_KINDS]


def _needs_work(event: TaskEvent) -> bool:
    return _payload(event).get("verdict") == "needs_work"


def _is_person_send_back(event: TaskEvent) -> bool:
    return event.event_type == "sent_back" and event.actor == task_events.PERSON


def covers(item: BacklogItem, events: list[TaskEvent]) -> bool:
    """Whether *events* answer for *item*: they render its whole thread."""
    comments = board.comment_list(item.comments)
    if not comments or not events:
        return False
    if any(e.event_type == "legacy" for e in events):
        return False
    ids = {e.id for e in events}
    return all(c.get("event_id") in ids for c in comments)


def status_from_events(events: list[TaskEvent], default: str = "todo") -> str:
    """The status the latest transition that set one left."""
    for event in reversed(events):
        if event.task_status:
            return event.task_status
    return default


def pending_rework_from_events(events: list[TaskEvent]) -> list[str]:
    """The rework requests made since the latest deliverable."""
    feedback: list[str] = []
    for event in reversed(events):
        if not _text(event):
            continue
        if _needs_work(event):
            author = _payload(event).get("author") or "Reviewer"
            feedback.append(f"{author}: {_text(event)}")
        elif event.event_type == "delivered":
            break
    return list(reversed(feedback))


def latest_deliverable_from_events(events: list[TaskEvent]) -> str:
    """The deliverable to review, or empty when a rework request came after it."""
    for event in reversed(events):
        if not _text(event):
            continue
        if _needs_work(event):
            return ""
        if event.event_type == "delivered" and not _payload(event).get("frozen"):
            return _text(event)
    return ""


def _is_verdict(event: TaskEvent) -> bool:
    return bool(_text(event)) and (
        event.actor in (task_events.SYSTEM, task_events.GATE) or _needs_work(event)
    )


def block_reason_from_events(events: list[TaskEvent]) -> str:
    """The latest verdict: what a gate, the system or a reviewer said."""
    for event in reversed(_verdict_events(events)):
        if _is_verdict(event):
            return _text(event)
    return ""


def awaits_risk_acceptance_from_events(events: list[TaskEvent]) -> bool:
    """The latest verdict is a high-risk hold, approved and waiting for a person."""
    for event in reversed(_verdict_events(events)):
        if _is_verdict(event) or _is_person_send_back(event):
            return event.event_type == "risk_hold"
    return False


def next_step_from_events(events: list[TaskEvent]) -> str | None:
    """What the worker still does on its own with a blocked task.

    ``frozen``, ``review``, ``retry``, or ``None`` when it waits for a person;
    the same answer ``board.blocked_item_next_step`` gives from the thread.
    """
    events = [e for e in _verdict_events(events) if _text(e)]
    flags = [_payload(e) for e in events]
    if any(f.get("frozen") for f in flags):
        return "frozen"
    if (
        events
        and not any(f.get("explicit_block") for f in flags)
        and latest_deliverable_from_events(events)
    ):
        return "review"
    if any(f.get("needs_person") for f in flags):
        return None
    if not any(f.get("retryable") for f in flags):
        return None
    return None if sum(1 for f in flags if f.get("retry")) >= 1 else "retry"


# --- The questions, from the events when they cover the task -----------------


# Histories loaded at once (``task_events.for_tasks``), by task id; a caller
# answering for many tasks passes them so no question queries per task.
Histories = dict[int, list[TaskEvent]]


def _events(
    session: Session, item: BacklogItem, histories: Histories | None = None
) -> list[TaskEvent] | None:
    if item.id is None:
        return None
    if histories is not None and item.id in histories:
        events = histories[item.id]
    else:
        events = task_events.for_task(session, item.id)
    return events if covers(item, events) else None


def source(session: Session, item: BacklogItem) -> str:
    """``events`` when the events answer for *item*, else ``comments``."""
    return "events" if _events(session, item) is not None else "comments"


def pending_rework_feedback(
    session: Session, item: BacklogItem, histories: Histories | None = None
) -> list[str]:
    events = _events(session, item, histories)
    if events is None:
        return board.pending_rework_feedback(item.comments)
    return pending_rework_from_events(events)


def block_reason(
    session: Session, item: BacklogItem, histories: Histories | None = None
) -> str:
    events = _events(session, item, histories)
    if events is None:
        return board.block_reason(item.comments)
    return block_reason_from_events(events)


def awaits_risk_acceptance(
    session: Session, item: BacklogItem, histories: Histories | None = None
) -> bool:
    events = _events(session, item, histories)
    if events is None:
        return board.awaits_risk_acceptance(item.comments)
    return awaits_risk_acceptance_from_events(events)


def blocked_item_next_step(
    session: Session, item: BacklogItem, histories: Histories | None = None
) -> str | None:
    events = _events(session, item, histories)
    if events is None:
        return board.blocked_item_next_step(item.comments, item.assignee)
    return next_step_from_events(events)


def waits_for_a_person(session: Session, item: BacklogItem) -> bool:
    """Blocked, and nothing the worker does on its own moves it (frozen included),
    as ``carry_over.waits_for_a_person`` reads it from the thread."""
    return item.status == "blocked" and blocked_item_next_step(session, item) in (
        None,
        "frozen",
    )
