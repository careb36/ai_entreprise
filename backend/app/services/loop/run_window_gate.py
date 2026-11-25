"""The run window applied: work that can wait starts in the company's window
(issue #736; the window is ``services/contracts/run_window.py``, the
company's setting ``services/platform/run_window_setting.py``).

What it holds back, outside the window and unless the sprint is ``urgent``:

- starting a sprint (``POST /v1/start_sprint``, ``/start``, ``/resume``,
  ``/dispatch``): its ready tasks are not queued and its kickoff message
  waits in the outbox (``HELD_OUTBOX``);
- the task worker picking up a task's next model turn: the claim
  (``worker/intake._claim``), the dispatch of ``todo`` tasks and QA's review
  of a delivered task (``worker/review``) ask ``gate_task`` first.

A model call already running is never cut: the window only gates starts.

Each hold is the task's ``run_window_wait`` event and each release its
``run_window_release`` event, by the System, carrying the window, its zone
and the time the backend computed (``until``, ``released_at``): an
observation of the backend's own clock, so the hold shows in the record. A
task has at most one open wait. ``sprints.run_window_wait_until`` says a
sprint has work held and when it is due; ``worker`` (a lifecycle worker)
wakes at the earliest of those, never more often than once a minute, and
releases each due sprint under ``locks.RUN_WINDOW_RELEASE`` so one replica
does it. A released sprint starts as if a Manager had started it then: its
kickoff goes out and its ready ``todo`` tasks are queued. Everything it reads
is in the database, so a restart mid-wait releases on time (or at once,
when the window opened while the backend was down).

A Manager marks a sprint ``urgent`` (``set_urgent``) to bypass the window,
audited as ``sprint_urgent_changed`` with the account; marking a waiting
sprint urgent releases it at once.
"""

from __future__ import annotations

import asyncio
from datetime import UTC, datetime
from typing import Any

from config import settings
from logging_config import setup_logger
from models import BacklogItem, OutboxEvent, Sprint, TaskEvent
from sqlmodel import Session, select

from services.contracts import run_window
from services.loop import sprint_control
from services.platform import locks
from services.platform import run_window_setting as setting
from services.platform.agent_service import AgentService
from services.tasks import dependencies, task_events

logger = setup_logger("fabrique-run-window")

WAIT = "run_window_wait"
RELEASE = "run_window_release"
HELD_OUTBOX = "held_for_run_window"
URGENT_AUDIT_ACTION = "sprint_urgent_changed"
MIN_SLEEP_SECONDS = 60
RUNNING = (sprint_control.ACTIVE, "needs_split")


def utc_now() -> datetime:
    """The backend's clock; the tests replace it."""
    return datetime.now(UTC)


def _aware(value: datetime | None) -> datetime | None:
    if value is None:
        return None
    return value.replace(tzinfo=UTC) if value.tzinfo is None else value.astimezone(UTC)


def _naive(value: datetime) -> datetime:
    return value.astimezone(UTC).replace(tzinfo=None)


def _window(session: Session, sprint: Sprint) -> dict[str, Any] | None:
    return setting.stored(session, sprint.company_id)


def hold_until(
    session: Session, sprint: Sprint, now: datetime | None = None
) -> datetime | None:
    """When the window opens for *sprint*'s work, or ``None``: it may start now."""
    if sprint.urgent:
        return None
    window = _window(session, sprint)
    if window is None:
        return None
    now = now or utc_now()
    holidays = setting.holidays_for(window)
    if run_window.is_open(window, now, holidays):
        return None
    # A valid window opens within the horizon; none found never holds work.
    return run_window.next_open(window, now, holidays)


def view(session: Session, sprint: Sprint) -> dict[str, Any] | None:
    """What the board and the inbox show of a hold, or ``None``."""
    until = _aware(sprint.run_window_wait_until)
    if until is None:
        return None
    window = _window(session, sprint)
    zone = window["timezone"] if window else "UTC"
    return {
        "waiting": True,
        "until": until.isoformat(),
        "until_local": run_window.local_iso({"timezone": zone}, until),
        "timezone": zone,
        "urgent": bool(sprint.urgent),
    }


