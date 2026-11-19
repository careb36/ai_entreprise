"""
The board's questions answered from a task's events (issue #324).

``services/task_events.record`` writes every routed transition as an event
and renders it as the board comment. Here the same questions
``services/tasks/board.py`` answers from the thread (what is pending, why the task
is blocked, whether it waits for a person) are answered from the events, by
their kind, actor, verdict and flags, never by reading comment text.

The events answer only for a task whose thread they cover: every comment
carries the id of one of the task's events, and the migration found nothing it
could not read back (no ``legacy`` event). Any other task (a thread written
before events existed, or by a writer not routed yet) is read from its
comments as before, so a task never gets a half-informed answer.

``TaskRecord`` is what a decision reads: the task's comments and events, the
source picked once, every question answered from it. The worker, the
dependencies, the carry-over, the ScrumMaster's triage and the procedures'
numbers decide through it; a loop over many tasks loads their events in one
query (``records_for``). An event may be a ``TaskEvent`` row or its
``task_events.as_dict`` form (the worker carries the latter in its task dict).
The ``*_from_events`` functions are the projections themselves.
"""

from __future__ import annotations

from datetime import UTC, datetime
from typing import Any

from models import BacklogItem, TaskEvent
from sqlmodel import Session

from services.tasks import board, task_events

# Events that never say anything about the task's own state: lineage, advice,
# the System's integration note (it moves branches, not the task) and the
# sprint board's notes (claims left for the other tasks, issue #439) and the
# relations between tasks (issue #533) and the
# effort observed at sprint close (issue #534) and the run window's holds and
# releases (issue #736: when work may start, never what the task is).
_ADVICE_KINDS = {
    "carried_over",
    "advised",
    "integration",
    "board_note",
    "board_note_rejected",
    "relation",
    "effort_observed",
    "run_window_wait",
    "run_window_release",
}
# A dependency hold (``services/dependencies.py``) says so in its payload.
DEPENDENCY_HOLD = "dependency"

Event = TaskEvent | dict[str, Any]


# --- One event, row or dict ---------------------------------------------------


def _get(event: Event, name: str) -> Any:
    if isinstance(event, dict):
        return event.get(name)
    return getattr(event, name, None)


def _kind(event: Event) -> str:
    return str(_get(event, "event_type") or _get(event, "kind") or "")


def _actor(event: Event) -> str:
    return str(_get(event, "actor") or "")


def _payload(event: Event) -> dict[str, Any]:
    return _get(event, "payload") or {}


def _text(event: Event) -> str:
    return str(_payload(event).get("text") or "").strip()


def _author(event: Event) -> str:
    return str(_payload(event).get("author") or "")


def _created_at(event: Event) -> datetime | None:
    value = _get(event, "created_at")
    if isinstance(value, datetime) or value is None:
        return value
    try:
        return datetime.fromisoformat(str(value))
    except ValueError:
        return None


def _verdict_events(events: list[Event]) -> list[Event]:
    """The events that may say something about this task's state."""
    return [e for e in events if _kind(e) not in _ADVICE_KINDS]


def _needs_work(event: Event) -> bool:
    return _payload(event).get("verdict") == "needs_work"


def _is_person_send_back(event: Event) -> bool:
    # A person's, or the Manager agent's on the client's behalf (issue #394).
    return _kind(event) == "sent_back" and (
        _actor(event) == task_events.PERSON
        or _actor(event).lower() == board.MANAGER_AGENT_AUTHOR
    )


def _since_send_back(events: list[Event]) -> list[Event]:
    for index in range(len(events) - 1, -1, -1):
        if _is_person_send_back(events[index]):
            return events[index + 1 :]
    return events


def _carry_direction(event: Event) -> str | None:
    """``to`` or ``from``: the payload's, else (an event migration 0014
    backfilled from a carry-over comment) the rendered lineage prefix."""
    direction = _payload(event).get("direction")
    if direction:
        return str(direction)
    if _text(event).startswith(board.CARRIED_TO_PREFIX):
        return "to"
    if _text(event).startswith(board.CARRIED_FROM_PREFIX):
        return "from"
    return None


# --- Coverage -----------------------------------------------------------------


def covers_thread(comments: Any, events: list[Event]) -> bool:
    """Whether *events* render the whole thread *comments*."""
    comments = board.comment_list(comments)
    if not comments or not events:
        return False
    if any(_kind(e) == "legacy" for e in events):
        return False
    ids = {_get(e, "id") for e in events}
    return all(c.get("event_id") in ids for c in comments)


def covers(item: BacklogItem, events: list[Event]) -> bool:
    """Whether *events* answer for *item*: they render its whole thread."""
    return covers_thread(item.comments, events)


