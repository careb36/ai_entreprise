"""The agent turns this process is running now, by sprint (issue #766).

The task worker enters ``running`` around an assignee's run of a claimed
task (its model turns, tool calls and test runs) and around QA's review of a
delivery, and leaves it when the run returns or raises. ``of_sprint`` is what
the Live page's "working" reads (``live_state``): an entry exists only while
the call is on this process's stack, so a crash, a cancellation or a closed
sprint leaves nothing behind.

The table is this process's memory, as ``daemon_liveness`` is: another
replica's run of a task is seen through the task's lock instead
(``live_state``), which every replica holds while it works on a task.
"""

from __future__ import annotations

import threading
from collections.abc import Iterator
from contextlib import contextmanager
from datetime import UTC, datetime
from itertools import count
from typing import Any

# The assignee's run of a claimed task, and QA's review of a delivery.
TASK = "task"
REVIEW = "review"

_lock = threading.Lock()
_runs: dict[int, dict[str, Any]] = {}
_ids = count(1)


@contextmanager
def running(
    sprint_id: Any, task_id: Any, role: str | None, kind: str = TASK
) -> Iterator[None]:
    """Registered while the body runs; removed however it ends."""
    if sprint_id is None or task_id is None:
        yield
        return
    with _lock:
        key = next(_ids)
        _runs[key] = {
            "sprint_id": int(sprint_id),
            "task_id": int(task_id),
            "role": str(role or ""),
            "kind": kind,
            "since": datetime.now(UTC).isoformat(),
        }
    try:
        yield
    finally:
        with _lock:
            _runs.pop(key, None)


def of_sprint(sprint_id: int) -> list[dict[str, Any]]:
    """The runs of *sprint_id* in flight in this process, oldest first."""
    with _lock:
        return [dict(r) for r in _runs.values() if r["sprint_id"] == sprint_id]


def reset() -> None:
    """Forget every run (tests)."""
    with _lock:
        _runs.clear()