def _open_waits(session: Session, sprint_id: int) -> dict[int, TaskEvent]:
    """Each task of the sprint whose latest run-window event is a wait."""
    last: dict[int, TaskEvent] = {}
    for event in session.exec(
        select(TaskEvent)
        .where(
            TaskEvent.sprint_id == sprint_id,
            TaskEvent.event_type.in_((WAIT, RELEASE)),  # type: ignore[attr-defined]
        )
        .order_by(TaskEvent.id)
    ).all():
        last[event.task_id] = event
    return {task: e for task, e in last.items() if e.event_type == WAIT}


def _has_open_wait(session: Session, item_id: int) -> bool:
    latest = session.exec(
        select(TaskEvent)
        .where(
            TaskEvent.task_id == item_id,
            TaskEvent.event_type.in_((WAIT, RELEASE)),  # type: ignore[attr-defined]
        )
        .order_by(TaskEvent.id.desc())  # type: ignore[union-attr]
    ).first()
    return latest is not None and latest.event_type == WAIT


def defer_task(
    session: Session,
    item: BacklogItem,
    sprint: Sprint,
    until: datetime,
    now: datetime | None = None,
) -> bool:
    """Record that *item* waits for the window (once per wait); the caller commits."""
    now = now or utc_now()
    current = _aware(sprint.run_window_wait_until)
    if current is None or current != until:
        sprint.run_window_wait_until = _naive(until)
        session.add(sprint)
    if _has_open_wait(session, item.id):
        return False
    window = _window(session, sprint)
    zone = window["timezone"] if window else "UTC"
    local = run_window.local_iso({"timezone": zone}, until)
    task_events.record(
        session,
        item,
        WAIT,
        task_events.SYSTEM,
        text=(
            f"Waiting for the run window until {local[11:16]} "
            f"({zone}, {local[:10]}): work that can wait starts "
            "in the company's window."
        ),
        payload={
            "window": run_window.describe(window) if window else None,
            "timezone": zone,
            "until": until.isoformat(),
            "until_local": local,
            "observed_at": _aware(now).isoformat(),
            "clock": "backend",
        },
        company_id=sprint.company_id,
    )
    return True


def gate_task(
    session: Session, item: BacklogItem, sprint: Sprint, now: datetime | None = None
) -> bool:
    """``True`` when *item*'s next model turn waits for the window (recorded)."""
    until = hold_until(session, sprint, now)
    if until is None:
        return False
    defer_task(session, item, sprint, until, now)
    return True


def hold_task(engine: Any, item_id: int, now: datetime | None = None) -> bool:
    """``gate_task`` in its own session, for the worker's claim."""
    with Session(engine) as session:
        item = session.get(BacklogItem, item_id)
        sprint = session.get(Sprint, item.sprint_id) if item else None
        if item is None or sprint is None:
            return False
        held = gate_task(session, item, sprint, now)
        session.commit()
        return held


def dispatch_or_defer(
    session: Session, sprint: Sprint, now: datetime | None = None
) -> dict[str, Any]:
    """Queue *sprint*'s ready ``todo`` tasks, or hold them for the window."""
    until = hold_until(session, sprint, now)
    if until is None:
        return {
            "dispatched": sprint_control.dispatch_todo(session, sprint),
            "run_window": None,
        }
    for item in session.exec(
        select(BacklogItem).where(
            BacklogItem.sprint_id == sprint.id, BacklogItem.status == "todo"
        )
    ).all():
        if dependencies.is_ready(session, item):
            defer_task(session, item, sprint, until, now)
    sprint.run_window_wait_until = _naive(until)
    session.add(sprint)
    session.flush()
    return {"dispatched": 0, "run_window": view(session, sprint)}


