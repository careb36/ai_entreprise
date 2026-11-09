"""How each model did on the company's own tasks, read from the boards.

The model suggestions (``services/model_suggestions.py``) start from a fixed
rule over the catalogue; once the company has run a model on enough of its
tasks, how those tasks went decides instead. Every number here is counted
from what the loop recorded, never from what a model says about itself:

- which model delivered a task: the ``model`` of the task worker's latest
  ``AgentRun`` for it (``external_ref`` ``task-<id>``), the model the proxy
  reports it served, under the role of the agent that delivered it;
- how the task went: its status on the board of a completed sprint (done or
  not) and its ``NEEDS WORK:`` requests (``board.rework_request_count``, the
  number the sprint list and the procedures use).

A task counts once, for the model of its last delivery: an earlier attempt
by another model is not credited with the outcome.
"""

from __future__ import annotations

from typing import Any

from models import BacklogItem, Sprint
from models.agent_run import TASK_WORKER_PRODUCER, AgentRun
from sqlmodel import Session, select

from services.models import company_models
from services.tasks.board import rework_request_count

# The fewest of the company's tasks a model must have delivered, in a role,
# before its record outweighs the catalogue rule.
MIN_TASKS = 3


def _task_id(external_ref: str) -> int | None:
    prefix, _, number = (external_ref or "").partition("-")
    return int(number) if prefix == "task" and number.isdigit() else None


def record(session: Session, company_id: int) -> dict[tuple[str, str], dict[str, int]]:
    """``{(role, model): {"tasks", "done", "rework"}}`` over completed sprints."""
    last: dict[int, AgentRun] = {}
    runs = session.exec(
        select(AgentRun)
        .where(
            AgentRun.company_id == company_id,
            AgentRun.producer == TASK_WORKER_PRODUCER,
        )
        .order_by(AgentRun.id)
    ).all()
    for run in runs:
        task_id = _task_id(run.external_ref)
        if task_id is not None and (run.model or "").strip():
            last[task_id] = run
    if not last:
        return {}
    items = session.exec(
        select(BacklogItem)
        .join(Sprint, Sprint.id == BacklogItem.sprint_id)
        .where(
            Sprint.company_id == company_id,
            Sprint.status == "completed",
            BacklogItem.id.in_(list(last)),
        )
    ).all()
    out: dict[tuple[str, str], dict[str, int]] = {}
    for item in items:
        run = last[item.id]
        key = (company_models.role_key(run.agent_role), run.model.strip())
        row = out.setdefault(key, {"tasks": 0, "done": 0, "rework": 0})
        row["tasks"] += 1
        row["done"] += int(item.status == "done")
        row["rework"] += rework_request_count(item.comments)
    return out


def measured(row: dict[str, int] | None) -> bool:
    """Enough tasks, and more of them done than not: the record may decide."""
    return bool(row) and row["tasks"] >= MIN_TASKS and 2 * row["done"] >= row["tasks"]


def describe(role: str, row: dict[str, Any]) -> str:
    """The record as the suggestion's reason."""
    return (
        f"on {row['tasks']} of the company's {role} tasks: {row['done']} done, "
        f"{row['rework'] / row['tasks']:.1f} rework per task"
    )
