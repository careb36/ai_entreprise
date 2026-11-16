"""Sprint finalisation, the follow-up sprint and the retrospective (ADR 0014, the task worker's close stage).

``_finalize_sprint_if_ready`` builds the retrospective, closes the sprint once
across replicas (``replicas.close_once`` with ``_complete_sprint_and_plan_follow_up``),
emits ``sprint.closed`` (``hooks.SPRINT_CLOSED``), stores each task's observed
effort (``services/models/effort.py``, issue #534), and, at a first close only,
posts the retrospective, runs the procedures round (``procedures_round``) and
writes the retrospective engram. ``_complete_sprint_and_plan_follow_up`` marks
the sprint completed once every task is settled and, at autonomy level 3,
opens the self-improvement follow-up sprint with the tasks carried over.
Moved out of ``task_worker``, which still re-exports these names.
"""

import asyncio
import json
from datetime import UTC, datetime
from typing import Any
from uuid import uuid4

from config import settings
from database import engine
from logging_config import setup_logger
from models import BacklogItem, Company, OutboxEvent, Sprint
from nats_client import NATSClient
from schemas import SCOPE_HARD_MAX, SCOPE_LIMITS
from sqlmodel import Session, select

from services.learning import retrospective
from services.loop import replicas, sprint_control, wiki_close
from services.loop.carry_over import carry_over_on_the_companys_own
from services.loop.sprint_close import PROTECTED_PATHS_ON, RECLOSED
from services.loop.sprint_close import (
    describe_follow_up_reason as _describe_follow_up_reason,
)
from services.loop.sprint_close import reclosed as _reclosed
from services.loop.worker import messages, procedures_round
from services.models import effort
from services.planning import spec_planning, sprint_team
from services.planning.sprint_planner import (
    build_self_improvement_objective,
    plan_self_improvement_sprint,
)
from services.platform import hooks, protected_paths_setting
from services.platform.agent_service import AgentService
from services.platform.glassroom import publish_glassroom
from services.tasks import dependencies, progress_snapshot
from services.tasks.board import SCRUM_MASTER_ROLE

logger = setup_logger("fabrique-task-worker")

AUTO_SELF_IMPROVEMENT_ENABLED = settings.AUTO_SELF_IMPROVEMENT_ENABLED


# ---------------------------------------------------------------------------
# Sprint retrospective — written to memory after sprint completion
# ---------------------------------------------------------------------------


def _build_sprint_retrospective(sprint_id: int) -> dict[str, Any]:
    """Sprint health metrics for the retrospective (``services/learning/retrospective``)."""
    return retrospective.build(engine, sprint_id)


_format_retrospective_summary = retrospective.summary


def _coerce_json_dict(value: Any) -> dict[str, Any]:
    """Decode a JSON object stored either as dict or JSON string."""
    if isinstance(value, dict):
        return value
    if isinstance(value, str) and value.strip():
        try:
            parsed = json.loads(value)
            if isinstance(parsed, dict):
                return parsed
        except Exception:  # noqa: BLE001 - Invalid stored structured data is treated as unavailable.
            return {}
    return {}


def _build_sprint_kickoff_story(
    sprint_name: str, sprint_id: int, backlog_items: list[dict[str, Any]]
) -> str:
    """Create the initial coordination story for a newly opened sprint."""
    return "\n".join(
        [
            f"Sprint kickoff: {sprint_name}",
            f"Sprint ID: {sprint_id}",
            "Backlog:",
            *[
                f"- {item.get('title', '').strip()}: {item.get('description', '').strip()}"
                for item in backlog_items
            ],
            "Goal: PO, Scrum Master and Developer must coordinate, surface blockers, and agree the first execution plan.",
        ]
    )


def _plan_follow_up(
    session: Session, company: Company, retro_metrics: dict[str, Any] | None
) -> tuple[dict[str, Any], str, str, list[dict[str, Any]]]:
    """The planner's follow-up sprint: its plan, objective, scope size and tasks."""
    # The agents carry their company, so a connection model routes with its key.
    _, agent_dicts, discovery = spec_planning.planning_context_of(session, company.id)
    detected_stack = _coerce_json_dict(discovery.detected_stack if discovery else None)
    project_path = discovery.project_path if discovery else None

    scope_size = (company.default_scope_size or "M").upper()
    if scope_size not in {"S", "M", "L"}:
        scope_size = "M"

    objective = build_self_improvement_objective(
        company_name=company.company_name,
        detected_stack=detected_stack,
        sprint_metrics=retro_metrics,
    )
    plan = plan_self_improvement_sprint(
        company_name=company.company_name,
        objective=objective,
        scope_size=scope_size,
        agents=agent_dicts,
        detected_stack=detected_stack,
        project_path=project_path,
    )
    return plan, objective, scope_size, plan.get("tasks") or []