def release_sprint(
    session: Session, sprint: Sprint, reason: str, now: datetime | None = None
) -> int:
    """Release *sprint*'s held work: events, the kickoff, its ready tasks; the count."""
    now = _aware(now or utc_now())
    window = _window(session, sprint)
    zone = window["timezone"] if window else "UTC"
    waited = _aware(sprint.run_window_wait_until)
    at = run_window.local_iso({"timezone": zone}, now)[11:16]
    texts = {
        "window_open": f"Released by the run window at {at} ({zone}).",
        "urgent": "Released: a Manager marked the sprint urgent; the run window is bypassed.",
        "window_removed": "Released: the company's run window was turned off or changed.",
    }
    released = 0
    for task_id in _open_waits(session, sprint.id):
        item = session.get(BacklogItem, task_id)
        if item is None:
            continue
        task_events.record(
            session,
            item,
            RELEASE,
            task_events.SYSTEM,
            text=texts.get(reason, texts["window_open"]),
            payload={
                "reason": reason,
                "window": run_window.describe(window) if window else None,
                "timezone": zone,
                "waited_until": waited.isoformat() if waited else None,
                "released_at": now.isoformat(),
                "clock": "backend",
            },
            company_id=sprint.company_id,
        )
        released += 1
    sprint.run_window_wait_until = None
    session.add(sprint)
    for row in session.exec(
        select(OutboxEvent).where(
            OutboxEvent.aggregate_type == "sprint",
            OutboxEvent.aggregate_id == str(sprint.id),
            OutboxEvent.status == HELD_OUTBOX,
        )
    ).all():
        row.status = "pending"
        session.add(row)
    if sprint.status in RUNNING:
        sprint_control.dispatch_todo(session, sprint)
    return released


def _release_one(engine: Any, sprint_id: int, now: datetime) -> bool:
    with Session(engine) as session:
        sprint = session.get(Sprint, sprint_id)
        if sprint is None or sprint.run_window_wait_until is None:
            return False
        if sprint.status not in RUNNING:
            if sprint.status in ("completed", sprint_control.CANCELLED):
                sprint.run_window_wait_until = None
                session.add(sprint)
                session.commit()
            return False  # planned or paused: its start or resume gates again
        until = hold_until(session, sprint, now)
        if until is None:
            if stuck_block_reason(session, sprint, now) is not None:
                return False  # the run may still finish; leave the sprint waiting
            if sprint.urgent:
                reason = "urgent"
            elif _window(session, sprint) is None:
                reason = "window_removed"
            else:
                reason = "window_open"
            release_sprint(session, sprint, reason, now)
            session.commit()
            return True
        if _aware(sprint.run_window_wait_until) != until:
            sprint.run_window_wait_until = _naive(until)  # the window changed
            session.add(sprint)
            session.commit()
        return False


def release_due(engine: Any, now: datetime | None = None) -> list[int]:
    """Release every held sprint whose window is open; the ids released."""
    now = now or utc_now()
    with Session(engine) as session:
        ids = list(
            session.exec(
                select(Sprint.id).where(Sprint.run_window_wait_until.is_not(None))  # type: ignore[union-attr]
            ).all()
        )
    released = []
    for sprint_id in ids:
        with locks.held(engine, locks.RUN_WINDOW_RELEASE, sprint_id) as mine:
            if mine and _release_one(engine, sprint_id, now):
                released.append(sprint_id)
    return released


def seconds_until_next_look(engine: Any, now: datetime | None = None) -> float:
    """How long the worker sleeps: to the earliest hold, within its bounds."""
    now = _aware(now or utc_now())
    longest = max(MIN_SLEEP_SECONDS, int(settings.RUN_WINDOW_RECHECK_SECONDS))
    with Session(engine) as session:
        dues = [
            _aware(d)
            for d in session.exec(
                select(Sprint.run_window_wait_until).where(
                    Sprint.run_window_wait_until.is_not(None)  # type: ignore[union-attr]
                )
            ).all()
        ]
    wait = float(longest)
    for due in dues:
        wait = min(wait, (due - now).total_seconds())
    return max(float(MIN_SLEEP_SECONDS), wait)


async def worker(engine: Any) -> None:
    """The lifecycle worker: release due sprints, then sleep to the next boundary."""
    while True:
        try:
            released = await asyncio.to_thread(release_due, engine)
            if released:
                logger.info("Run window released sprints %s", released)
            pause = await asyncio.to_thread(seconds_until_next_look, engine)
        except Exception as exc:  # noqa: BLE001 - Keep the worker alive; look again later.
            logger.warning("Run window release failed: %s", exc)
            pause = float(MIN_SLEEP_SECONDS)
        await asyncio.sleep(pause)


