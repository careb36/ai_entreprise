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
import re
from datetime import datetime, timezone
from typing import Any, Dict, Optional

from sqlmodel import Session, select, text

from database import engine
from models import BacklogItem, Sprint, Agent, ProjectDiscovery
from llm_proxy_client import LLMProxyClient
from logging_config import setup_logger
from nats_client import NATSClient

logger = setup_logger("fabrique-task-worker")

MAX_QA_REVIEW_FAILURES = 3
MAX_QA_REWORK_CYCLES = 2
MIN_DELIVERABLE_WORDS = 80
MIN_DOC_DELIVERABLE_WORDS = 180
AUTOPILOT_QA_REWORK_ENABLED = os.getenv("AUTOPILOT_QA_REWORK_ENABLED", "true").lower() in {"1", "true", "yes", "on"}
AUTOPILOT_MAX_REWORK_CYCLES = int(os.getenv("AUTOPILOT_MAX_REWORK_CYCLES", str(MAX_QA_REWORK_CYCLES)))
ALLOWED_ARTIFACT_EXTENSIONS = {
    ".md", ".txt", ".json", ".yaml", ".yml", ".toml",
    ".py", ".js", ".ts", ".tsx", ".jsx", ".css", ".html",
}


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


def _validate_minimum_deliverable(item: Dict[str, Any], deliverable: str) -> tuple[bool, str]:
    """Apply lightweight quality checks before sending output to QA review."""
    text = (deliverable or "").strip()
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


def _resolve_workspace_root(company_id: int) -> Optional[str]:
    """Best-effort lookup of a writable workspace root for artifact generation."""
    explicit = os.getenv("AGENT_WORKSPACE_ROOT")
    if explicit:
        return explicit

    with Session(engine) as session:
        discovery = session.exec(
            select(ProjectDiscovery)
            .where(ProjectDiscovery.company_id == company_id)
            .order_by(text("created_at DESC"))
        ).first()
        if discovery and discovery.project_path:
            return discovery.project_path

    return None


def _sanitize_relative_path(path: str) -> Optional[str]:
    """Normalize a relative path and reject unsafe values."""
    p = (path or "").strip().replace("\\", "/")
    if not p or p.startswith("/") or ".." in p.split("/"):
        return None
    return p


def _normalize_artifact_content(path: str, deliverable: str) -> str:
    """Trim wrapper text so persisted files contain only final artifact content."""
    text_content = (deliverable or "").strip().replace("\r\n", "\n")

    fenced = re.search(r"```(?:[a-zA-Z0-9_+-]+)?\s*(.*?)```", text_content, flags=re.DOTALL)
    if fenced and len(fenced.group(1).strip()) > 40:
        text_content = fenced.group(1).strip()

    if path.lower().endswith(".md"):
        text_content = re.sub(r"(?m)^\*\*\*\s*$", "", text_content).strip()
        heading = re.search(r"(?m)^#\s+", text_content)
        if heading and heading.start() > 0:
            text_content = text_content[heading.start():].strip()

    return text_content


def _extract_artifact_targets(item: Dict[str, Any], deliverable: str) -> list[Dict[str, str]]:
    """Infer concrete file targets from task metadata and generated content."""
    title_desc = f"{item.get('title', '')} {item.get('description', '')}"
    candidates = re.findall(r"([A-Za-z0-9_./-]+\.[A-Za-z0-9]+)", title_desc)

    artifacts: list[Dict[str, str]] = []
    for c in candidates:
        rel = _sanitize_relative_path(c)
        if not rel:
            continue
        _, ext = os.path.splitext(rel)
        if ext.lower() not in ALLOWED_ARTIFACT_EXTENSIONS:
            continue
        artifacts.append({"path": rel, "content": _normalize_artifact_content(rel, deliverable)})

    # Remove duplicates while preserving order.
    dedup: list[Dict[str, str]] = []
    seen: set[str] = set()
    for a in artifacts:
        if a["path"] in seen:
            continue
        seen.add(a["path"])
        dedup.append(a)

    return dedup


def _build_review_payload(deliverable: str, artifact_targets: list[Dict[str, str]]) -> str:
    """Prefer normalized artifact content for QA review when files were materialized."""
    if not artifact_targets:
        return (deliverable or "").strip()

    sections: list[str] = []
    for artifact in artifact_targets:
        path = artifact.get("path", "artifact")
        content = (artifact.get("content") or "").strip()
        sections.append(f"# Artifact: {path}\n\n{content}")

    return "\n\n".join(section for section in sections if section.strip())