def _add_follow_up_tasks(
    session: Session, next_sprint: Sprint, tasks: list[dict[str, Any]]
) -> list[dict[str, Any]]:
    """One backlog item and one ``task.assign`` outbox event per planned task."""
    backlog_payloads: list[dict[str, Any]] = []
    for task in tasks:
        backlog_item = BacklogItem(
            sprint_id=next_sprint.id,
            title=task.get("title", ""),
            description=task.get("description", ""),
            priority=task.get("priority", "medium"),
            assignee=task.get("assignee", "PO"),
        )
        session.add(backlog_item)
        session.flush()
        backlog_payloads.append(task)

        session.add(
            OutboxEvent(
                aggregate_type="backlog_item",
                aggregate_id=str(backlog_item.id),
                event_type="nats_publish",
                payload={
                    "subject": "task.assign",
                    "message": {
                        "backlog_item_id": backlog_item.id,
                        "interaction_id": str(uuid4()),
                        "agent": task.get("assignee", "PO"),
                        "sprint_id": next_sprint.id,
                    },
                },
            )
        )
    return backlog_payloads


def _carry_into_follow_up(
    session: Session,
    company: Company,
    sprint: Sprint,
    next_sprint: Sprint,
    scope_size: str,
    planned: int,
) -> list[BacklogItem]:
    """The closed sprint's blocked tasks carried into the follow-up, once each."""
    # The tasks the agents gave up on get one more attempt, with what
    # blocked them; a second failure stays for a person.
    try:
        # A savepoint: a failure halfway leaves no half-carried task behind
        # and the session stays usable for the commit below.
        with session.begin_nested():
            carried = carry_over_on_the_companys_own(
                session,
                company.id,
                sprint.id,
                next_sprint,
                SCOPE_LIMITS.get(scope_size, SCOPE_HARD_MAX) - planned,
            )
    except Exception as exc:  # noqa: BLE001 - A failed carry-over never stops the follow-up sprint.
        logger.warning("Carry-over into sprint=%s failed: %s", next_sprint.id, exc)
        carried = []
    return carried


def _kickoff_event(
    company: Company, next_sprint: Sprint, backlog_payloads: list[dict[str, Any]]
) -> OutboxEvent:
    """The ``workspace.generate`` outbox event that starts the follow-up's conversation."""
    return OutboxEvent(
        aggregate_type="sprint",
        aggregate_id=str(next_sprint.id),
        event_type="nats_publish",
        payload={
            "subject": "workspace.generate",
            "message": {
                "interaction_id": str(uuid4()),
                "company_id": company.id,
                "business_unit_id": None,
                "sprint_id": next_sprint.id,
                "story": _build_sprint_kickoff_story(
                    next_sprint.sprint_name, next_sprint.id, backlog_payloads
                ),
                "constraints": "Start the sprint conversation, coordinate roles, and keep the GlassRoom updated.",
            },
        },
    )


def _audit_follow_up_off(session: Session, company: Company, sprint: Sprint) -> None:
    logger.info(
        "Sprint %s closed: no model-planned follow-up for company %s, which hosts "
        "the factory (protected paths on; AUTO_SELF_IMPROVEMENT_ENABLED=%s)",
        sprint.id,
        company.id,
        AUTO_SELF_IMPROVEMENT_ENABLED,
    )
    AgentService().audit_interaction(
        session=session,
        company_id=company.id,
        interaction_id=f"sprint:{sprint.id}:follow_up",
        agent_id="platform:protected_paths",
        action="follow_up_sprint_skipped",
        details={"company_id": str(company.id), "sprint_id": sprint.id},
        result={
            "reason": PROTECTED_PATHS_ON,
            "auto_self_improvement_enabled": AUTO_SELF_IMPROVEMENT_ENABLED,
        },
        decision_signature=None,
        cost_tokens=0,
    )


