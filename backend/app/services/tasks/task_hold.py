"""What holds a task back, from its record (issue #766): the board's one answer.

The board showed a blocked task as blocked and, beside it, "waits for a
person" whenever the worker would not move it on its own, which included a
task that only waits for another task. ``hold_of`` names the hold instead,
from the same ``TaskRecord`` the worker decides with:

- ``frozen``: an operator froze it out of automation; a person unfreezes it.
- ``risk_hold``: approved, held for a person to accept the risk.
- ``dependency``: blocked or waiting until the tasks in ``waiting_for`` are
  done; the worker moves it when they are.
- ``retry_pending``: blocked by a failure the worker retries on its own.
- ``review_pending``: blocked with a delivery the worker reviews again.
- ``person``: blocked, and only a person moves it (``reason``: the latest
  verdict's text).
- ``run_window``: ready, held until the company's run window opens.

``None`` for a task nothing holds. ``waits_for_person`` is true for the holds
only a person releases (``frozen``, ``risk_hold``, ``person``).
"""

from __future__ import annotations

from typing import Any

from services.tasks.dependencies import BLOCKED_BY_DEPENDENCY_PREFIX
from services.tasks.task_projection import TaskRecord

FROZEN = "frozen"
RISK_HOLD = "risk_hold"
DEPENDENCY = "dependency"
RETRY_PENDING = "retry_pending"
REVIEW_PENDING = "review_pending"
PERSON = "person"
RUN_WINDOW = "run_window"
KINDS = (
    FROZEN,
    RISK_HOLD,
    DEPENDENCY,
    RETRY_PENDING,
    REVIEW_PENDING,
    PERSON,
    RUN_WINDOW,
)
PERSON_HOLDS = (FROZEN, RISK_HOLD, PERSON)
_RUN_WINDOW_KINDS = ("run_window_wait", "run_window_release")


def _kind(event: Any) -> str:
    if isinstance(event, dict):
        return str(event.get("event_type") or event.get("kind") or "")
    return str(getattr(event, "event_type", "") or "")


def run_window_open(events: list[Any]) -> bool:
    """The task's latest run-window event is a wait (``run_window_gate``)."""
    kinds = [_kind(e) for e in events if _kind(e) in _RUN_WINDOW_KINDS]
    return bool(kinds) and kinds[-1] == "run_window_wait"


def hold_of(
    record: TaskRecord,
    status: str | None,
    waiting_for: list[int],
    events: list[Any] | None = None,
) -> dict[str, Any] | None:
    """The hold on a task with *status*, or ``None``; *waiting_for* are its
    dependencies not done (``dependencies.waiting_for``), *events* all of its
    events (the run window's are read from them, whatever the thread)."""
    if status == "blocked":
        step = record.next_step()
        if step == "frozen":
            return {"kind": FROZEN}
        if record.awaits_risk_acceptance():
            return {"kind": RISK_HOLD, "reason": record.risk_hold_note()}
        if record.held_by_dependency(BLOCKED_BY_DEPENDENCY_PREFIX):
            return {"kind": DEPENDENCY, "waiting_for": list(waiting_for)}
        if step == "retry":
            return {"kind": RETRY_PENDING, "reason": record.block_reason()}
        if step == "review":
            return {"kind": REVIEW_PENDING}
        return {"kind": PERSON, "reason": record.block_reason()}
    if status == "todo":
        if waiting_for:
            return {"kind": DEPENDENCY, "waiting_for": list(waiting_for)}
        if run_window_open(events or []):
            return {"kind": RUN_WINDOW}
    return None


def waits_for_person(hold: dict[str, Any] | None) -> bool:
    """Only a person releases *hold*."""
    return hold is not None and hold["kind"] in PERSON_HOLDS
