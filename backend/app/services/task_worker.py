"""
Task Worker — NATS consumer for backlog item execution by AI agents.

Listens for ``task.assign`` events published by the transactional outbox
and dispatches each task to the appropriate AI agent based on the backlog
item's ``assignee`` field.  The agent's LLM model and system prompt are
loaded from the ``agents`` table so each role uses its configured model.

Workflow per task:
1. Receive ``task.assign`` NATS message with ``backlog_item_id`` and ``sprint_id``.
2. Load the BacklogItem from Postgres (title, description, assignee).
3. Look up the Agent row matching the assignee role + company.
4. Call the LLM proxy with the agent's model and system prompt.
5. Update the backlog item: status → ``in_progress`` → ``in_review``,
   append a comment with the agent's deliverable.
6. Publish a Glassroom event so the UI shows real-time progress.
"""

import asyncio
import functools
import json
import os
from datetime import datetime, timezone
from typing import Any, Dict, Optional

from sqlmodel import Session, select, text

from database import engine
from models import BacklogItem, Sprint, Agent
from llm_proxy_client import LLMProxyClient
from logging_config import setup_logger
from nats_client import NATSClient

logger = setup_logger("fabrique-task-worker")


# ---------------------------------------------------------------------------
# LLM interaction — runs in a thread to avoid blocking the event loop
# ---------------------------------------------------------------------------

def _execute_task(
    item: Dict[str, Any],
    agent: Dict[str, Any],
    sprint_name: str,
) -> str:
    """
    Call the LLM to produce a deliverable for the given backlog item.

    Runs synchronously (called via ``asyncio.to_thread``).
    Returns the LLM response text.
    """
    llm_url = os.getenv("LLM_PROXY_URL", "http://llm-proxy:5100")
    client = LLMProxyClient(base_url=llm_url)
    client.timeout = 120  # Agent tasks may produce long outputs

    system_prompt = agent.get("system_prompt") or (
        f"You are a {agent.get('seniority', 'Senior')} {agent.get('role', 'Developer')} "
        f"specializing in {agent.get('specialty', 'software engineering')}. "
        "Produce clear, actionable, production-quality deliverables."
    )

    user_prompt = (
        f"## Task: {item['title']}\n\n"
        f"{item.get('description', '')}\n\n"
        f"Sprint: {sprint_name}\n"
        f"Priority: {item.get('priority', 'medium')}\n\n"
        "Produce a complete, professional deliverable for this task. "
        "Be thorough and specific to the project context."
    )

    model = agent.get("model", "deepseek-coder")
    role = agent.get("role", "Dev")

    logger.info(
        "Agent %s (%s) working on: %s [model=%s]",
        agent.get("name") or role, role, item["title"], model,
    )

    result = client.chat(
        messages=[
            {"role": "system", "content": system_prompt},
            {"role": "user", "content": user_prompt},
        ],
        model=model,
        role=role,
    )

    if result.get("error"):
        raise RuntimeError(f"LLM error: {result['error']}")

    content = (
        result.get("choices", [{}])[0]
        .get("message", {})
        .get("content", "")
    )
    return content.strip()


# ---------------------------------------------------------------------------
# Database helpers
# ---------------------------------------------------------------------------

def _load_task_context(backlog_item_id: int) -> Optional[Dict[str, Any]]:
    """Load the backlog item, its sprint, and the matching agent from Postgres."""
    with Session(engine) as session:
        item = session.get(BacklogItem, backlog_item_id)
        if not item:
            logger.warning("BacklogItem %s not found — skipping", backlog_item_id)
            return None

        sprint = session.get(Sprint, item.sprint_id)
        if not sprint:
            logger.warning("Sprint %s not found for item %s", item.sprint_id, backlog_item_id)
            return None

        # Find the agent matching this assignee role within the company.
        agent_row = session.exec(
            select(Agent).where(
                Agent.company_id == sprint.company_id,
                Agent.role == (item.assignee or "Dev"),
            )
        ).first()

        agent_dict = {}
        if agent_row:
            agent_dict = {
                "role": agent_row.role,
                "name": agent_row.name,
                "model": agent_row.model,
                "seniority": agent_row.seniority,
                "specialty": agent_row.specialty,
                "system_prompt": agent_row.system_prompt,
            }
        else:
            # Fallback: use default config if no matching agent found.
            agent_dict = {
                "role": item.assignee or "Dev",
                "name": "",
                "model": "deepseek-coder",
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
            },
            "sprint": {
                "id": sprint.id,
                "name": sprint.sprint_name,
                "company_id": sprint.company_id,
            },
            "agent": agent_dict,
        }