# --- The projections ------------------------------------------------------------


def status_from_events(events: list[Event], default: str = "todo") -> str:
    """The status the latest transition that set one left."""
    for event in reversed(events):
        if _get(event, "task_status"):
            return str(_get(event, "task_status"))
    return default


def pending_rework_from_events(events: list[Event]) -> list[str]:
    """The rework requests made since the latest deliverable."""
    feedback: list[str] = []
    for event in reversed(events):
        if not _text(event):
            continue
        if _needs_work(event):
            feedback.append(f"{_author(event) or 'Reviewer'}: {_text(event)}")
        elif _kind(event) == "delivered":
            break
    return list(reversed(feedback))


def _is_deliverable(event: Event, assignee: str | None) -> bool:
    payload = _payload(event)
    if _kind(event) != "delivered" or payload.get("frozen"):
        return False
    if payload.get("verdict") == "approved":
        return False
    # QA's words are a review unless QA owns the task (its acceptance tests).
    qa_owned = (assignee or "").strip().lower() == "qa"
    return qa_owned or _author(event).strip().lower() != "qa"


def latest_deliverable_from_events(
    events: list[Event], assignee: str | None = None
) -> str:
    """The deliverable to review, or empty when a rework request came after it."""
    for event in reversed(events):
        if not _text(event):
            continue
        if _needs_work(event):
            return ""
        if _is_deliverable(event, assignee):
            return _text(event)
    return ""


def _is_verdict(event: Event) -> bool:
    return bool(_text(event)) and (
        _actor(event) in (task_events.SYSTEM, task_events.GATE) or _needs_work(event)
    )


def block_reason_from_events(events: list[Event]) -> str:
    """The latest verdict: what a gate, the system or a reviewer said."""
    for event in reversed(_verdict_events(events)):
        if _is_verdict(event):
            return _text(event)
    return ""


def risk_hold_from_events(events: list[Event]) -> Event | None:
    """The hold event when the latest verdict is a high-risk hold, else ``None``."""
    for event in reversed(_verdict_events(events)):
        if _is_verdict(event) or _is_person_send_back(event):
            return event if _kind(event) == "risk_hold" else None
    return None


def awaits_risk_acceptance_from_events(events: list[Event]) -> bool:
    """The latest verdict is a high-risk hold, approved and waiting for a person."""
    return risk_hold_from_events(events) is not None


def frozen_from_events(events: list[Event]) -> bool:
    """An operator froze the task out of automation."""
    return any(_payload(e).get("frozen") for e in _verdict_events(events))


def next_step_from_events(
    events: list[Event], assignee: str | None = None
) -> str | None:
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
        and latest_deliverable_from_events(events, assignee)
    ):
        return "review"
    if any(f.get("needs_person") for f in flags):
        return None
    if not any(f.get("retryable") for f in flags):
        return None
    return None if sum(1 for f in flags if f.get("retry")) >= 1 else "retry"


def rework_requests_by_from_events(events: list[Event], author: str) -> int:
    """The rework requests *author* made since a person last sent the task back."""
    return sum(
        1
        for e in _since_send_back(events)
        if _needs_work(e) and _author(e) == author and _text(e)
    )


# --- The record a decision reads ------------------------------------------------


