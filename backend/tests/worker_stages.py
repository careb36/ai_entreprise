"""Point the task worker and its stage modules at a test's engine (ADR 0014).

``task_worker`` is being split into ``services/loop/worker/<stage>.py``; each
stage imports ``engine`` from ``database`` itself, so a test that runs the
worker on its own engine patches every one of them, and a stage never writes
to another database than the rest of the worker. Each stage's pull request
adds its module to ``STAGES``.
"""

from __future__ import annotations

from typing import Any

from services.loop import task_worker
from services.loop.worker import (
    board_writes,
    deliver,
    execute,
    messages,
    prompt,
    record,
)

STAGES = (task_worker, messages, board_writes, prompt, record, deliver, execute)


def use_engine(monkeypatch: Any, engine: Any) -> None:
    """``monkeypatch.setattr(<module>, "engine", engine)`` for the worker and each stage."""
    for module in STAGES:
        monkeypatch.setattr(module, "engine", engine)