def _complete_sprint_and_plan_follow_up(
    completed_sprint_id: int,
    retro_metrics: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """Mark a sprint completed once every item is settled; optionally open a follow-up sprint.

    A sprint ends like a real one: when nothing in it can still move on its
    own. Tasks blocked until a person acts count as settled, so the sprint
    closes around them, the retrospective records them and ``blocked_rate``
    measures them. Holding the sprint open for a blocked task would leave the
    company without a retrospective, and its procedures unmeasured, for good.
    """
    with Session(engine) as session:
        sprint = session.get(Sprint, completed_sprint_id)
        if not sprint or sprint.status != "active":
            return {"completed": False, "auto_opened": False, "reason": "not_active"}

        # The dependency holds are reconciled first (``dependencies.sprint_settled``).
        if not dependencies.sprint_settled(session, completed_sprint_id):
            session.commit()
            return {"completed": False, "auto_opened": False, "reason": "not_all_done"}

        if not replicas.mark_completed(session, sprint):
            return {"completed": False, "auto_opened": False, "reason": "not_active"}

        result = {
            "completed": True,
            "auto_opened": False,
            # A second close, after a person reopened it: no second follow-up.
            "reason": RECLOSED
            if _reclosed(session, sprint.id)
            else "completed_no_follow_up",
            "sprint_id": sprint.id,
            "sprint_name": sprint.sprint_name,
            "company_id": sprint.company_id,
        }

        company = session.get(Company, sprint.company_id)
        if result["reason"] == RECLOSED:
            session.commit()
            return result
        if not company:
            result["reason"] = "company_missing"
            session.commit()
            return result

        if company.autonomy_level < 3:
            result["reason"] = "autonomy_below_threshold"
            session.commit()
            return result

        if protected_paths_setting.enabled(session, company.id):
            # The factory's own company (rsi-loop REQ-21): the model-planned
            # follow-up has no protected-path guard, so it never runs here.
            result["reason"] = PROTECTED_PATHS_ON
            _audit_follow_up_off(session, company, sprint)
            session.commit()
            return result

        if not AUTO_SELF_IMPROVEMENT_ENABLED:
            result["reason"] = "auto_self_improvement_disabled"
            session.commit()
            return result

        active_sprint = session.exec(
            select(Sprint).where(
                Sprint.company_id == company.id,
                Sprint.id != sprint.id,
                Sprint.status.in_(sprint_control.OPEN_STATUSES),
            )
        ).first()
        if active_sprint:
            result["reason"] = "active_sprint_already_exists"
            result["blocking_sprint_id"] = active_sprint.id
            session.commit()
            return result

        plan, objective, scope_size, tasks = _plan_follow_up(
            session, company, retro_metrics
        )
        if not tasks:
            result["reason"] = "planner_returned_no_tasks"
            session.commit()
            return result

        next_sprint = Sprint(
            company_id=company.id,
            sprint_name=plan.get(
                "sprint_name", f"Sprint - Improve {company.company_name}"
            ),
            start_date=datetime.now(UTC).date().isoformat(),
            scope_size=scope_size,
            status="active",
            goal=plan.get("goal") or objective,
            specification_id=sprint.specification_id,
        )
        session.add(next_sprint)
        session.flush()

        backlog_payloads = _add_follow_up_tasks(session, next_sprint, tasks)
        carried = _carry_into_follow_up(
            session,
            company,
            sprint,
            next_sprint,
            scope_size,
            len(tasks),
        )
        backlog_payloads.extend(
            {"title": item.title, "description": item.description or ""}
            for item in carried
        )
        result["carried_over"] = sprint_team.automatic_team(next_sprint, plan, carried)

        session.add(_kickoff_event(company, next_sprint, backlog_payloads))

        session.commit()
        result.update(
            {
                "auto_opened": True,
                "reason": "auto_follow_up_opened",
                "next_sprint_id": next_sprint.id,
                "next_sprint_name": next_sprint.sprint_name,
                "next_goal": next_sprint.goal,
            }
        )
        return result


async def _learn_from_close(sprint_id: int, retro_metrics: dict[str, Any]) -> None:
    """The retrospective message to the team, then the procedures round."""
    await asyncio.to_thread(
        messages._post_message,
        company_id=retro_metrics["company_id"],
        sprint_id=sprint_id,
        item_id=None,
        from_name=SCRUM_MASTER_ROLE,
        from_role=SCRUM_MASTER_ROLE,
        to_role=None,
        kind="retrospective",
        body=_format_retrospective_summary(retro_metrics),
    )
    # Measure procedures before proposing; learning never blocks closing.
    try:
        await asyncio.to_thread(
            procedures_round._evaluate_procedures,
            retro_metrics["company_id"],
            sprint_id,
        )
    except Exception as exc:  # noqa: BLE001
        logger.warning("Procedure evaluation failed for sprint=%s: %s", sprint_id, exc)
    try:
        await asyncio.to_thread(
            procedures_round._propose_procedures,
            retro_metrics["company_id"],
            sprint_id,
            retro_metrics,
        )
    except Exception as exc:  # noqa: BLE001
        logger.warning("Procedure proposal failed for sprint=%s: %s", sprint_id, exc)


def _observe_effort(sprint_id: int) -> int:
    """Write each task's ``effort_observed`` event (``services/models/effort.py``);
    at every close, a new event only when the observation changed. Never
    stops the close."""
    try:
        with Session(engine) as session:
            written = effort.record_sprint(session, sprint_id)
            session.commit()
            return written
    except Exception as exc:  # noqa: BLE001 - Observing effort never blocks closing.
        logger.warning("Effort observation failed for sprint=%s: %s", sprint_id, exc)
        return 0


def _burn_retrospective_engram(sprint_id: int, retro_metrics: dict[str, Any]) -> None:
    """Burn retrospective engram to memory so future sprints can learn from it."""
    try:
        from services.models.model_factory import get_llm_for_role
        from services.platform.engram_processor import (
            RETROSPECTIVE_AGENT_PREFIX,
            get_engram_processor,
        )

        processor = get_engram_processor()
        processor.burn_engram(
            company_id=retro_metrics.get("company_id"),
            agent_id=f"{RETROSPECTIVE_AGENT_PREFIX}{sprint_id}",
            role="ScrumMaster",
            decision_summary=_format_retrospective_summary(retro_metrics),
            metadata={
                "action_type": "sprint_retrospective",
                **retro_metrics,
            },
            llm=get_llm_for_role("EngramProcessor"),
        )
    except Exception as exc:  # noqa: BLE001 - Optional engram writes must not abort sprint completion.
        logger.warning(
            "Retrospective engram write failed for sprint=%s: %s", sprint_id, exc
        )


async def _announce_close(
    nats_client: NATSClient, sprint_id: int, result: dict[str, Any]
) -> None:
    """The Glassroom's close and follow-up statuses, and the follow-up's log line."""
    await publish_glassroom(
        nats_client,
        sprint_id,
        {
            "type": "status",
            "agent": "System",
            "role": "System",
            "task": result.get("sprint_name", "Sprint"),
            "message": f"🏁 Sprint completed: {result.get('sprint_name', 'Sprint')}",
        },
    )

    await publish_glassroom(
        nats_client,
        sprint_id,
        {
            "type": "status",
            "agent": "System",
            "role": "System",
            "task": result.get("sprint_name", "Sprint"),
            "message": _describe_follow_up_reason(result),
        },
    )

    logger.info(
        "Sprint follow-up evaluation sprint_id=%s auto_opened=%s reason=%s next_sprint_id=%s",
        sprint_id,
        result.get("auto_opened"),
        result.get("reason"),
        result.get("next_sprint_id"),
    )

    if result.get("auto_opened") and result.get("next_sprint_id"):
        await publish_glassroom(
            nats_client,
            result["next_sprint_id"],
            {
                "type": "status",
                "agent": "System",
                "role": "System",
                "task": result.get("next_sprint_name", "Self-improvement sprint"),
                "message": f"♻️ Autonomous self-improvement sprint opened: {result.get('next_sprint_name', 'Self-improvement sprint')}",
            },
        )


async def _finalize_sprint_if_ready(nats_client: NATSClient, sprint_id: int) -> None:
    """Close a sprint once all cards are done and optionally open the next autonomous self-improvement sprint."""
    # Build retrospective metrics before closing so the planner can use them.
    retro_metrics = await asyncio.to_thread(_build_sprint_retrospective, sprint_id)

    result = await asyncio.to_thread(  # once across replicas (issue #328)
        replicas.close_once,
        engine,
        sprint_id,
        _complete_sprint_and_plan_follow_up,
        retro_metrics,
    )
    if not result.get("completed"):
        return
    # The progress as it stood at this close, sealed in the audit chain
    # (issue #531); a reclose adds a snapshot and keeps the first.
    await asyncio.to_thread(
        progress_snapshot.seal_at_close, engine, sprint_id, retro_metrics
    )
    # What leaves the factory starts from the event: release integrates (issue #314).
    hooks.emit(
        hooks.SPRINT_CLOSED, nats_client=nats_client, engine=engine, sprint_id=sprint_id
    )
    # Each task's observed effort, from its own records (issue #534).
    await asyncio.to_thread(_observe_effort, sprint_id)
    if result.get("reason") == RECLOSED:
        retro_metrics = None  # learned once, at the first close
    if retro_metrics and retro_metrics.get("company_id") is not None:
        await _learn_from_close(sprint_id, retro_metrics)
        # The project wiki: compiled from the sprint's evidence, linted (#445).
        await wiki_close.after_close(nats_client, sprint_id, retro_metrics)

    if retro_metrics:
        _burn_retrospective_engram(sprint_id, retro_metrics)

        await publish_glassroom(
            nats_client,
            sprint_id,
            {
                "type": "status",
                "semantic_type": "sprint.retrospective",
                "source": "task_worker",
                "agent": "System",
                "role": "System",
                "task": "Sprint retrospective",
                "message": (
                    f"📊 Sprint retrospective: {retro_metrics.get('done_tasks', 0)}/{retro_metrics.get('total_tasks', 0)} tasks done "
                    f"({retro_metrics.get('completion_rate', 0)}%), "
                    f"{retro_metrics.get('rework_cycles', 0)} rework cycles, "
                    f"{retro_metrics.get('blocked_tasks', 0)} blocked. "
                    "Lessons stored in memory for future sprints."
                ),
                "retro": retro_metrics,
            },
        )

    await _announce_close(nats_client, sprint_id, result)