class TaskRecord:
    """A task's comments and events; the events answer when they cover the thread."""

    def __init__(
        self,
        comments: Any,
        events: list[Event] | None = None,
        assignee: str | None = None,
    ):
        self.comments = board.comment_list(comments)
        self.assignee = assignee
        events = list(events or [])
        self.events: list[Event] | None = (
            events if covers_thread(self.comments, events) else None
        )

    @property
    def source(self) -> str:
        return "events" if self.events is not None else "comments"

    def frozen(self) -> bool:
        if self.events is None:
            return board.has_manual_freeze_marker(self.comments)
        return frozen_from_events(self.events)

    def deliverable(self) -> str:
        if self.events is None:
            return board.extract_latest_reviewable_comment(self.comments, self.assignee)
        return latest_deliverable_from_events(self.events, self.assignee)

    def pending_rework_feedback(self) -> list[str]:
        if self.events is None:
            return board.pending_rework_feedback(self.comments)
        return pending_rework_from_events(self.events)

    def rework_requests_by(self, author: str) -> int:
        if self.events is None:
            return board.rework_requests_by(self.comments, author)
        return rework_requests_by_from_events(self.events, author)

    def rework_request_count(self) -> int:
        """Every rework request on the task, whoever made it."""
        if self.events is None:
            return board.rework_request_count(self.comments)
        return sum(
            1 for e in _verdict_events(self.events) if _needs_work(e) and _text(e)
        )

    def review_failures(self) -> int:
        """How many QA reviews failed (the model call, not the verdict)."""
        if self.events is None:
            return sum(
                1
                for c in self.comments
                if c.get("author") == "System"
                and "qa review failed" in str(c.get("text", "")).lower()
            )
        return sum(1 for e in self.events if _kind(e) == "review_failed")

    def next_step(self) -> str | None:
        if self.events is None:
            return board.blocked_item_next_step(self.comments, self.assignee)
        return next_step_from_events(self.events, self.assignee)

    def block_reason(self) -> str:
        if self.events is None:
            return board.block_reason(self.comments)
        return block_reason_from_events(self.events)

    def awaits_risk_acceptance(self) -> bool:
        if self.events is None:
            return board.awaits_risk_acceptance(self.comments)
        return awaits_risk_acceptance_from_events(self.events)

    def risk_hold_note(self) -> str:
        """The text of the hold behind ``awaits_risk_acceptance`` (its reasons
        say whether it is autonomy level 1's or a high tier), else ``""``."""
        if self.events is None:
            return board.risk_hold_note(self.comments)
        hold = risk_hold_from_events(self.events)
        return _text(hold) if hold is not None else ""

    def is_settled(self, status: str | None) -> bool:
        """Done, or blocked until a person acts (``board.item_is_settled``)."""
        if status == "done":
            return True
        return status == "blocked" and self.next_step() is None

    def waits_for_a_person(self, status: str | None) -> bool:
        """Blocked, and nothing the worker does on its own moves it (frozen included)."""
        return status == "blocked" and self.next_step() in (None, "frozen")

    def rework_since_send_back(self) -> list[tuple[str, str]]:
        """``(author, text)`` of every rework request since a person's last send-back."""
        if self.events is None:
            return [
                (str(c.get("author") or ""), str(c.get("text", "")).strip())
                for c in board.since_send_back(self.comments)
                if board.is_rework_request(c)
            ]
        return [
            (_author(e), _text(e))
            for e in _since_send_back(self.events)
            if _needs_work(e) and _text(e)
        ]

    def last_send_back_at(self) -> datetime | None:
        """When a person last sent the task back to work, or ``None``."""
        if self.events is None:
            return board.last_send_back_at(self.comments)
        for event in reversed(self.events):
            if _is_person_send_back(event):
                return _created_at(event)
        return None

    def carried(self, direction: str) -> bool:
        """Carried into another sprint (``to``) or itself a second attempt (``from``)."""
        if self.events is None:
            prefix = (
                board.CARRIED_TO_PREFIX
                if direction == "to"
                else board.CARRIED_FROM_PREFIX
            )
            return any(str(c.get("text", "")).startswith(prefix) for c in self.comments)
        return any(
            _kind(e) == "carried_over" and _carry_direction(e) == direction
            for e in self.events
        )

    def carry_mark(self, mark_prefix: str, unmark_prefix: str) -> bool | None:
        """The Manager agent's carry-over mark: ``True`` marked, ``False`` a
        person took it back, ``None`` never marked (``carry_over.carry_mark``).

        From the events, the mark's payload says so; an event written before
        the payload carried it is read by its actor and rendered prefix.
        """
        state: bool | None = None
        if self.events is None:
            for comment in self.comments:
                author = str(comment.get("author") or "").strip().lower()
                text = str(comment.get("text") or "")
                if author == board.MANAGER_AGENT_AUTHOR and text.startswith(
                    mark_prefix
                ):
                    state = True
                elif author == "user" and text.startswith(unmark_prefix):
                    state = False
            return state
        for event in self.events:
            mark = _payload(event).get("carry_mark")
            if mark is not None:
                state = bool(mark)
            elif _actor(event).lower() == board.MANAGER_AGENT_AUTHOR and _text(
                event
            ).startswith(mark_prefix):
                state = True
            elif _actor(event) == task_events.PERSON and _text(event).startswith(
                unmark_prefix
            ):
                state = False
        return state

    def qa_approved(self) -> bool | None:
        """QA's latest verdict is an approval with no rework after it, from the
        events; ``None`` when the comments must answer."""
        if self.events is None:
            return None
        for event in reversed(self.events):
            if _needs_work(event) and _text(event):
                return False
            if _payload(event).get("verdict") == "approved" and _actor(
                event
            ).lower() not in (
                task_events.SYSTEM,
                task_events.GATE,
                task_events.PERSON,
                board.MANAGER_AGENT_AUTHOR,
            ):
                return True
        return False

    def person_since(self, since: datetime) -> bool | None:
        """A person wrote on the task at or after *since* (naive UTC), from the
        events; ``None`` when the comments must answer."""
        if self.events is None:
            return None
        for event in self.events:
            if _actor(event) != task_events.PERSON or not _text(event):
                continue
            moment = _created_at(event)
            if moment is not None and moment.tzinfo is not None:
                moment = moment.astimezone(UTC).replace(tzinfo=None)
            if moment is None or moment >= since:
                return True
        return False

    def held_by_dependency(self, prefix: str) -> bool:
        """The System's latest note is a dependency hold.

        From the events, the hold's payload says so; an event written before
        the payload carried it is a ``blocked`` event rendered with *prefix*.
        """
        if self.events is None:
            notes = [
                c
                for c in self.comments
                if str(c.get("author") or "").strip().lower() == "system"
                and not board.is_integration_note(c)
            ]
            return bool(notes) and str(notes[-1].get("text") or "").startswith(prefix)
        notes = [
            e
            for e in self.events
            if _actor(e) == task_events.SYSTEM
            and _text(e)
            and _kind(e) != "integration"
        ]
        if not notes:
            return False
        last = notes[-1]
        hold = _payload(last).get("hold")
        if hold is not None:
            return hold == DEPENDENCY_HOLD
        return _kind(last) == "blocked" and _text(last).startswith(prefix)


