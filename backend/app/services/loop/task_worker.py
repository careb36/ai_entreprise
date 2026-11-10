"""
Task Worker: the NATS consumer that has an AI agent execute each backlog item.

Per task: receive ``task.assign`` (from the outbox), load the item and the agent
for its ``assignee`` (role and company, with its configured model and prompt),
call the LLM proxy, move the item to ``in_review`` with the deliverable as a
comment, and publish to Glassroom.
"""

import asyncio
import json
import os
from datetime import UTC, datetime
from typing import Any
from uuid import uuid4

from database import engine
from logging_config import setup_logger
from models import (
    Agent,
    BacklogItem,
    Sprint,
)
from models.agent_run import TASK_WORKER_PRODUCER  # noqa: F401 - re-exported
from nats_client import NATSClient
from sqlmodel import Session, select

from services.contracts.manifest import parse_file_manifest  # noqa: F401 - re-exported
from services.evidence import delivery_gate
from services.loop import (
    console_host,
    replicas,
    sprint_control,
)
from services.loop.answers import gate_reason as _gate_reason
from services.loop.worker import (
    board_writes,
    deliver,
    execute,
    messages,
    prompt,
    quality_gate,
    review,
)

# Re-exported for importers of ``services.task_worker`` (ADR 0014); a test
# patches the stage module (``services.loop.worker.<stage>``), which owns them.
from services.loop.worker.board_writes import (  # noqa: F401 - re-exported
    _append_item_comment,
    _update_item_status,
)
from services.loop.worker.close import (  # noqa: F401 - re-exported
    AUTO_SELF_IMPROVEMENT_ENABLED,
    _build_sprint_kickoff_story,
    _build_sprint_retrospective,
    _coerce_json_dict,
    _complete_sprint_and_plan_follow_up,
    _finalize_sprint_if_ready,
    _format_retrospective_summary,
)
from services.loop.worker.deliver import (  # noqa: F401 - re-exported
    _build_review_payload,
    _deliver_artifacts,
    _delivery_comment,
    _detect_project_test_command,
    _detect_test_command,
    _extract_artifact_targets,
    _normalize_artifact_content,
    _on_delivery_result,
    _plan_queued_delivery,
    _publish_queued_delivery,
    _queued_delivery_evidence,
    _resolve_workspace_root,
    _run_delivery_tests,
)
from services.loop.worker.execute import (  # noqa: F401 - re-exported
    ARCHITECT_CONSULT_ENABLED,
    LLMCallError,
    _architect_brief,
    _consult_architect,
    _execute_task,
    _sanitize_deliverable_text,
)
from services.loop.worker.messages import (  # noqa: F401 - re-exported
    MESSAGE_BODY_LIMIT,
    _handoff_body,
    _message,
    _post_message,
    _triage_blocked_task,
)
from services.loop.worker.procedures_round import (  # noqa: F401 - re-exported
    _ask_scrum_master_for_procedures,
    _evaluate_procedures,
    _propose_procedures,
)
from services.loop.worker.prompt import (  # noqa: F401 - re-exported
    FILE_MANIFEST_INSTRUCTIONS,
    LESSON_FEEDBACK_CHARS,
    LESSON_FEEDBACK_LIMIT,
    LESSON_SPRINTS,
    _fetch_memory_context,
    _is_lesson,
    _load_procedures_context,
    _load_recent_lessons,
    _load_sprint_completed_tasks,
)
from services.loop.worker.qa_review import (  # noqa: F401 - re-exported
    AUTOPILOT_MAX_REWORK_CYCLES,
    AUTOPILOT_QA_REWORK_ENABLED,
    MAX_QA_REWORK_CYCLES,
    _call_llm_for_review,
)
from services.loop.worker.quality_gate import (  # noqa: F401 - re-exported
    MIN_DELIVERABLE_WORDS,
    MIN_DOC_DELIVERABLE_WORDS,
    _send_back_from_quality_gate,
    _validate_minimum_deliverable,
)
from services.loop.worker.record import (  # noqa: F401 - re-exported
    _delivery_gate,
    _delivery_risk,
    _latest_task_verification,
    _record_task_run,
    _reverify_run,
    _summarize_deliverable,
)
from services.loop.worker.review import (  # noqa: F401 - re-exported
    MAX_QA_REVIEW_FAILURES,
    _review_cycle,
    _review_cycle_body,
)
from services.models import model_routing
from services.planning import spec_planning, sprint_team
from services.platform.glassroom import publish_glassroom
from services.producers import console_assistants
from services.producers.console_assistants import normalize_utc
from services.tasks import dependencies, task_events, task_projection
from services.tools import developer_tools