def _extract_latest_reviewable_comment(comments: Any) -> str:
    """Return the latest non-system, non-QA deliverable comment for review."""
    if isinstance(comments, str):
        try:
            comments = json.loads(comments)
        except Exception:
            comments = []

    if not isinstance(comments, list):
        return ""

    for comment in reversed(comments):
        author = str(comment.get("author", "")).strip().lower()
        text = str(comment.get("text", "")).strip()
        if not text:
            continue
        if text.lower().startswith("freeze after") or "sleep-induced churn" in text.lower():
            continue
        if author in {"system", "qa"}:
            continue
        if text.upper().startswith("APPROVED:") or text.upper().startswith("NEEDS WORK:"):
            continue
        return text

    return ""


def _has_manual_freeze_marker(comments: Any) -> bool:
    """Detect operator comments that intentionally freeze a task out of automation."""
    if isinstance(comments, str):
        try:
            comments = json.loads(comments)
        except Exception:
            comments = []

    if not isinstance(comments, list):
        return False

    return any(
        "sleep-induced churn" in str(comment.get("text", "")).lower()
        or str(comment.get("text", "")).strip().lower().startswith("freeze after")
        for comment in comments
    )


def _load_item_runtime_state(item_id: int) -> Optional[Dict[str, Any]]:
    """Load the latest mutable state so stale in-flight work can be discarded."""
    with Session(engine) as session:
        item = session.get(BacklogItem, item_id)
        if not item:
            return None

        comments = item.comments or []
        if isinstance(comments, str):
            try:
                comments = json.loads(comments)
            except Exception:
                comments = []

        return {
            "status": item.status,
            "comments": comments,
            "manually_frozen": _has_manual_freeze_marker(comments),
        }


