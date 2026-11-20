"""Running one claimed task (ADR 0014, the task worker's intake stage).

The intake (``intake._on_task_assign``) loads a task with ``_load_task_context``
(nothing for a halted sprint or a task assigned outside the sprint's team) and
claims it; ``_run_claimed_task`` then runs it, in these steps:

- ``_announce_claim``: the ``task.claimed`` and context events on Glassroom;
- ``_gather_context``: the memory, lessons, procedures, completed tasks and
  requirements, fetched concurrently (no external CLI is consulted: the
  second opinion is the Architect's brief, issue #750);
- ``_announce_context``: the memory event;
- ``_produce``: the Architect's brief, then the company's console producer
  for the role, else the LLM, and the deliverable on Glassroom;
- ``_still_in_progress``: a completion for a task that was frozen or moved
  meanwhile is discarded (``_load_item_runtime_state``,
  ``_sync_manual_freeze_state``);
- ``_passes_quality_gate``: the text checks, which send the task back;
- ``_hand_to_qa``: the artifacts to the daemon, the task ``in_review`` with the
  deliverable, the handoff message and the Glassroom events.

Moved out of ``task_worker``, which still re-exports these names.
"""

import asyncio
from typing import Any

from database import engine
from logging_config import setup_logger
from models import Agent, BacklogItem, Sprint
from nats_client import NATSClient
from sqlmodel import Session, select

from services.loop import console_host, sprint_control
from services.loop.answers import gate_reason as _gate_reason
from services.loop.worker import (
    board_writes,
    deliver,
    execute,
    messages,
    prompt,
    quality_gate,
    wiki_section,
)
from services.models import model_routing
from services.planning import spec_planning, sprint_team
from services.platform.glassroom import publish_glassroom
from services.tasks import task_events, task_projection

logger = setup_logger("fabrique-task-worker")


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


async def _run_claimed_task(
    nats_client: NATSClient, backlog_item_id: Any, ctx: dict[str, Any]
) -> None:
    """Run a task this replica claimed, up to its handoff to QA."""
    item = ctx["item"]
    agent = ctx["agent"]
    sprint = ctx["sprint"]

    await _announce_claim(nats_client, item, agent, sprint)
    context = await _gather_context(nats_client, item, agent, sprint)
    await _announce_context(nats_client, item, agent, sprint, context)
    deliverable = await _produce(nats_client, item, agent, sprint, context)

    if not await _still_in_progress(item["id"]):
        return
    if not await _passes_quality_gate(nats_client, item, agent, sprint, deliverable):
        return

    await _hand_to_qa(nats_client, item, agent, sprint, deliverable)

    logger.info(
        "Task completed: item=%s by %s (%s) — %d chars",
        backlog_item_id,
        agent.get("name") or agent["role"],
        agent.get("model", "?"),
        len(deliverable),
    )


async def _announce_claim(
    nats_client: NATSClient,
    item: dict[str, Any],
    agent: dict[str, Any],
    sprint: dict[str, Any],
) -> None:
    """The ``task.claimed`` event, then the context-gathering one."""
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


async def _gather_context(
    nats_client: NATSClient,
    item: dict[str, Any],
    agent: dict[str, Any],
    sprint: dict[str, Any],
) -> dict[str, Any]:
    """Fetch memory context and cross-task context concurrently (non-blocking)."""
    (
        memory_ctx,
        lessons_ctx,
        procedures_ctx,
        completed_ctx,
        requirements_ctx,
    ) = await asyncio.gather(
        # The project wiki first; the vector memory only as its fallback (#445).
        asyncio.to_thread(
            wiki_section.wiki_then_memory,
            item,
            agent,
            sprint["company_id"],
            engine=engine,
            fetch_memory=prompt._fetch_memory_context,
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
    )
    return {
        "memory": memory_ctx["memory"],
        "wiki": memory_ctx["wiki"],
        "lessons": lessons_ctx,
        "procedures": procedures_ctx,
        "completed": completed_ctx,
        "requirements": requirements_ctx,
    }


async def _announce_context(
    nats_client: NATSClient,
    item: dict[str, Any],
    agent: dict[str, Any],
    sprint: dict[str, Any],
    context: dict[str, Any],
) -> None:
    """The memory event when there was memory."""
    if context["memory"] or context.get("wiki") or context["lessons"]:
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


async def _produce(
    nats_client: NATSClient,
    item: dict[str, Any],
    agent: dict[str, Any],
    sprint: dict[str, Any],
    context: dict[str, Any],
) -> str:
    """The Architect's brief, then the deliverable from the producer, sanitized."""
    requirements_ctx = context["requirements"]
    architect_advice = await execute._architect_brief(nats_client, item, agent, sprint)

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
            "message": f"🛠️ {agent.get('name') or agent['role']} is executing autonomously on: {item['title']}",
        },
    )

    # The company's console producer for the role, else the LLM (in a thread).
    deliverable = await console_host.produce(
        nats_client, item, agent, sprint, requirements_ctx, architect_advice
    ) or await asyncio.to_thread(
        execute._execute_task,
        item,
        agent,
        sprint["name"],
        context["memory"],
        context["completed"],
        architect_advice or None,
        sprint["company_id"],
        sprint["id"],
        context["lessons"] or None,
        context["procedures"] or None,
        requirements_ctx or None,
        context.get("wiki") or "",
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
    return deliverable


async def _still_in_progress(item_id: int) -> bool:
    """False, with the reason logged, for a completion that arrived stale."""
    latest_state = await asyncio.to_thread(_load_item_runtime_state, item_id)
    if not latest_state:
        logger.info("Skipping stale completion for missing item=%s", item_id)
        return False
    if latest_state.get("manually_frozen"):
        await asyncio.to_thread(_sync_manual_freeze_state, item_id)
        logger.info(
            "Discarding stale completion for manually frozen item=%s",
            item_id,
        )
        return False
    if latest_state.get("status") != "in_progress":
        logger.info(
            "Discarding stale completion for item=%s with status=%s",
            item_id,
            latest_state.get("status"),
        )
        return False
    return True


async def _passes_quality_gate(
    nats_client: NATSClient,
    item: dict[str, Any],
    agent: dict[str, Any],
    sprint: dict[str, Any],
    deliverable: str,
) -> bool:
    """The text checks; a deliverable that fails them is sent back with the reason."""
    is_valid, validation_reason = quality_gate._validate_minimum_deliverable(
        item, deliverable, agent.get("tool_edits")
    )
    if not is_valid:
        reason = _gate_reason(validation_reason, agent.get("finish_reason"))
        await quality_gate._send_back_from_quality_gate(
            nats_client, item, agent, sprint, deliverable, reason
        )
        return False
    return True


async def _hand_to_qa(
    nats_client: NATSClient,
    item: dict[str, Any],
    agent: dict[str, Any],
    sprint: dict[str, Any],
    deliverable: str,
) -> None:
    """Deliver the artifacts, move the task to ``in_review`` and tell QA."""
    review_payload = await deliver._deliver_artifacts(
        nats_client, item, agent, sprint, deliverable
    )

    # Save result and mark as in_review (a reviewable comment even if
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