logger = setup_logger("fabrique-task-worker")

DISPATCH_REQUEUE_COOLDOWN_SECONDS = int(
    os.getenv("DISPATCH_REQUEUE_COOLDOWN_SECONDS", "90")
)
LAST_AUTONOMOUS_QUEUE_AT: dict[int, datetime] = {}


def _queued_recently(item_id: int, now: datetime) -> bool:
    """Avoid publishing duplicate queue events for the same todo item every review tick."""
    last_dispatched_at = LAST_AUTONOMOUS_QUEUE_AT.get(item_id)
    if not last_dispatched_at:
        return False
    return (
        now - last_dispatched_at
    ).total_seconds() < DISPATCH_REQUEUE_COOLDOWN_SECONDS


def _mark_queued(item_id: int, now: datetime) -> None:
    LAST_AUTONOMOUS_QUEUE_AT[item_id] = now


def _clear_queue_marker(item_id: int) -> None:
    LAST_AUTONOMOUS_QUEUE_AT.pop(item_id, None)


def _load_item_runtime_state(item_id: int) -> dict[str, Any] | None:
    """Load the latest mutable state so stale in-flight work can be discarded."""
    with Session(engine) as session:
        item = session.get(BacklogItem, item_id)
        if not item:
            return None
        record = task_projection.record_of(session, item)
        return {
            "status": item.status,
            "comments": record.comments,
            "manually_frozen": record.frozen(),
        }


def _sync_manual_freeze_state(item_id: int) -> bool:
    """Ensure manually frozen items stay blocked and out of automation."""
    with Session(engine) as session:
        item = session.get(BacklogItem, item_id)
        if not item or not task_projection.record_of(session, item).frozen():
            return False

        changed = item.status != "blocked"
        if changed:
            task_events.record(session, item, "blocked", "system", status="blocked")
        session.commit()
        return changed


# ---------------------------------------------------------------------------
# Database helpers
# ---------------------------------------------------------------------------


def _load_task_context(backlog_item_id: int) -> dict[str, Any] | None:
    """Load the backlog item, its sprint, and the matching agent from Postgres."""
    with Session(engine) as session:
        item = session.get(BacklogItem, backlog_item_id)
        if not item:
            logger.warning("BacklogItem %s not found — skipping", backlog_item_id)
            return None

        sprint = session.get(Sprint, item.sprint_id)
        if not sprint:
            logger.warning(
                "Sprint %s not found for item %s", item.sprint_id, backlog_item_id
            )
            return None
        if sprint_control.is_halted(getattr(sprint, "status", None)):
            logger.info("Sprint %s is not running: item %s", sprint.id, item.id)
            return None
        # A task assigned outside the sprint's team never runs (blocked for a person).
        if sprint_team.refuse_off_team(session, sprint, item):
            return None

        agent_row = session.exec(
            select(Agent).where(
                Agent.company_id == sprint.company_id,
                Agent.role == (item.assignee or "Dev"),
            )
        ).first()
        cm = model_routing.company_model(session, sprint.company_id, item.assignee)
        if agent_row:
            agent_dict = {
                "role": agent_row.role,
                "name": agent_row.name,
                # The agent's, else the company's; empty lets difficulty routing apply.
                "model": (agent_row.model or "").strip() or cm,
                "seniority": agent_row.seniority,
                "specialty": agent_row.specialty,
                "system_prompt": agent_row.system_prompt,
            }
        else:
            agent_dict = {
                "role": item.assignee or "Dev",
                "name": "",
                "model": cm,
                "seniority": "Senior",
                "specialty": "general software engineering",
                "system_prompt": None,
            }

        return {
            "item": {
                "id": item.id,
                "title": item.title,
                "description": item.description,
                "priority": item.priority,
                "assignee": item.assignee,
                "status": item.status,
                "comments": item.comments or [],
                # The decisions on this task read its events (issue #324).
                "events": task_projection.snapshot(session, item),
                "covers": item.covers if isinstance(item.covers, list) else [],
            },
            "sprint": {
                "id": sprint.id,
                "name": sprint.sprint_name,
                "company_id": sprint.company_id,
            },
            "agent": agent_dict,
        }


