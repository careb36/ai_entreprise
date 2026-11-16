"""The task worker's model choice by difficulty, and its record (issue #319).

Before each attempt the worker asks ``route_task`` which model to request:
it reads the task's plan (its criteria, the approved architecture's modules
and paths for them, its dependencies, the attempt and the rework history on
its board), has models estimate the difficulty
(``model_routing.estimate_task``), reads the company's routing policy, else
the deployment's (``model_routing.effective_policy``), and lets
``model_routing.route`` choose. The estimate and the routing are stored on
the task as an ``estimated`` event (an observation of the plan, never asked
of a model) and returned for the run's evidence, beside the model the proxy
reports serving.

Recording never blocks the task: if any of it fails, the worker falls back to
the deployment's routing as it was before, with no event.
"""

from __future__ import annotations

from typing import Any

from logging_config import setup_logger
from models import BacklogItem, Sprint
from sqlmodel import Session

from services.models import model_routing
from services.planning import arch_author
from services.tasks import task_events

logger = setup_logger("fabrique-task-worker")


def _architecture(session: Session, row: BacklogItem) -> dict[str, Any] | None:
    sprint = session.get(Sprint, row.sprint_id)
    if sprint is None or sprint.specification_id is None:
        return None
    return arch_author.content_for(session, sprint.specification_id)


def estimate_for(session: Session, row: BacklogItem, *, rework: bool) -> dict[str, Any]:
    """The difficulty of *row*'s next attempt, from its plan and its board:
    the architecture is planning's, the estimate models'."""
    return model_routing.estimate_task(
        session, row, _architecture(session, row), rework=rework
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
            policy, source = model_routing.effective_policy(session, company_id)
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