def set_urgent(
    session: Session,
    sprint: Sprint,
    urgent: bool,
    *,
    by: str,
    user_id: int | None,
    token_id: int | None,
    now: datetime | None = None,
) -> dict[str, Any]:
    """Mark or unmark *sprint* urgent (a Manager's, checked by the caller); audited.

    Marking a sprint whose work waits for the window releases it at once.
    The caller commits.
    """
    before = bool(sprint.urgent)
    released = 0
    if before != urgent:
        sprint.urgent = urgent
        session.add(sprint)
        AgentService().audit_interaction(
            session=session,
            company_id=sprint.company_id,
            interaction_id=f"sprint:{sprint.id}:admin",
            agent_id=f"human:{by}",
            action=URGENT_AUDIT_ACTION,
            details={
                "sprint_id": sprint.id,
                "user_id": user_id,
                "token_id": token_id,
                "waiting": sprint.run_window_wait_until is not None,
            },
            result={"from": before, "to": urgent},
            decision_signature=None,
            cost_tokens=0,
        )
        if urgent and sprint.run_window_wait_until is not None:
            released = release_sprint(session, sprint, "urgent", now)
    return {
        "sprint_id": sprint.id,
        "urgent": bool(sprint.urgent),
        "released_tasks": released,
        "run_window": view(session, sprint),
    }


def actor_fields(actor: Any) -> dict[str, Any]:
    """``by``, ``user_id`` and ``token_id`` of a person (``sod.actor_of``) for the audit."""
    return {
        "by": (getattr(actor, "label", "") or "") or "A person",
        "user_id": getattr(actor, "user_id", None),
        "token_id": getattr(actor, "token_id", None),
    }


def start_new(
    session: Session,
    sprint: Sprint,
    urgent: bool,
    actor: Any,
    now: datetime | None = None,
) -> datetime | None:
    """A new sprint: its urgent mark (audited), then when its work may start."""
    if urgent:
        set_urgent(session, sprint, True, now=now, **actor_fields(actor))
    return hold_until(session, sprint, now)


# ---------------------------------------------------------------------------
# Stuck async-task dispatch gate
# ---------------------------------------------------------------------------

# Statuses that mean a task's async run may still be working. A task sitting in
# one of these for longer than ``RUN_WINDOW_STUCK_TASK_SECONDS`` blocks new
# task dispatch for the same sprint.
STUCK_TASK_STATUSES = ["in_progress", "in_review"]


def _as_naive_utc(value: datetime) -> datetime:
    """Return *value* as a naive UTC datetime for comparing model values."""
    if value.tzinfo is not None:
        return value.astimezone(UTC).replace(tzinfo=None)
    return value


def stuck_block_reason(
    session: Session, sprint: Sprint, now: datetime | None = None
) -> str | None:
    """Why new tasks for *sprint* are blocked by a stuck async task, or ``None``.

    A prior task in ``STUCK_TASK_STATUSES`` whose ``updated_at`` (or
    ``created_at``) is older than ``RUN_WINDOW_STUCK_TASK_SECONDS`` is stuck:
    its async run has not reached a terminal state in the configured time.
    Setting the threshold to 0 disables the gate.
    """
    from config import settings

    threshold = settings.RUN_WINDOW_STUCK_TASK_SECONDS
    if threshold <= 0:
        return None
    now_naive = _as_naive_utc(now or utc_now())
    stuck = []
    for item in session.exec(
        select(BacklogItem).where(
            BacklogItem.sprint_id == sprint.id,
            BacklogItem.status.in_(STUCK_TASK_STATUSES),  # type: ignore[union-attr]
        )
    ).all():
        changed_at = item.updated_at or item.created_at
        if changed_at is None:
            continue
        age = (now_naive - _as_naive_utc(changed_at)).total_seconds()
        if age > threshold:
            stuck.append((item.id, item.status, age))
    if not stuck:
        return None
    details = ", ".join(
        f"task {item_id} ({status}) for {int(age)}s"
        for item_id, status, age in stuck
    )
    return (
        f"Dispatch blocked: prior async task(s) stuck beyond "
        f"{threshold}s: {details}"
    )


_original_release_sprint = release_sprint


def release_sprint(
    session: Session,
    sprint: Sprint,
    reason: str,
    now: datetime | None = None,
) -> int:
    """Release *sprint* unless a stuck async task blocks new dispatch."""
    blocked = stuck_block_reason(session, sprint, now)
    if blocked is not None:
        # The caller commits; nothing was queued, so the reason is the only
        # outward signal and upstream code keeps the sprint waiting.
        return 0
    return _original_release_sprint(session, sprint, reason, now)