def _update_item_status(
    item_id: int,
    status: str,
    comment_text: Optional[str] = None,
    comment_author: Optional[str] = None,
):
    """Update a backlog item's status and optionally append a comment."""
    with Session(engine) as session:
        item = session.get(BacklogItem, item_id)
        if not item:
            return

        item.status = status
        item.updated_at = datetime.now(timezone.utc)

        if comment_text and comment_author:
            existing = item.comments or []
            if isinstance(existing, str):
                try:
                    existing = json.loads(existing)
                except (json.JSONDecodeError, TypeError):
                    existing = []
            existing.append({
                "author": comment_author,
                "text": comment_text[:4000],
                "created_at": datetime.now(timezone.utc).isoformat(),
            })
            item.comments = existing

        session.add(item)
        session.commit()


# ---------------------------------------------------------------------------
# NATS consumer — the main entry point started from main.py
# ---------------------------------------------------------------------------

async def process_backlog_tasks(nats_client: NATSClient) -> None:
    """
    Background coroutine that subscribes to ``task.assign`` and processes
    each backlog item by invoking the appropriate AI agent.

    The LLM call is offloaded to a thread via ``asyncio.to_thread()`` to keep
    the event loop free for other NATS callbacks and SSE streaming.
    """

    async def _on_task_assign(msg, data_from_nats=None):
        data: Dict[str, Any] = {}
        try:
            data = data_from_nats or json.loads(msg.data.decode())
            backlog_item_id = data.get("backlog_item_id")
            sprint_id = data.get("sprint_id")

            if not backlog_item_id:
                logger.warning("task.assign message missing backlog_item_id: %s", data)
                return

            logger.info("Received task.assign: item=%s sprint=%s", backlog_item_id, sprint_id)

            # 1. Load context from DB.
            ctx = await asyncio.to_thread(_load_task_context, backlog_item_id)
            if not ctx:
                return

            item = ctx["item"]
            agent = ctx["agent"]
            sprint = ctx["sprint"]

            # 2. Mark as in_progress and notify glassroom.
            await asyncio.to_thread(
                _update_item_status, item["id"], "in_progress"
            )

            await _publish_glassroom(nats_client, sprint["id"], {
                "type": "agent_start",
                "agent": agent.get("name") or agent["role"],
                "role": agent["role"],
                "task": item["title"],
                "message": f"🚀 {agent.get('name') or agent['role']} started working on: {item['title']}",
            })

            # 3. Call the LLM (in a thread).
            deliverable = await asyncio.to_thread(
                _execute_task, item, agent, sprint["name"],
            )

            # 4. Save result and mark as in_review.
            await asyncio.to_thread(
                _update_item_status,
                item["id"],
                "in_review",
                comment_text=deliverable,
                comment_author=agent.get("name") or agent["role"],
            )

            await _publish_glassroom(nats_client, sprint["id"], {
                "type": "agent_complete",
                "agent": agent.get("name") or agent["role"],
                "role": agent["role"],
                "task": item["title"],
                "message": f"✅ {agent.get('name') or agent['role']} completed: {item['title']}",
                "preview": deliverable[:200] if deliverable else "",
            })

            logger.info(
                "Task completed: item=%s by %s (%s) — %d chars",
                backlog_item_id,
                agent.get("name") or agent["role"],
                agent.get("model", "?"),
                len(deliverable),
            )

        except Exception as e:
            logger.error(
                "Error processing task.assign (item=%s): %s",
                data.get("backlog_item_id", "?"),
                e,
                exc_info=True,
            )

            # Mark as blocked on failure.
            if data.get("backlog_item_id"):
                try:
                    await asyncio.to_thread(
                        _update_item_status,
                        data["backlog_item_id"],
                        "blocked",
                        comment_text=f"Agent error: {str(e)[:500]}",
                        comment_author="System",
                    )
                except Exception:
                    pass

            if data.get("sprint_id"):
                await _publish_glassroom(nats_client, data["sprint_id"], {
                    "type": "error",
                    "message": f"❌ Task failed: {str(e)[:200]}",
                })

        finally:
            pass  # msg.ack() is handled by NATSClient._listen()

    logger.info("Task Worker subscribed to task.assign")
    await nats_client.subscribe("task.assign", _on_task_assign)


async def _publish_glassroom(
    nats_client: NATSClient,
    sprint_id: int,
    event: Dict[str, Any],
):
    """Publish a real-time event to the Glassroom SSE stream for a sprint."""
    try:
        event["timestamp"] = datetime.now(timezone.utc).isoformat()
        await nats_client.publish(f"glassroom.{sprint_id}", event)
    except Exception as e:
        logger.warning("Failed to publish glassroom event: %s", e)
