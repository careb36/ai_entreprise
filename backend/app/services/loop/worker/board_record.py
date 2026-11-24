"""The record tools' scopes and offer: which turns get ``board__task`` and
``board__evidence``, over which tasks (issue #766, family E).

``services/tools/board_tools.py`` holds the tools and
``services/tasks/task_records.py`` the readers (claims apart from
observations; re-exported here). ``offer`` puts the tools beside a turn's
toolbox, with a scope read from the database, never from the call's
arguments:

- the QA review of a delivery (``qa_review_reads``), the Architect's brief
  (``architect_brief``) and an execution turn on a rework (``execute_tools``):
  the turn's company and its own task plus the tasks it depends on
  (``task_scope``);
- the ScrumMaster's decision on a blocked task (``triage_reads``): the
  task's sprint (``sprint_scope``), as the design's table says;
- the PO's sprint plan (``planning/planning_reads``): the company's board
  (``board_tools.Scope`` without task ids), composed there from the same
  readers.

They go beside the turn's own tools (within its rounds) and never turn a
plain call into a tool loop: a turn offered no tools gets none.
"""

from __future__ import annotations

from collections.abc import Callable
from typing import Any

from config import settings
from logging_config import setup_logger

from services.tasks import task_records
from services.tasks.task_records import read_evidence, read_task
from services.tools import board_tools

logger = setup_logger("fabrique-task-worker")

__all__ = ["offer", "on_rework", "read_evidence", "read_task", "task_scope"]


def task_scope(engine: Any, company_id: int, item_id: int) -> board_tools.Scope | None:
    """The task and the tasks it depends on, when it is a task of the company."""
    ids = task_records.task_ids(engine, company_id, item_id)
    return board_tools.Scope(company_id, ids) if ids is not None else None


def sprint_scope(
    engine: Any, company_id: int, item_id: int
) -> board_tools.Scope | None:
    """Every task of the task's sprint, when it is a task of the company."""
    ids = task_records.sprint_task_ids(engine, company_id, item_id)
    return board_tools.Scope(company_id, ids) if ids is not None else None


def _toolbox(
    scope_of: Callable[[Any, int, int], board_tools.Scope | None],
    company_id: int | None,
    item_id: int | None,
    *,
    rounds: int,
    engine: Any,
) -> board_tools.BoardToolbox | None:
    if not settings.BOARD_TOOLS_ENABLED or rounds <= 0:
        return None
    if company_id is None or item_id is None:
        return None
    try:
        scope = scope_of(engine, company_id, item_id)
    except Exception as exc:  # noqa: BLE001 - Without a scope, no record tools.
        logger.warning("No record tools for item=%s: %s", item_id, exc)
        return None
    if scope is None:
        return None
    return board_tools.BoardToolbox(
        scope,
        lambda c, t, a: read_task(engine, c, t, a),
        lambda c, t, a: read_evidence(engine, c, t, a),
        rounds=rounds,
    )


def toolbox_for_task(
    company_id: int | None, item_id: int | None, *, rounds: int, engine: Any
) -> board_tools.BoardToolbox | None:
    """The record tools scoped to the task, or ``None`` when they are off,
    there are no rounds or the task is not the company's."""
    return _toolbox(task_scope, company_id, item_id, rounds=rounds, engine=engine)


def toolbox_for_sprint(
    company_id: int | None, item_id: int | None, *, rounds: int, engine: Any
) -> board_tools.BoardToolbox | None:
    """The record tools scoped to the task's sprint, or ``None`` as above."""
    return _toolbox(sprint_scope, company_id, item_id, rounds=rounds, engine=engine)


def offer(
    toolbox: Any,
    company_id: int | None,
    item_id: int | None,
    *,
    rounds: int,
    engine: Any,
    sprint: bool = False,
) -> Any:
    """*toolbox* with the record tools beside it (``board_tools.attach``),
    scoped to the task (or, with *sprint*, to its sprint). They go beside a
    turn's tools and never make a plain call a tool loop: without a toolbox
    (no daemon, no recorded delivery) there is none."""
    if toolbox is None:
        return None
    make = toolbox_for_sprint if sprint else toolbox_for_task
    board = make(company_id, item_id, rounds=rounds, engine=engine)
    return board_tools.attach(toolbox, board)


def on_rework(
    toolbox: Any,
    company_id: int | None,
    item: dict[str, Any],
    limits: dict[str, Any],
    engine: Any,
) -> Any:
    """An execution turn on a rework: its toolbox with the task's record tools,
    within the model's rounds and result budget (``model_profile``); none
    for a turn without tools, as in ``offer``."""
    if toolbox is None:
        return None
    board = toolbox_for_task(
        company_id,
        item.get("id"),
        rounds=int(limits.get("tool_rounds") or 0),
        engine=engine,
    )
    if board is not None and limits.get("result_max_chars"):
        board.result_max_chars = int(limits["result_max_chars"])
    return board_tools.attach(toolbox, board)
