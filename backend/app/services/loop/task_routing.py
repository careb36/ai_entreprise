"""The task worker's model choice by difficulty, and its record (issue #319).

Before each attempt the worker asks ``route_task`` which model to request:
it reads the task's plan (its criteria, the approved architecture's modules
and paths for them, its dependencies, the attempt and the rework history on
its board), estimates the difficulty (``services/models/difficulty.py``),
reads the company's routing policy, else the deployment's
(``services/models/routing_policy.py``), and lets ``model_routing.route``
choose. The estimate and the routing are stored on the task as an
``estimated`` event (an observation of the plan, never asked of a model) and
returned for the run's evidence, beside the model the proxy reports serving.

Recording never blocks the task: if any of it fails, the worker falls back to
the deployment's routing as it was before, with no event.
"""

from __future__ import annotations

from typing import Any

from logging_config import setup_logger
from models import BacklogItem, Sprint, TaskEvent
from sqlmodel import Session, func, select

from services.models import difficulty, model_routing, routing_policy
from services.planning import arch_author
from services.tasks import board, task_events

logger = setup_logger("fabrique-task-worker")


def _architecture(session: Session, row: BacklogItem) -> dict[str, Any] | None:
    sprint = session.get(Sprint, row.sprint_id)
    if sprint is None or sprint.specification_id is None:
        return None
    return arch_author.content_for(session, sprint.specification_id)


def _deliveries(session: Session, task_id: int) -> int:
    return int(
        session.exec(
            select(func.count())
            .select_from(TaskEvent)
            .where(TaskEvent.task_id == task_id, TaskEvent.event_type == "delivered")
        ).one()
    )


def estimate_for(session: Session, row: BacklogItem, *, rework: bool) -> dict[str, Any]:
    """The difficulty of *row*'s next attempt, from its plan and its board."""
    comments = board.comment_list(row.comments)
    return difficulty.estimate_item(
        row,
        _architecture(session, row),
        attempt=_deliveries(session, row.id) + 1,
        rework=rework,
        rework_requests=board.rework_request_count(row.comments),
        send_backs=sum(1 for c in comments if board.is_send_back(c)),
    )


def route_task(
    item: dict[str, Any],
    agent: dict[str, Any],
    company_id: int | None,
    *,
    rework: bool,
    editor: bool,
    engine: Any,
) -> dict[str, Any]:
    """``model_routing.route`` for this attempt, with the estimate stored on the task.

    Returns ``{"model", "difficulty", "routing"}``.
    """
    if company_id is None or not item.get("id"):
        return model_routing.route(agent, item=item, rework=rework, editor=editor)
    try:
        with Session(engine) as session:
            row = session.get(BacklogItem, item["id"])
            if row is None:
                return model_routing.route(
                    agent, item=item, rework=rework, editor=editor
                )
            estimate = estimate_for(session, row, rework=rework)
            policy, source = routing_policy.effective(session, company_id)
            decision = model_routing.route(
                agent,
                item=item,
                rework=rework,
                editor=editor,
                policy=policy,
                source=source,
                estimate=estimate,
            )
            task_events.record(
                session,
                row,
                "estimated",
                task_events.SYSTEM,
                payload={"difficulty": estimate, "routing": decision["routing"]},
                company_id=company_id,
            )
            session.commit()
            return decision
    except Exception as exc:  # noqa: BLE001 - The task runs on the deployment's routing.
        logger.warning(
            "Difficulty routing unavailable for item=%s: %s", item.get("id"), exc
        )
        return model_routing.route(agent, item=item, rework=rework, editor=editor)