# ---------------------------------------------------------------------------
# NATS consumer — the main entry point started from main.py
# ---------------------------------------------------------------------------


DELIVERY_QUEUED = delivery_gate.DELIVERY_QUEUED
TASK_ASSIGN_CONSUMER = "task-worker"
DELIVERY_RESULTS_CONSUMER = "delivery-results"
# A task can take several LLM calls, console assistants and a test run; the
# consumer keeps the message claimed while the handler runs, so this only
# bounds how long a crashed worker holds an assignment.
TASK_ASSIGN_ACK_WAIT_SECONDS = 600.0


async def process_backlog_tasks(nats_client: NATSClient) -> None:
    """
    Background coroutine that subscribes to ``task.assign`` and processes
    each backlog item by invoking the appropriate AI agent.

    The LLM call is offloaded to a thread via ``asyncio.to_thread()`` to keep
    the event loop free for other NATS callbacks and SSE streaming.
    """

    async def _on_task_assign(msg, data_from_nats=None):
        data: dict[str, Any] = {}
        try:
            data = data_from_nats or json.loads(msg.data.decode())
            backlog_item_id = data.get("backlog_item_id")
            sprint_id = data.get("sprint_id")

            if not backlog_item_id:
                logger.warning("task.assign message missing backlog_item_id: %s", data)
                return

            logger.info(
                "Received task.assign: item=%s sprint=%s", backlog_item_id, sprint_id
            )

            # 1. Load context from DB.
            ctx = await asyncio.to_thread(_load_task_context, backlog_item_id)
            if not ctx:
                return

            item = ctx["item"]
            agent = ctx["agent"]
            sprint = ctx["sprint"]
            _clear_queue_marker(item["id"])

            if task_projection.record_from_dict(item).frozen():
                await asyncio.to_thread(_sync_manual_freeze_state, item["id"])
                logger.info(
                    "Skipping manually frozen assignment for item=%s", item["id"]
                )
                return

            # A task that waits for others is queued once they are done.
            if not await asyncio.to_thread(dependencies.ready, engine, item["id"]):
                return
            # Guard against duplicate task.assign events for the same item: one
            # replica claims it, with a conditional update from todo (issue #328),
            # recorded as its ``claimed`` event (issue #324).
            if item.get("status") != "todo" or not await asyncio.to_thread(
                replicas.claim_todo, engine, item["id"]
            ):
                logger.info(
                    "Skipping duplicate assignment for item=%s with status=%s",
                    item["id"],
                    item.get("status"),
                )
                return

            await publish_glassroom(
                nats_client,
                sprint["id"],
                {
                    "type": "agent_start",
                    "semantic_type": "task.claimed",
                    "source": "task_worker",
                    "agent": agent.get("name") or agent["role"],
                    "role": agent["role"],
                    "task_id": item["id"],
                    "task_status": "in_progress",
                    "phase": "claimed",
                    "task": item["title"],
                    "message": f"🚀 {agent.get('name') or agent['role']} started working on: {item['title']}",
                },
            )

            await publish_glassroom(
                nats_client,
                sprint["id"],
                {
                    "type": "status",
                    "semantic_type": "task.progress",
                    "source": "task_worker",
                    "agent": agent.get("name") or agent["role"],
                    "role": agent["role"],
                    "task_id": item["id"],
                    "task_status": "in_progress",
                    "phase": "context",
                    "task": item["title"],
                    "message": f"🧭 {agent.get('name') or agent['role']} claimed the card and is gathering context for: {item['title']}",
                },
            )

            # Fetch memory context and cross-task context concurrently (non-blocking).
            (
                memory_ctx,
                lessons_ctx,
                procedures_ctx,
                completed_ctx,
                requirements_ctx,
                assistant_context,
            ) = await asyncio.gather(
                asyncio.to_thread(
                    prompt._fetch_memory_context, item, agent, sprint["company_id"]
                ),
                asyncio.to_thread(
                    prompt._load_recent_lessons, sprint["company_id"], sprint["id"]
                ),
                asyncio.to_thread(
                    prompt._load_procedures_context,
                    sprint["company_id"],
                    agent.get("role"),
                ),
                asyncio.to_thread(
                    prompt._load_sprint_completed_tasks, sprint["id"], item["id"]
                ),
                spec_planning.task_requirements_context(
                    nats_client, engine, item["id"], deliver._resolve_workspace_root
                ),
                console_assistants.collect_advice(
                    nats_client, item, agent, sprint, deliver._resolve_workspace_root
                ),
            )
            external_advice = assistant_context.get("combined", "")
            assistant_usage_comment = console_assistants.usage_comment(
                assistant_context.get("sources", [])
            )

            if memory_ctx or lessons_ctx:
                await publish_glassroom(
                    nats_client,
                    sprint["id"],
                    {
                        "type": "status",
                        "semantic_type": "task.memory_retrieved",
                        "source": "task_worker",
                        "agent": agent.get("name") or agent["role"],
                        "role": agent["role"],
                        "task_id": item["id"],
                        "task_status": "in_progress",
                        "phase": "memory_retrieved",
                        "task": item["title"],
                        "message": f"🧠 Memory context retrieved for: {item['title']}",
                    },
                )

            if assistant_usage_comment:
                await asyncio.to_thread(
                    board_writes._update_item_status,
                    item["id"],
                    "in_progress",
                    comment_text=assistant_usage_comment,
                    comment_author="System",
                    kind="advised",
                )

            architect_advice = await execute._architect_brief(
                nats_client, item, agent, sprint
            )

            await publish_glassroom(
                nats_client,
                sprint["id"],
                {
                    "type": "status",
                    "semantic_type": "task.progress",
                    "source": "task_worker",
                    "agent": agent.get("name") or agent["role"],
                    "role": agent["role"],
                    "task_id": item["id"],
                    "task_status": "in_progress",
                    "phase": "execution",
                    "task": item["title"],
                    "message": (
                        f"🛠️ {agent.get('name') or agent['role']} is executing autonomously on: {item['title']}"
                        if external_advice
                        else f"🛠️ {agent.get('name') or agent['role']} is executing autonomously without external assistant help on: {item['title']}"
                    ),
                },
            )

            # 3. The company's console producer for the role, else the LLM (in a thread).
            deliverable = await console_host.produce(
                nats_client, item, agent, sprint, requirements_ctx, architect_advice
            ) or await asyncio.to_thread(
                execute._execute_task,
                item,
                agent,
                sprint["name"],
                external_advice,
                memory_ctx,
                completed_ctx,
                architect_advice or None,
                sprint["company_id"],
                sprint["id"],
                lessons_ctx or None,
                procedures_ctx or None,
                requirements_ctx or None,
            )
            deliverable = execute._sanitize_deliverable_text(deliverable)

            await publish_glassroom(
                nats_client,
                sprint["id"],
                {
                    "type": "status",
                    "semantic_type": "task.deliverable_ready",
                    "source": "task_worker",
                    "agent": agent.get("name") or agent["role"],
                    "role": agent["role"],
                    "task_id": item["id"],
                    "task_status": "in_progress",
                    "phase": "deliverable",
                    "task": item["title"],
                    "message": f"📦 {agent.get('name') or agent['role']} produced a deliverable for: {item['title']}. Running quality checks before QA handoff.",
                    "preview": deliverable[:200] if deliverable else "",
                },
            )

            latest_state = await asyncio.to_thread(_load_item_runtime_state, item["id"])
            if not latest_state:
                logger.info("Skipping stale completion for missing item=%s", item["id"])
                return
            if latest_state.get("manually_frozen"):
                await asyncio.to_thread(_sync_manual_freeze_state, item["id"])
                logger.info(
                    "Discarding stale completion for manually frozen item=%s",
                    item["id"],
                )
                return
            if latest_state.get("status") != "in_progress":
                logger.info(
                    "Discarding stale completion for item=%s with status=%s",
                    item["id"],
                    latest_state.get("status"),
                )
                return

            is_valid, validation_reason = quality_gate._validate_minimum_deliverable(
                item, deliverable, agent.get("tool_edits")
            )
            if not is_valid:
                reason = _gate_reason(validation_reason, agent.get("finish_reason"))
                await quality_gate._send_back_from_quality_gate(
                    nats_client, item, agent, sprint, deliverable, reason
                )
                return

            review_payload = await deliver._deliver_artifacts(
                nats_client, item, agent, sprint, deliverable
            )

            # 4. Save result and mark as in_review (a reviewable comment even if
            # artifact normalization produced an empty payload).
            review_text = (review_payload or deliverable or "").strip()
            if not review_text:
                review_text = "(no deliverable text produced)"
            await asyncio.to_thread(
                board_writes._update_item_status,
                item["id"],
                "in_review",
                comment_text=review_text,
                comment_author=agent.get("name") or agent["role"],
                kind="delivered",
                actor=agent["role"],
            )
            await asyncio.to_thread(
                messages._post_message,
                company_id=sprint["company_id"],
                sprint_id=sprint["id"],
                item_id=item["id"],
                from_name=agent.get("name") or agent["role"],
                from_role=agent["role"],
                to_role="QA",
                kind="handoff",
                body=messages._handoff_body(item["title"], deliverable),
            )

            await publish_glassroom(
                nats_client,
                sprint["id"],
                {
                    "type": "status",
                    "semantic_type": "task.review_handoff",
                    "source": "task_worker",
                    "agent": "System",
                    "role": "System",
                    "task_id": item["id"],
                    "task_status": "in_review",
                    "phase": "qa_handoff",
                    "task": item["title"],
                    "message": f"🧪 {item['title']} moved to QA review.",
                    "preview": deliverable[:200] if deliverable else "",
                },
            )

            await publish_glassroom(
                nats_client,
                sprint["id"],
                {
                    "type": "agent_complete",
                    "semantic_type": "task.agent_complete",
                    "source": "task_worker",
                    "agent": agent.get("name") or agent["role"],
                    "role": agent["role"],
                    "task_id": item["id"],
                    "task_status": "in_review",
                    "phase": "implementation_complete",
                    "task": item["title"],
                    "message": f"✅ {agent.get('name') or agent['role']} completed: {item['title']}",
                    "preview": deliverable[:200] if deliverable else "",
                },
            )

            logger.info(
                "Task completed: item=%s by %s (%s) — %d chars",
                backlog_item_id,
                agent.get("name") or agent["role"],
                agent.get("model", "?"),
                len(deliverable),
            )

        except Exception as e:
            logger.exception(
                "Error processing task.assign (item=%s): %s",
                data.get("backlog_item_id", "?"),
                e,  # noqa: TRY401 - Preserve the existing log message as well as its traceback.
            )

            credit = getattr(e, "credit_provider", None)
            if credit and data.get("backlog_item_id") and data.get("sprint_id"):
                # No credit: the sprint pauses and the task waits as todo, so
                # it runs again when a person resumes the sprint.
                await asyncio.to_thread(
                    sprint_control.pause_task_for_credit,
                    engine,
                    data["backlog_item_id"],
                    data["sprint_id"],
                    credit,
                )
                await sprint_control.publish_credit_pause(
                    nats_client, data["sprint_id"], credit
                )
                return

            # Mark as blocked on failure.
            if data.get("backlog_item_id"):
                try:
                    await asyncio.to_thread(
                        board_writes._update_item_status,
                        data["backlog_item_id"],
                        "blocked",
                        comment_text=(
                            str(e)[:500]
                            if isinstance(e, execute.LLMCallError)
                            else f"Agent error: {str(e)[:500]}"
                        ),
                        comment_author="System",
                        kind="blocked",
                    )
                except Exception:  # noqa: BLE001, S110 - Recovery bookkeeping must not mask the task failure.
                    pass

            if data.get("sprint_id"):
                await publish_glassroom(
                    nats_client,
                    data["sprint_id"],
                    {
                        "type": "error",
                        "semantic_type": "task.error",
                        "source": "task_worker",
                        "task_id": data.get("backlog_item_id"),
                        "task_status": "blocked",
                        "message": f"❌ Task failed: {str(e)[:200]}",
                    },
                )

    # Durable: assignments published while this worker was down are processed
    # when it starts.  The handler records its own failures on the board, so a
    # message is acknowledged once the handler returns.
    developer_tools.bind(
        nats_client, asyncio.get_running_loop(), deliver._resolve_workspace_root
    )

    async def _on_task_assign_held(msg, data=None):
        # Held while this replica works on the task: a restarting one leaves it alone.
        with replicas.task_lock(engine, (data or {}).get("backlog_item_id")) as mine:
            if mine:
                await _on_task_assign(msg, data)

    await nats_client.consume(
        "task.assign",
        TASK_ASSIGN_CONSUMER,
        _on_task_assign_held,
        ack_wait_seconds=TASK_ASSIGN_ACK_WAIT_SECONDS,
    )
    logger.info("Task Worker consuming task.assign")

    async def _on_delivered(msg, data):
        await deliver._on_delivery_result(nats_client, data)

    # Without JetStream nothing is ever queued, so there are no results either.
    if nats_client.jetstream_enabled:
        await nats_client.consume(
            "workspace.delivered", DELIVERY_RESULTS_CONSUMER, _on_delivered
        )

    await review._review_cycle(nats_client)

    # Periodic review loop — processes items stuck in in_review or blocked.
    await asyncio.sleep(10)  # Let initial tasks start first
    while True:
        try:
            await review._review_cycle(nats_client)
        except Exception as e:  # noqa: BLE001 - Keep the review polling loop alive across cycle failures.
            logger.error("Review cycle error: %s", e)
        await asyncio.sleep(30)  # Run every 30s


