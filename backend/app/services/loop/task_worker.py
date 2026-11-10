"""
Task Worker: the NATS consumer that has an AI agent execute each backlog item.

Per task: receive ``task.assign`` (from the outbox), load the item and the agent
for its ``assignee`` (role and company, with its configured model and prompt),
call the LLM proxy, move the item to ``in_review`` with the deliverable as a
comment, and publish to Glassroom.
"""

import asyncio
import functools
import json
import os
import re
from datetime import UTC, datetime
from typing import Any
from uuid import uuid4

from database import engine
from llm_proxy_client import LLMProxyClient
from logging_config import setup_logger
from models import (
    Agent,
    BacklogItem,
    Company,
    Sprint,
)
from models.agent_run import TASK_WORKER_PRODUCER  # noqa: F401 - re-exported
from nats_client import NATSClient
from sqlmodel import Session, select

from services.contracts.manifest import parse_file_manifest
from services.evidence import (
    delivery_gate,
    risk,
    standards,
)
from services.learning import procedures
from services.loop import (
    console_host,
    replicas,
    review_digest,
    sprint_control,
)
from services.loop.answers import REVIEW_MAX_TOKENS
from services.loop.answers import gate_reason as _gate_reason
from services.loop.answers import llm_content as _llm_content
from services.loop.answers import llm_failure as _llm_failure
from services.loop.answers import review_format_reminder as _review_format_reminder
from services.loop.carry_over import previous_attempt_context
from services.loop.worker import (
    board_writes,
    close,
    deliver,
    execute,
    messages,
    prompt,
    record,
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
from services.loop.worker.record import (  # noqa: F401 - re-exported
    _delivery_gate,
    _delivery_risk,
    _latest_task_verification,
    _record_task_run,
    _reverify_run,
    _summarize_deliverable,
)
from services.models import model_routing
from services.planning import spec_planning, sprint_team
from services.platform import locks
from services.platform.glassroom import publish_glassroom
from services.platform.orchestration_metrics import (
    ACTION_QA_REVIEW,
    RESULT_NEEDS_WORK,
    classify_review_result,
    record_agent_turn,
)
from services.producers import console_assistants
from services.producers.console_assistants import normalize_utc
from services.tasks import dependencies, task_events, task_projection
from services.tasks.board import (
    DELIVERY_GATE_AUTHOR,
    QUALITY_GATE_AUTHOR,
    RISK_HOLD_PREFIX,
    SCRUM_MASTER_ROLE,
)
from services.tools import developer_tools
from services.tools.workspace_edits import delivered

logger = setup_logger("fabrique-task-worker")

MAX_QA_REVIEW_FAILURES = 3
MAX_QA_REWORK_CYCLES = 2
MIN_DELIVERABLE_WORDS = 80
MIN_DOC_DELIVERABLE_WORDS = 180
AUTOPILOT_QA_REWORK_ENABLED = os.getenv(
    "AUTOPILOT_QA_REWORK_ENABLED", "true"
).lower() in {"1", "true", "yes", "on"}
AUTOPILOT_MAX_REWORK_CYCLES = int(
    os.getenv("AUTOPILOT_MAX_REWORK_CYCLES", str(MAX_QA_REWORK_CYCLES))
)
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


def _validate_minimum_deliverable(
    item: dict[str, Any], deliverable: str, tool_edits: Any = None
) -> tuple[bool, str]:
    """Light checks before QA review; edit-tool files make the prose a summary."""
    text = (deliverable or "").strip()
    if delivered(tool_edits) and text.count("```") % 2 == 0:
        return True, "ok"
    if not text:
        return False, "Empty output from agent"

    word_count = len(re.findall(r"\b\w+\b", text))
    title_desc = f"{item.get('title', '')} {item.get('description', '')}".lower()
    doc_like = any(
        kw in title_desc
        for kw in (
            "document",
            "documentation",
            "readme",
            "architecture",
            "api",
            "strategy",
            "process",
            "pipeline",
            "standards",
        )
    )

    min_words = MIN_DOC_DELIVERABLE_WORDS if doc_like else MIN_DELIVERABLE_WORDS
    if word_count < min_words:
        return False, f"Too short ({word_count} words, minimum {min_words})"

    heading_count = len(re.findall(r"(?m)^#{1,6}\s+", text))
    if doc_like and heading_count < 2:
        return False, "Documentation-like deliverable missing structured sections"

    code_fence_count = text.count("```")
    if code_fence_count % 2 != 0:
        return False, "Unbalanced code fences (likely truncated output)"

    tail = text[-180:].strip()
    if tail.endswith((":", ",", ";", " and", " or", " the", " to", " with")):
        return False, "Output appears to end abruptly"

    return True, "ok"


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


async def _send_back_from_quality_gate(
    nats_client: NATSClient,
    item: dict[str, Any],
    agent: dict[str, Any],
    sprint: dict[str, Any],
    deliverable: str,
    reason: str,
) -> None:
    """Ask for another attempt when the text checks reject a deliverable; block after the limit.

    The checks look at the text's form (length, sections, fences): a
    deterministic opinion, not evidence. It may send work back with the
    reason, like a reviewer, but holding a task for good on it would leave the
    sprint open forever. After ``AUTOPILOT_MAX_REWORK_CYCLES`` attempts the
    task is blocked for a person and escalated to the ScrumMaster.
    """
    previous_reworks = task_projection.record_from_dict(item).rework_requests_by(
        QUALITY_GATE_AUTHOR
    )
    rework = previous_reworks < AUTOPILOT_MAX_REWORK_CYCLES
    text = (
        f"NEEDS WORK: {reason}. Deliver the complete result again, with every "
        "file in full as a path= block."
        if rework
        else f"Pre-review quality gate failed after {previous_reworks} rework(s) "
        f"— blocked for manual triage: {reason}"
    )
    message_id = await asyncio.to_thread(
        messages._post_message,
        company_id=sprint["company_id"],
        sprint_id=sprint["id"],
        item_id=item["id"],
        from_name=QUALITY_GATE_AUTHOR,
        from_role=QUALITY_GATE_AUTHOR,
        to_role=agent["role"] if rework else SCRUM_MASTER_ROLE,
        kind="request" if rework else "escalation",
        body=text,
    )
    if not rework:
        # Answer the escalation before the task counts as blocked: the review
        # cycle may close the sprint (and, at autonomy 3, carry the task over)
        # as soon as it is, and that must see the ScrumMaster's decision.
        await asyncio.to_thread(
            messages._triage_blocked_task,
            sprint["company_id"],
            sprint["id"],
            item["id"],
            text,
            message_id,
        )
    await asyncio.to_thread(
        board_writes._update_item_status,
        item["id"],
        "todo" if rework else "blocked",
        comment_text=text,
        comment_author=QUALITY_GATE_AUTHOR,
        kind="reviewed" if rework else "blocked",
    )
    await publish_glassroom(
        nats_client,
        sprint["id"],
        {
            "type": "status" if rework else "error",
            "semantic_type": "task.rework_scheduled" if rework else "task.blocked",
            "source": "task_worker",
            "agent": QUALITY_GATE_AUTHOR,
            "role": "System",
            "task_id": item["id"],
            "task_status": "todo" if rework else "blocked",
            "phase": "quality_gate",
            "task": item["title"],
            "message": (
                f"↩️ Sent back for rework: {item['title']} ({reason})."
                if rework
                else f"❌ Pre-review gate blocked deliverable for: {item['title']} ({reason})."
            ),
            "preview": deliverable[:200] if deliverable else "",
        },
    )
    logger.warning(
        "Pre-review gate %s item=%s by %s: %s",
        "sent back" if rework else "blocked",
        item["id"],
        agent.get("name") or agent["role"],
        reason,
    )


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

            is_valid, validation_reason = _validate_minimum_deliverable(
                item, deliverable, agent.get("tool_edits")
            )
            if not is_valid:
                reason = _gate_reason(validation_reason, agent.get("finish_reason"))
                await _send_back_from_quality_gate(
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

    await _review_cycle(nats_client)

    # Periodic review loop — processes items stuck in in_review or blocked.
    await asyncio.sleep(10)  # Let initial tasks start first
    while True:
        try:
            await _review_cycle(nats_client)
        except Exception as e:  # noqa: BLE001 - Keep the review polling loop alive across cycle failures.
            logger.error("Review cycle error: %s", e)
        await asyncio.sleep(30)  # Run every 30s


async def _review_cycle(nats_client: NATSClient) -> None:
    """One replica at a time runs the review cycle; the others skip the round (issue #328)."""
    with locks.held(engine, locks.REVIEW_CYCLE) as mine:
        if mine:  # a dead replica's tasks first, so the dispatch below queues them
            await replicas.recover_stale(engine, nats_client)
            await _review_cycle_body(nats_client)


async def _review_cycle_body(nats_client: NATSClient) -> None:
    """
    Periodic cycle that advances the sprint board:
    - ``in_review`` items get reviewed by QA and moved to ``done``.
    - ``blocked`` items get retried once (moved back to ``todo`` for re-dispatch).
    """
    # Items blocked by QA in this same cycle must not be auto-retried below.
    quality_gate_blocked_ids: set[int] = set()

    with Session(engine) as session:
        # --- Review: in_review → done ---
        in_review = session.exec(
            select(BacklogItem).where(BacklogItem.status == "in_review")
        ).all()
        # Every decision below reads the task's events (one query for all).
        histories = task_projection.histories_for(session, list(in_review))

        for item in in_review:
            sprint = session.get(Sprint, item.sprint_id)
            if not sprint or sprint_control.is_halted(sprint.status):
                continue

            # Find QA agent for this company (fallback to any reviewer)
            reviewer = session.exec(
                select(Agent).where(
                    Agent.company_id == sprint.company_id,
                    Agent.role == "QA",
                )
            ).first()
            qa_model = getattr(reviewer, "model", None) or model_routing.company_model(
                session, sprint.company_id, "QA"
            )
            reviewer_dict = {
                "role": reviewer.role if reviewer else "QA",
                "name": reviewer.name if reviewer else "QA",
                "model": model_routing.resolve_agent_model(
                    {"model": qa_model, "role": "QA"}
                ),
                "seniority": reviewer.seniority if reviewer else "Senior",
                "system_prompt": reviewer.system_prompt if reviewer else None,
            }

            task_record = task_projection.record_of(session, item, histories)
            if task_record.frozen():
                task_events.record(session, item, "blocked", "system", status="blocked")
                session.commit()
                logger.info("Skipping QA review for manually frozen item=%s", item.id)
                continue

            deliverable = task_record.deliverable()
            # Prevent infinite review loops when QA repeatedly fails.
            qa_review_failures = task_record.review_failures()
            if qa_review_failures >= MAX_QA_REVIEW_FAILURES:
                task_events.record(
                    session,
                    item,
                    "blocked",
                    "system",
                    status="blocked",
                    payload={"reason": "review retries exhausted"},
                )
                session.commit()

                await publish_glassroom(
                    nats_client,
                    sprint.id,
                    {
                        "type": "error",
                        "semantic_type": "task.blocked",
                        "source": "task_worker",
                        "agent": reviewer_dict["name"],
                        "role": reviewer_dict["role"],
                        "task_id": item.id,
                        "task_status": "blocked",
                        "phase": "qa_review",
                        "task": f"Reviewing: {item.title}",
                        "message": f"❌ {reviewer_dict['name']} review failed {MAX_QA_REVIEW_FAILURES} times for: {item.title}. Marked as blocked.",
                    },
                )

                logger.warning(
                    "Review retries exhausted for item=%s; marked blocked", item.id
                )
                continue

            if not deliverable:
                task_events.record(
                    session,
                    item,
                    "blocked",
                    "system",
                    status="blocked",
                    text="QA review aborted: no deliverable found in task comments.",
                )
                session.commit()

                await publish_glassroom(
                    nats_client,
                    sprint.id,
                    {
                        "type": "error",
                        "semantic_type": "task.blocked",
                        "source": "task_worker",
                        "agent": reviewer_dict["name"],
                        "role": reviewer_dict["role"],
                        "task_id": item.id,
                        "task_status": "blocked",
                        "phase": "qa_review",
                        "task": f"Reviewing: {item.title}",
                        "message": f"❌ {reviewer_dict['name']} cannot review {item.title}: missing deliverable. Marked as blocked.",
                    },
                )

                continue

            # Evidence before opinion: a delivery the daemon refuted or never
            # confirmed goes back for rework without asking the QA model.
            gate = await asyncio.to_thread(
                record._delivery_gate, sprint.company_id, item.id
            )
            if gate is not None and gate[0] == DELIVERY_QUEUED:
                # Files are written when the daemon connects; review then.
                continue
            if gate is not None:
                problem, observation = gate
                previous_gate_reworks = task_record.rework_requests_by(
                    DELIVERY_GATE_AUTHOR
                )
                rework = previous_gate_reworks < AUTOPILOT_MAX_REWORK_CYCLES
                instruction = (
                    "Fix the code so the project's checks pass and deliver every "
                    "changed file in full as a path= block."
                    if problem in delivery_gate.CODE_PROBLEMS
                    else "Re-run the task and give every file in full as a path= block."
                )
                gate_event = task_events.record(
                    session,
                    item,
                    "reviewed" if rework else "blocked",
                    "gate",
                    author=DELIVERY_GATE_AUTHOR,
                    status="todo" if rework else "blocked",
                    text=(
                        f"NEEDS WORK: {problem} — {observation}. {instruction}"
                        if rework
                        else f"{problem.capitalize()} after {previous_gate_reworks} "
                        f"rework(s) — blocked for manual triage: {observation}"
                    ),
                )
                gate_text = gate_event.payload["text"]
                gate_message = messages._message(
                    sprint.company_id,
                    sprint.id,
                    item.id,
                    DELIVERY_GATE_AUTHOR,
                    DELIVERY_GATE_AUTHOR,
                    (item.assignee or "Developer") if rework else SCRUM_MASTER_ROLE,
                    "request" if rework else "escalation",
                    gate_text,
                )
                session.add(gate_message)
                session.commit()
                if not rework:
                    quality_gate_blocked_ids.add(item.id)
                    await asyncio.to_thread(
                        messages._triage_blocked_task,
                        sprint.company_id,
                        sprint.id,
                        item.id,
                        gate_text,
                        gate_message.id,
                    )
                await publish_glassroom(
                    nats_client,
                    sprint.id,
                    {
                        "type": "status" if rework else "error",
                        "semantic_type": "task.rework_scheduled"
                        if rework
                        else "task.blocked",
                        "source": "task_worker",
                        "agent": DELIVERY_GATE_AUTHOR,
                        "role": "System",
                        "task_id": item.id,
                        "task_status": item.status,
                        "phase": "delivery_gate",
                        "task": f"Reviewed: {item.title}",
                        "message": f"🧱 {problem.capitalize()} for {item.title}: {observation}",
                    },
                )
                continue

            # Build review prompt
            review_prompt = (
                f"You are reviewing the following deliverable for the task: '{item.title}'.\n"
                f"Description: {item.description or 'N/A'}\n\n"
                f"--- DELIVERABLE ---\n{deliverable[: review_digest.DIGEST_CHARS]}\n--- END ---"
                + (review_digest.NOTE if review_digest.is_digest(deliverable) else "")
                + "\n\nProvide a brief review (2-3 sentences). If acceptable, start with 'APPROVED:'. "
                "If not, start with 'NEEDS WORK:' and explain what's missing."
                + _review_format_reminder(qa_review_failures)
                # A second attempt is judged against what blocked the first and
                # the ScrumMaster's brief for it, the same context the agent had.
                + previous_attempt_context(item)
                + spec_planning.requirements_for(session, sprint, item)
                + standards.prompt_section(reviewer_dict["role"])
            ) + procedures.procedures_prompt(
                procedures.active_procedures(
                    session, sprint.company_id, reviewer_dict["role"]
                )
            )

            try:
                await publish_glassroom(
                    nats_client,
                    sprint.id,
                    {
                        "type": "agent_start",
                        "semantic_type": "task.review_started",
                        "source": "task_worker",
                        "agent": reviewer_dict["name"],
                        "role": reviewer_dict["role"],
                        "task_id": item.id,
                        "task_status": "in_review",
                        "phase": "qa_review",
                        "task": f"Reviewing: {item.title}",
                        "message": f"🔍 {reviewer_dict['name']} is reviewing: {item.title}",
                    },
                )

                review_result = await asyncio.to_thread(
                    functools.partial(
                        _call_llm_for_review,
                        reviewer_dict,
                        review_prompt,
                        company_id=sprint.company_id,
                        sprint_id=sprint.id,
                        backlog_item_id=item.id,
                    )
                )

                normalized_review = (review_result or "").strip()
                verdict = classify_review_result(normalized_review)
                if verdict is None:
                    # Only an explicit APPROVED: closes a task; anything else
                    # is a failed review, retried and then blocked below.
                    raise ValueError(
                        "reviewer answered without APPROVED:/NEEDS WORK: — "
                        f"{normalized_review[:120]!r}"
                    )
                needs_work = verdict == RESULT_NEEDS_WORK
                # Approved, but a high-risk delivery waits for a person at
                # every autonomy level (services/risk.py).
                risk_check = (
                    None
                    if needs_work
                    else await asyncio.to_thread(
                        record._delivery_risk, sprint.company_id, item.id
                    )
                )
                if risk_check is None and not needs_work:
                    # No recorded check (the run was not recorded): unknown is
                    # not low, so the paths the deliverable declares decide; a
                    # claim, which can only raise the tier.
                    risk_check = risk.classify(
                        [f["path"] for f in parse_file_manifest(deliverable)]
                    )
                risk_hold = risk_check is not None and risk.is_high(
                    risk_check.get("tier")
                )
                company = session.get(Company, sprint.company_id)
                person_reason = sprint_control.person_acceptance_reason(
                    company.autonomy_level, engine, sprint.company_id, item.id
                )
                if not needs_work and not risk_hold and person_reason:
                    risk_hold = True
                    risk_check = {"reasons": [person_reason]}

                previous_reworks = task_record.rework_requests_by(reviewer_dict["name"])
                autopilot_rework_allowed = (
                    needs_work
                    and AUTOPILOT_QA_REWORK_ENABLED
                    and previous_reworks < AUTOPILOT_MAX_REWORK_CYCLES
                )
                if needs_work:
                    review_status = "todo" if autopilot_rework_allowed else "blocked"
                else:
                    review_status = "blocked" if risk_hold else "done"
                # The verdict, then what it leads to: each an event and its comment.
                task_events.record(
                    session,
                    item,
                    "reviewed",
                    reviewer_dict["role"],
                    author=reviewer_dict["name"],
                    status=review_status,
                    text=normalized_review[:2000],
                    payload={"verdict": "needs_work" if needs_work else "approved"},
                )
                if needs_work and autopilot_rework_allowed:
                    task_events.record(
                        session,
                        item,
                        "sent_back",
                        "system",
                        text=f"Autopilot rework scheduled after QA feedback ({previous_reworks + 1}/{AUTOPILOT_MAX_REWORK_CYCLES}).",
                    )
                elif needs_work:
                    task_events.record(
                        session,
                        item,
                        "blocked",
                        "system",
                        text="QA quality gate failed — task blocked pending manual triage.",
                    )
                    quality_gate_blocked_ids.add(item.id)

                risk_note = None
                if risk_hold:
                    risk_reasons = "; ".join(risk_check.get("reasons") or [])
                    risk_note = (
                        f"{RISK_HOLD_PREFIX} ({risk_reasons or 'high tier'}). "
                        "Mark it done to accept it, or send it back to work."
                    )
                    task_events.record(
                        session, item, "risk_hold", "system", text=risk_note
                    )

                session.add(item)
                session.add(
                    messages._message(
                        sprint.company_id,
                        sprint.id,
                        item.id,
                        reviewer_dict["name"],
                        reviewer_dict["role"],
                        item.assignee or "Developer",
                        "request" if needs_work else "decision",
                        normalized_review[:2000],
                    )
                )
                if risk_note is not None:
                    # To a person, not the ScrumMaster: its triage decides
                    # retries, and this delivery was approved.
                    session.add(
                        messages._message(
                            sprint.company_id,
                            sprint.id,
                            item.id,
                            DELIVERY_GATE_AUTHOR,
                            DELIVERY_GATE_AUTHOR,
                            "Manager",
                            "escalation",
                            risk_note,
                        )
                    )
                escalation = None
                if needs_work and not autopilot_rework_allowed:
                    escalation = messages._message(
                        sprint.company_id,
                        sprint.id,
                        item.id,
                        reviewer_dict["name"],
                        reviewer_dict["role"],
                        SCRUM_MASTER_ROLE,
                        "escalation",
                        f'"{item.title}" is blocked: still not accepted after '
                        f"{previous_reworks} rework(s). Last review: {normalized_review[:500]}",
                    )
                    session.add(escalation)
                session.commit()
                if escalation is not None:
                    await asyncio.to_thread(
                        messages._triage_blocked_task,
                        sprint.company_id,
                        sprint.id,
                        item.id,
                        f"Still not accepted after {previous_reworks} rework(s). "
                        f"Last review: {normalized_review[:500]}",
                        escalation.id,
                    )

                if needs_work:
                    if autopilot_rework_allowed:
                        await publish_glassroom(
                            nats_client,
                            sprint.id,
                            {
                                "type": "status",
                                "semantic_type": "task.rework_scheduled",
                                "source": "task_worker",
                                "agent": "System",
                                "role": "System",
                                "task_id": item.id,
                                "task_status": "todo",
                                "phase": "rework",
                                "rework_count": previous_reworks + 1,
                                "rework_limit": AUTOPILOT_MAX_REWORK_CYCLES,
                                "task": f"Reviewed: {item.title}",
                                "message": f"🔁 Autopilot accepted QA feedback for: {item.title}. Rework attempt {previous_reworks + 1}/{AUTOPILOT_MAX_REWORK_CYCLES}.",
                                "preview": normalized_review[:200],
                            },
                        )
                        logger.info(
                            "Reviewed item=%s → todo (autopilot rework) by %s [reworks=%s/%s]",
                            item.id,
                            reviewer_dict["name"],
                            previous_reworks + 1,
                            AUTOPILOT_MAX_REWORK_CYCLES,
                        )
                    else:
                        await publish_glassroom(
                            nats_client,
                            sprint.id,
                            {
                                "type": "error",
                                "semantic_type": "task.blocked",
                                "source": "task_worker",
                                "agent": reviewer_dict["name"],
                                "role": reviewer_dict["role"],
                                "task_id": item.id,
                                "task_status": "blocked",
                                "phase": "qa_gate",
                                "rework_count": previous_reworks + 1,
                                "rework_limit": AUTOPILOT_MAX_REWORK_CYCLES,
                                "task": f"Reviewed: {item.title}",
                                "message": f"❌ {reviewer_dict['name']} requested rework for: {item.title}. Blocked for manual triage.",
                                "preview": normalized_review[:200],
                            },
                        )
                        logger.info(
                            "Reviewed item=%s → blocked (needs work gate) by %s [reworks=%s/%s]",
                            item.id,
                            reviewer_dict["name"],
                            previous_reworks + 1,
                            AUTOPILOT_MAX_REWORK_CYCLES,
                        )
                elif risk_hold:
                    await publish_glassroom(
                        nats_client,
                        sprint.id,
                        {
                            "type": "status",
                            "semantic_type": "task.awaiting_person",
                            "source": "task_worker",
                            "agent": DELIVERY_GATE_AUTHOR,
                            "role": "System",
                            "task_id": item.id,
                            "task_status": "blocked",
                            "phase": "risk_hold",
                            "risk_tier": risk_check.get("tier"),
                            "task": f"Reviewed: {item.title}",
                            "message": f"🛡️ {reviewer_dict['name']} approved {item.title}; "
                            "high-risk delivery, waiting for a person to accept it.",
                            "preview": "; ".join(risk_check.get("reasons") or [])[:200],
                        },
                    )
                    logger.info(
                        "Reviewed item=%s → blocked (high risk, awaiting a person)",
                        item.id,
                    )
                    await close._finalize_sprint_if_ready(nats_client, sprint.id)
                else:
                    await publish_glassroom(
                        nats_client,
                        sprint.id,
                        {
                            "type": "agent_complete",
                            "semantic_type": "task.approved",
                            "source": "task_worker",
                            "agent": reviewer_dict["name"],
                            "role": reviewer_dict["role"],
                            "task_id": item.id,
                            "task_status": "done",
                            "phase": "qa_approved",
                            "task": f"Reviewed: {item.title}",
                            "message": f"✅ {reviewer_dict['name']} approved: {item.title} → done",
                            "preview": normalized_review[:200],
                        },
                    )

                    logger.info(
                        "Reviewed item=%s → done by %s", item.id, reviewer_dict["name"]
                    )
                    await close._finalize_sprint_if_ready(nats_client, sprint.id)

            except Exception as e:  # noqa: BLE001 - Isolate review failures to the affected item.
                logger.warning("Review failed for item=%s: %s", item.id, e)
                credit = getattr(e, "credit_provider", None)
                if credit and sprint_control.pause_for_credit(
                    session, sprint.id, credit
                ):
                    session.commit()
                    await sprint_control.publish_credit_pause(
                        nats_client, sprint.id, credit
                    )
                if credit:
                    continue

                task_events.record(
                    session,
                    item,
                    "review_failed",
                    "system",
                    text=f"QA review failed — will retry automatically. Reason: {str(e)[:300]}",
                )
                session.commit()

                await publish_glassroom(
                    nats_client,
                    sprint.id,
                    {
                        "type": "error",
                        "semantic_type": "task.review_error",
                        "source": "task_worker",
                        "agent": reviewer_dict["name"],
                        "role": reviewer_dict["role"],
                        "task_id": item.id,
                        "task_status": item.status,
                        "phase": "qa_review",
                        "task": f"Reviewing: {item.title}",
                        "message": f"❌ {reviewer_dict['name']} review failed for: {item.title}. Will retry.",
                    },
                )

        # --- Retry: blocked → todo (one retry only) ---
        blocked = session.exec(
            select(BacklogItem).where(BacklogItem.status == "blocked")
        ).all()
        # Sprints holding an item that stays blocked until a person acts may
        # have nothing left to move; they are checked for closing below.
        settled_sprint_ids: set[int] = set()
        histories = task_projection.histories_for(session, list(blocked))

        for item in blocked:
            item_sprint = session.get(Sprint, item.sprint_id)
            if item_sprint is not None and sprint_control.is_halted(item_sprint.status):
                continue
            if item.id in quality_gate_blocked_ids:
                settled_sprint_ids.add(item.sprint_id)
                continue

            next_step = task_projection.record_of(session, item, histories).next_step()
            if next_step is None:
                settled_sprint_ids.add(item.sprint_id)
                continue
            if next_step == "frozen":
                continue

            if next_step == "review":
                task_events.record(
                    session,
                    item,
                    "recovered",
                    "system",
                    status="in_review",
                    text="Autonomous recovery detected a blocked task with an existing deliverable and no explicit block reason. Returning it to QA review.",
                )

                sprint = session.get(Sprint, item.sprint_id)
                if sprint:
                    await publish_glassroom(
                        nats_client,
                        sprint.id,
                        {
                            "type": "status",
                            "semantic_type": "task.recovered",
                            "source": "task_worker",
                            "agent": "System",
                            "role": "System",
                            "task_id": item.id,
                            "task_status": "in_review",
                            "phase": "blocked_recovery",
                            "task": item.title,
                            "message": f"♻️ Recovered blocked task with existing deliverable: {item.title} moved back to QA review.",
                        },
                    )

                logger.info(
                    "Recovered blocked item=%s back to in_review because a deliverable already exists",
                    item.id,
                )
                continue

            task_events.record(
                session,
                item,
                "retried",
                "system",
                status="todo",
                text="Automatic retry — re-queuing blocked task.",
            )

            sprint = session.get(Sprint, item.sprint_id)
            if sprint:
                await publish_glassroom(
                    nats_client,
                    sprint.id,
                    {
                        "type": "status",
                        "semantic_type": "task.retry_queued",
                        "source": "task_worker",
                        "agent": "System",
                        "role": "System",
                        "task_id": item.id,
                        "task_status": "todo",
                        "phase": "retry",
                        "task": item.title,
                        "message": f"🔄 Retrying blocked task: {item.title}",
                    },
                )

            logger.info("Retrying blocked item=%s", item.id)

        session.commit()

        # Only a sprint with nothing left to move is worth the retrospective.
        closing_candidates = [
            sprint_id
            for sprint_id in (
                session.exec(
                    select(Sprint.id).where(
                        Sprint.id.in_(settled_sprint_ids), Sprint.status == "active"
                    )
                ).all()
                if settled_sprint_ids
                else []
            )
            if dependencies.sprint_settled(session, sprint_id)
        ]
        session.commit()

    # A sprint whose last open task just got blocked for a person closes now,
    # with its retrospective; approvals close theirs on the spot.
    for sprint_id in closing_candidates:
        await close._finalize_sprint_if_ready(nats_client, sprint_id)

    # Re-dispatch any items that just went back to todo
    await _dispatch_todo_items(nats_client)


def _call_llm_for_review(
    agent: dict[str, Any],
    prompt: str,
    *,
    company_id: int | None = None,
    sprint_id: int | None = None,
    backlog_item_id: int | None = None,
) -> str:
    """Synchronous LLM call for code review (runs in thread).

    When ``company_id`` is provided the review is written to ``audit_log`` with
    ``role=QA`` and ``result_type`` (APPROVED / NEEDS_WORK) so the QA rate KPI
    reads from the audit trail instead of its comment-scan fallback.
    """
    client = LLMProxyClient(os.getenv("LLM_PROXY_URL", "http://llm-proxy:5100"))
    client.max_tokens, client.company_id = REVIEW_MAX_TOKENS, company_id
    messages = [{"role": "user", "content": prompt}]
    if agent.get("system_prompt"):
        messages.insert(0, {"role": "system", "content": agent["system_prompt"]})
    model = model_routing.resolve_agent_model(agent, default_role="QA")
    result = client.chat(
        model=model,
        messages=messages,
        role=agent.get("role", "QA"),
    )

    failure = _llm_failure(result, model)
    if failure:
        raise execute.LLMCallError(failure, sprint_control.credit_failure(result))

    content = _llm_content(result)

    if company_id is not None:
        record_agent_turn(
            company_id=company_id,
            sprint_id=sprint_id,
            backlog_item_id=backlog_item_id,
            agent={**agent, "model": result.get("served_model") or model},
            action=ACTION_QA_REVIEW,
            llm_result=result,
            result_type=classify_review_result(content),
            result_preview=content,
        )
    return content


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