def _sync_manual_freeze_state(item_id: int) -> bool:
    """Ensure manually frozen items stay blocked and out of automation."""
    with Session(engine) as session:
        item = session.get(BacklogItem, item_id)
        if not item:
            return False

        comments = item.comments or []
        if isinstance(comments, str):
            try:
                comments = json.loads(comments)
            except Exception:
                comments = []

        if not _has_manual_freeze_marker(comments):
            return False

        changed = item.status != "blocked"
        item.status = "blocked"
        item.updated_at = datetime.now(timezone.utc)
        session.add(item)
        session.commit()
        return changed


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
                "comments": item.comments or [],
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

            if _has_manual_freeze_marker(item.get("comments") or []):
                await asyncio.to_thread(_sync_manual_freeze_state, item["id"])
                logger.info("Skipping manually frozen assignment for item=%s", item["id"])
                return

            # Guard against duplicate task.assign events for the same item.
            if item.get("status") != "todo":
                logger.info(
                    "Skipping duplicate assignment for item=%s with status=%s",
                    item["id"],
                    item.get("status"),
                )
                return

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

            latest_state = await asyncio.to_thread(_load_item_runtime_state, item["id"])
            if not latest_state:
                logger.info("Skipping stale completion for missing item=%s", item["id"])
                return
            if latest_state.get("manually_frozen"):
                await asyncio.to_thread(_sync_manual_freeze_state, item["id"])
                logger.info("Discarding stale completion for manually frozen item=%s", item["id"])
                return
            if latest_state.get("status") != "in_progress":
                logger.info(
                    "Discarding stale completion for item=%s with status=%s",
                    item["id"],
                    latest_state.get("status"),
                )
                return

            is_valid, validation_reason = _validate_minimum_deliverable(item, deliverable)
            if not is_valid:
                await asyncio.to_thread(
                    _update_item_status,
                    item["id"],
                    "blocked",
                    comment_text=f"Pre-review quality gate failed: {validation_reason}",
                    comment_author="System",
                )

                await _publish_glassroom(nats_client, sprint["id"], {
                    "type": "error",
                    "agent": agent.get("name") or agent["role"],
                    "role": agent["role"],
                    "task": item["title"],
                    "message": f"❌ Pre-review gate blocked deliverable for: {item['title']} ({validation_reason}).",
                    "preview": deliverable[:200] if deliverable else "",
                })

                logger.warning(
                    "Pre-review gate blocked item=%s by %s: %s",
                    backlog_item_id,
                    agent.get("name") or agent["role"],
                    validation_reason,
                )
                return

            artifact_targets = await asyncio.to_thread(_extract_artifact_targets, item, deliverable)
            review_payload = await asyncio.to_thread(_build_review_payload, deliverable, artifact_targets)
            if artifact_targets:
                workspace_root = await asyncio.to_thread(_resolve_workspace_root, sprint["company_id"])
                if not workspace_root:
                    await asyncio.to_thread(
                        _update_item_status,
                        item["id"],
                        "in_progress",
                        comment_text="Artifact materialization skipped: workspace path not configured",
                        comment_author="System",
                    )
                    logger.warning("Artifact materialization skipped for item=%s: missing workspace root", item["id"])
                else:
                    try:
                        await nats_client.publish(
                            "workspace.generate",
                            {
                                "workspace_root": workspace_root,
                                "files": artifact_targets,
                                "interaction_id": f"task-{item['id']}",
                                "sprint_id": sprint["id"],
                            },
                        )
                        await asyncio.to_thread(
                            _update_item_status,
                            item["id"],
                            "in_progress",
                            comment_text=f"Artifact materialization requested for: {', '.join(a['path'] for a in artifact_targets)}",
                            comment_author="System",
                        )
                    except Exception as e:
                        await asyncio.to_thread(
                            _update_item_status,
                            item["id"],
                            "in_progress",
                            comment_text=f"Artifact materialization request failed: {str(e)[:240]}",
                            comment_author="System",
                        )
                        logger.warning("Artifact materialization request failed for item=%s: %s", item["id"], e)

            # 4. Save result and mark as in_review.
            await asyncio.to_thread(
                _update_item_status,
                item["id"],
                "in_review",
                comment_text=review_payload,
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

    # Periodic review loop — processes items stuck in in_review or blocked.
    await asyncio.sleep(10)  # Let initial tasks start first
    while True:
        try:
            await _review_cycle(nats_client)
        except Exception as e:
            logger.error("Review cycle error: %s", e)
        await asyncio.sleep(30)  # Run every 30s


async def _review_cycle(nats_client: NATSClient) -> None:
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

        for item in in_review:
            sprint = session.get(Sprint, item.sprint_id)
            if not sprint:
                continue

            # Find QA agent for this company (fallback to any reviewer)
            reviewer = session.exec(
                select(Agent).where(
                    Agent.company_id == sprint.company_id,
                    Agent.role == "QA",
                )
            ).first()

            reviewer_dict = {
                "role": reviewer.role if reviewer else "QA",
                "name": reviewer.name if reviewer else "QA",
                "model": reviewer.model if reviewer else "deepseek-coder",
                "seniority": reviewer.seniority if reviewer else "Senior",
                "system_prompt": reviewer.system_prompt if reviewer else None,
            }

            # Get the deliverable from comments
            comments = item.comments or []
            if isinstance(comments, str):
                try:
                    comments = json.loads(comments)
                except Exception:
                    comments = []

            if _has_manual_freeze_marker(comments):
                item.status = "blocked"
                item.updated_at = datetime.now(timezone.utc)
                session.add(item)
                session.commit()
                logger.info("Skipping QA review for manually frozen item=%s", item.id)
                continue

            deliverable = _extract_latest_reviewable_comment(comments)

            # Prevent infinite review loops when QA repeatedly fails.
            qa_review_failures = sum(
                1
                for c in comments
                if c.get("author") == "System" and "qa review failed" in c.get("text", "").lower()
            )
            if qa_review_failures >= MAX_QA_REVIEW_FAILURES:
                item.status = "blocked"
                item.updated_at = datetime.now(timezone.utc)
                session.add(item)
                session.commit()

                await _publish_glassroom(nats_client, sprint.id, {
                    "type": "error",
                    "agent": reviewer_dict["name"],
                    "role": reviewer_dict["role"],
                    "task": f"Reviewing: {item.title}",
                    "message": f"❌ {reviewer_dict['name']} review failed {MAX_QA_REVIEW_FAILURES} times for: {item.title}. Marked as blocked.",
                })

                logger.warning("Review retries exhausted for item=%s; marked blocked", item.id)
                continue

            if not deliverable:
                comments.append({
                    "author": "System",
                    "text": "QA review aborted: no deliverable found in task comments.",
                    "created_at": datetime.now(timezone.utc).isoformat(),
                })
                item.comments = comments
                item.status = "blocked"
                item.updated_at = datetime.now(timezone.utc)
                session.add(item)
                session.commit()

                await _publish_glassroom(nats_client, sprint.id, {
                    "type": "error",
                    "agent": reviewer_dict["name"],
                    "role": reviewer_dict["role"],
                    "task": f"Reviewing: {item.title}",
                    "message": f"❌ {reviewer_dict['name']} cannot review {item.title}: missing deliverable. Marked as blocked.",
                })

                continue

            # Build review prompt
            review_prompt = (
                f"You are reviewing the following deliverable for the task: '{item.title}'.\n"
                f"Description: {item.description or 'N/A'}\n\n"
                f"--- DELIVERABLE ---\n{deliverable[:3000]}\n--- END ---\n\n"
                "Provide a brief review (2-3 sentences). If acceptable, start with 'APPROVED:'. "
                "If not, start with 'NEEDS WORK:' and explain what's missing."
            )

            try:
                await _publish_glassroom(nats_client, sprint.id, {
                    "type": "agent_start",
                    "agent": reviewer_dict["name"],
                    "role": reviewer_dict["role"],
                    "task": f"Reviewing: {item.title}",
                    "message": f"🔍 {reviewer_dict['name']} is reviewing: {item.title}",
                })

                review_result = await asyncio.to_thread(
                    _call_llm_for_review, reviewer_dict, review_prompt
                )

                normalized_review = (review_result or "").strip()
                needs_work = normalized_review.upper().startswith("NEEDS WORK:")

                # Append review comment
                comments.append({
                    "author": reviewer_dict["name"],
                    "text": normalized_review[:2000],
                    "created_at": datetime.now(timezone.utc).isoformat(),
                })

                previous_reworks = sum(
                    1
                    for c in comments[:-1]
                    if c.get("author") == reviewer_dict["name"]
                    and str(c.get("text", "")).upper().startswith("NEEDS WORK:")
                )

                autopilot_rework_allowed = (
                    needs_work
                    and AUTOPILOT_QA_REWORK_ENABLED
                    and previous_reworks < AUTOPILOT_MAX_REWORK_CYCLES
                )

                item.comments = comments
                if needs_work:
                    item.status = "todo" if autopilot_rework_allowed else "blocked"
                else:
                    item.status = "done"
                item.updated_at = datetime.now(timezone.utc)

                if needs_work:
                    if autopilot_rework_allowed:
                        comments.append({
                            "author": "System",
                            "text": f"Autopilot rework scheduled after QA feedback ({previous_reworks + 1}/{AUTOPILOT_MAX_REWORK_CYCLES}).",
                            "created_at": datetime.now(timezone.utc).isoformat(),
                        })
                    else:
                        comments.append({
                            "author": "System",
                            "text": "QA quality gate failed — task blocked pending manual triage.",
                            "created_at": datetime.now(timezone.utc).isoformat(),
                        })
                        quality_gate_blocked_ids.add(item.id)

                    item.comments = comments

                session.add(item)
                session.commit()

                if needs_work:
                    if autopilot_rework_allowed:
                        await _publish_glassroom(nats_client, sprint.id, {
                            "type": "status",
                            "agent": "System",
                            "role": "System",
                            "task": f"Reviewed: {item.title}",
                            "message": f"🔁 Autopilot accepted QA feedback for: {item.title}. Rework attempt {previous_reworks + 1}/{AUTOPILOT_MAX_REWORK_CYCLES}.",
                            "preview": normalized_review[:200],
                        })
                        logger.info("Reviewed item=%s → todo (autopilot rework) by %s [reworks=%s/%s]", item.id, reviewer_dict["name"], previous_reworks + 1, AUTOPILOT_MAX_REWORK_CYCLES)
                    else:
                        await _publish_glassroom(nats_client, sprint.id, {
                            "type": "error",
                            "agent": reviewer_dict["name"],
                            "role": reviewer_dict["role"],
                            "task": f"Reviewed: {item.title}",
                            "message": f"❌ {reviewer_dict['name']} requested rework for: {item.title}. Blocked for manual triage.",
                            "preview": normalized_review[:200],
                        })
                        logger.info("Reviewed item=%s → blocked (needs work gate) by %s [reworks=%s/%s]", item.id, reviewer_dict["name"], previous_reworks + 1, AUTOPILOT_MAX_REWORK_CYCLES)
                else:
                    await _publish_glassroom(nats_client, sprint.id, {
                        "type": "agent_complete",
                        "agent": reviewer_dict["name"],
                        "role": reviewer_dict["role"],
                        "task": f"Reviewed: {item.title}",
                        "message": f"✅ {reviewer_dict['name']} approved: {item.title} → done",
                        "preview": normalized_review[:200],
                    })

                    logger.info("Reviewed item=%s → done by %s", item.id, reviewer_dict["name"])

            except Exception as e:
                logger.warning("Review failed for item=%s: %s", item.id, e)

                comments.append({
                    "author": "System",
                    "text": f"QA review failed — will retry automatically. Reason: {str(e)[:300]}",
                    "created_at": datetime.now(timezone.utc).isoformat(),
                })
                item.comments = comments
                item.updated_at = datetime.now(timezone.utc)
                session.add(item)
                session.commit()

                await _publish_glassroom(nats_client, sprint.id, {
                    "type": "error",
                    "agent": reviewer_dict["name"],
                    "role": reviewer_dict["role"],
                    "task": f"Reviewing: {item.title}",
                    "message": f"❌ {reviewer_dict['name']} review failed for: {item.title}. Will retry.",
                })

        # --- Retry: blocked → todo (one retry only) ---
        blocked = session.exec(
            select(BacklogItem).where(BacklogItem.status == "blocked")
        ).all()

        for item in blocked:
            if item.id in quality_gate_blocked_ids:
                continue

            comments = item.comments or []
            if isinstance(comments, str):
                try:
                    comments = json.loads(comments)
                except Exception:
                    comments = []

            if _has_manual_freeze_marker(comments):
                continue

            # QA quality-gate blocks require manual triage; do not auto-retry.
            qa_quality_gate_block = any(
                (c.get("author") == "System" and "manual triage" in str(c.get("text", "")).lower())
                or (c.get("author") == "System" and "qa quality gate failed" in str(c.get("text", "")).lower())
                or (c.get("author") == "QA" and str(c.get("text", "")).upper().startswith("NEEDS WORK:"))
                or str(c.get("text", "")).upper().startswith("NEEDS WORK:")
                for c in comments
            )
            if qa_quality_gate_block:
                continue

            # Only retry blocked items that failed for transient/system reasons.
            retryable_system_block = any(
                c.get("author") == "System"
                and (
                    "qa review failed" in str(c.get("text", "")).lower()
                    or "agent error:" in str(c.get("text", "")).lower()
                )
                for c in comments
            )
            if not retryable_system_block:
                continue

            # Only retry once — check if there's already a retry comment
            retry_count = sum(1 for c in comments if c.get("author") == "System" and "retry" in c.get("text", "").lower())
            if retry_count >= 1:
                continue

            comments.append({
                "author": "System",
                "text": "Automatic retry — re-queuing blocked task.",
                "created_at": datetime.now(timezone.utc).isoformat(),
            })
            item.comments = comments
            item.status = "todo"
            item.updated_at = datetime.now(timezone.utc)
            session.add(item)

            sprint = session.get(Sprint, item.sprint_id)
            if sprint:
                await _publish_glassroom(nats_client, sprint.id, {
                    "type": "status",
                    "agent": "System",
                    "role": "System",
                    "task": item.title,
                    "message": f"🔄 Retrying blocked task: {item.title}",
                })

            logger.info("Retrying blocked item=%s", item.id)

        session.commit()

    # Re-dispatch any items that just went back to todo
    await _dispatch_todo_items(nats_client)


def _call_llm_for_review(agent: Dict[str, Any], prompt: str) -> str:
    """Synchronous LLM call for code review (runs in thread)."""
    client = LLMProxyClient(os.getenv("LLM_PROXY_URL", "http://llm-proxy:5100"))
    messages = [{"role": "user", "content": prompt}]
    if agent.get("system_prompt"):
        messages.insert(0, {"role": "system", "content": agent["system_prompt"]})
    result = client.chat(
        model=agent.get("model", "deepseek-coder"),
        messages=messages,
        role=agent.get("role", "QA"),
    )

    if result.get("error"):
        raise RuntimeError(f"LLM error: {result['error']}")

    content = (
        result.get("choices", [{}])[0]
        .get("message", {})
        .get("content", "")
    )
    return content.strip()


async def _dispatch_todo_items(nats_client: NATSClient) -> None:
    """Find todo items and publish task.assign events for them."""
    with Session(engine) as session:
        todo_items = session.exec(
            select(BacklogItem).where(BacklogItem.status == "todo")
        ).all()
        for item in todo_items:
            comments = item.comments or []
            if isinstance(comments, str):
                try:
                    comments = json.loads(comments)
                except Exception:
                    comments = []

            if _has_manual_freeze_marker(comments):
                item.status = "blocked"
                item.updated_at = datetime.now(timezone.utc)
                session.add(item)
                logger.info("Keeping manually frozen todo item=%s blocked during dispatch", item.id)
                continue

            from uuid import uuid4
            await nats_client.publish("task.assign", {
                "agent": item.assignee or "Dev",
                "sprint_id": item.sprint_id,
                "interaction_id": str(uuid4()),
                "backlog_item_id": item.id,
            })
        session.commit()


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