async def _dispatch_todo_items(nats_client: NATSClient) -> None:
    """Find todo items and publish task.assign events for them."""
    # Tasks waiting on one a person must act on are blocked with the reason;
    # those whose wait ended come back to todo, and are queued below.
    await asyncio.to_thread(dependencies.settle_active, engine)
    with Session(engine) as session:
        todo_items = session.exec(
            select(BacklogItem).where(BacklogItem.status == "todo")
        ).all()
        histories = task_projection.histories_for(session, list(todo_items))
        for item in todo_items:
            item_sprint = session.get(Sprint, item.sprint_id)
            if item_sprint is not None and sprint_control.is_halted(item_sprint.status):
                continue
            if not dependencies.is_ready(session, item):
                continue
            dispatch_time = datetime.now(UTC)
            record = task_projection.record_of(session, item, histories)
            if record.frozen():
                task_events.record(session, item, "blocked", "system", status="blocked")
                logger.info(
                    "Keeping manually frozen todo item=%s blocked during dispatch",
                    item.id,
                )
                continue

            existing_deliverable = record.deliverable()
            if existing_deliverable:
                task_events.record(
                    session, item, "recovered", "system", status="in_review"
                )
                _clear_queue_marker(item.id)
                await publish_glassroom(
                    nats_client,
                    item.sprint_id,
                    {
                        "type": "status",
                        "semantic_type": "task.recovered",
                        "source": "task_worker",
                        "agent": "System",
                        "role": "System",
                        "task_id": item.id,
                        "task_status": "in_review",
                        "phase": "todo_recovery",
                        "task": item.title,
                        "message": f"♻️ Recovered todo task with existing deliverable: {item.title} moved to QA review.",
                        "preview": existing_deliverable[:200],
                    },
                )
                logger.info(
                    "Recovered todo item=%s to in_review because a deliverable already exists",
                    item.id,
                )
                continue

            if _queued_recently(item.id, dispatch_time):
                logger.debug(
                    "Skipping duplicate autonomous queue publish for item=%s", item.id
                )
                continue

            # Durable: one stored assignment per todo state of the item, even
            # though this runs every review cycle while the worker is busy.
            updated = normalize_utc(item.updated_at)
            await nats_client.publish(
                "task.assign",
                {
                    "agent": item.assignee or "Dev",
                    "sprint_id": item.sprint_id,
                    "interaction_id": str(uuid4()),
                    "backlog_item_id": item.id,
                },
                msg_id=f"assign-{item.id}-{updated.isoformat() if updated else '-'}",
            )
            _mark_queued(item.id, dispatch_time)
            await publish_glassroom(
                nats_client,
                item.sprint_id,
                {
                    "type": "status",
                    "semantic_type": "task.queued",
                    "source": "task_worker",
                    "agent": "System",
                    "role": "System",
                    "task_id": item.id,
                    "task_status": "todo",
                    "phase": "queued",
                    "task": item.title,
                    "message": f"📬 Task queued for autonomous execution: {item.title} → {item.assignee or 'Dev'}",
                },
            )
        session.commit()