# Histories loaded at once (``task_events.for_tasks``), by task id; a caller
# answering for many tasks passes them so no question queries per task.
Histories = dict[int, list[Event]]


def histories_for(session: Session, items: list[BacklogItem]) -> Histories:
    """The events of *items* in one query, as plain dicts.

    Dicts, not rows: a loop that commits after each task expires the rows the
    session holds, and reading an expired row queries it again.
    """
    loaded = task_events.for_tasks(
        session, [item.id for item in items if item.id is not None]
    )
    return {
        task_id: [task_events.as_dict(e) for e in events]
        for task_id, events in loaded.items()
    }


def record_of(
    session: Session, item: BacklogItem, histories: Histories | None = None
) -> TaskRecord:
    """The record of *item*, its events read now unless *histories* has them."""
    if item.id is None:
        return TaskRecord(item.comments, [], item.assignee)
    if histories is not None and item.id in histories:
        events = histories[item.id]
    else:
        events = task_events.for_task(session, item.id)
    return TaskRecord(item.comments, events, item.assignee)


def records_for(session: Session, items: list[BacklogItem]) -> dict[int, TaskRecord]:
    """The records of *items*, their events loaded in one query."""
    histories = histories_for(session, items)
    return {
        item.id: TaskRecord(item.comments, histories.get(item.id, []), item.assignee)
        for item in items
        if item.id is not None
    }


def snapshot(session: Session, item: BacklogItem) -> list[dict[str, Any]]:
    """*item*'s events as plain dicts, for a task dict that outlives the session."""
    if item.id is None:
        return []
    return [task_events.as_dict(e) for e in task_events.for_task(session, item.id)]


def record_from_dict(item: dict[str, Any]) -> TaskRecord:
    """The record of a task dict carrying ``comments`` and, when loaded, ``events``."""
    return TaskRecord(item.get("comments"), item.get("events"), item.get("assignee"))


# --- The questions, from the events when they cover the task -----------------


def source(session: Session, item: BacklogItem) -> str:
    """``events`` when the events answer for *item*, else ``comments``."""
    return record_of(session, item).source


def pending_rework_feedback(
    session: Session, item: BacklogItem, histories: Histories | None = None
) -> list[str]:
    return record_of(session, item, histories).pending_rework_feedback()


def block_reason(
    session: Session, item: BacklogItem, histories: Histories | None = None
) -> str:
    return record_of(session, item, histories).block_reason()


def awaits_risk_acceptance(
    session: Session, item: BacklogItem, histories: Histories | None = None
) -> bool:
    return record_of(session, item, histories).awaits_risk_acceptance()


def blocked_item_next_step(
    session: Session, item: BacklogItem, histories: Histories | None = None
) -> str | None:
    return record_of(session, item, histories).next_step()


def is_settled(
    session: Session, item: BacklogItem, histories: Histories | None = None
) -> bool:
    """Done, or blocked until a person acts: nothing the worker does moves it."""
    return record_of(session, item, histories).is_settled(item.status)


def waits_for_a_person(
    session: Session, item: BacklogItem, histories: Histories | None = None
) -> bool:
    """Blocked, and nothing the worker does on its own moves it (frozen included)."""
    return record_of(session, item, histories).waits_for_a_person(item.status)
