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
import hashlib
import json
import os
import re
import shlex
from datetime import UTC, datetime, timedelta
from typing import Any
from uuid import uuid4

from config import settings
from database import engine
from llm_proxy_client import LLMProxyClient
from logging_config import setup_logger
from models import (
    Agent,
    AssistantRuntimeState,
    BacklogItem,
    Company,
    OutboxEvent,
    ProjectDiscovery,
    Sprint,
)
from models.agent_run import AgentRun, Verification
from nats_client import NATSClient
from schemas import AgentRunCreate
from sqlmodel import Session, select, text

from llm.model_factory import default_model_for_role
from services import agent_run_service, console_capabilities
from services.orchestration_metrics import (
    ACTION_ARCHITECT_CONSULT,
    ACTION_EXECUTE_TASK,
    ACTION_QA_REVIEW,
    ARCHITECT_COMMENT_AUTHOR,
    KPI_ROLE_ARCHITECT,
    RESULT_NEEDS_WORK,
    VERDICT_REFUTED,
    VERDICT_UNVERIFIABLE,
    build_architect_consult_prompt,
    classify_review_result,
    format_architect_consult_comment,
    is_architect_consult_comment,
    record_agent_turn,
    should_consult_architect,
)
from services.sprint_planner import (
    build_self_improvement_objective,
    plan_self_improvement_sprint,
)
from services.verification import evaluate_workspace_writes, verify_agent_run

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
AUTO_SELF_IMPROVEMENT_ENABLED = settings.AUTO_SELF_IMPROVEMENT_ENABLED
CONSOLE_ASSISTANTS_ENABLED = settings.CONSOLE_ASSISTANTS_ENABLED
ARCHITECT_CONSULT_ENABLED = settings.ARCHITECT_CONSULT_ENABLED
CONSOLE_ASSISTANT_TIMEOUT = float(settings.CONSOLE_ASSISTANT_TIMEOUT_SECONDS)
COPILOT_CLI_COMMAND_TEMPLATE = settings.COPILOT_CLI_COMMAND_TEMPLATE
CLAUDE_CLI_COMMAND_TEMPLATE = settings.CLAUDE_CLI_COMMAND_TEMPLATE or ""
CLAUDE_CLI_ALLOWED_TOOLS = settings.CLAUDE_CLI_ALLOWED_TOOLS
CLAUDE_CLI_APPEND_SYSTEM_PROMPT = settings.CLAUDE_CLI_APPEND_SYSTEM_PROMPT
CONSOLE_ASSISTANT_STRATEGY = settings.CONSOLE_ASSISTANT_STRATEGY
CLAUDE_CLI_HIGH_VALUE_ROLES = {
    role.strip()
    for role in settings.CLAUDE_CLI_HIGH_VALUE_ROLES.split(",")
    if role.strip()
}
CLAUDE_CLI_DEFAULT_COOLDOWN_MINUTES = max(
    1, int(settings.CLAUDE_CLI_DEFAULT_COOLDOWN_MINUTES)
)
ASSISTANT_UNAVAILABLE_COOLDOWN_MINUTES = int(
    os.getenv("ASSISTANT_UNAVAILABLE_COOLDOWN_MINUTES", "30")
)
CONSOLE_ASSISTANT_EXCERPT_CHARS = int(
    os.getenv("CONSOLE_ASSISTANT_EXCERPT_CHARS", "240")
)
CONSOLE_ASSISTANT_ALLOWED_ROLES = {
    role.strip()
    for role in settings.CONSOLE_ASSISTANT_ALLOWED_ROLES.split(",")
    if role.strip()
}
CLAUDE_QUOTA_PATTERNS = (
    "usage limit",
    "rate limit",
    "quota",
    "too many requests",
)
ASSISTANT_UNAVAILABLE_PATTERNS = (
    "not found",
    "cannot find github copilot cli",
    "no such file or directory",
    "command not found",
)
CLAUDE_RESET_TIME_PATTERNS = (
    re.compile(
        r"(?:available|retry|resets?)\s+(?:at|after)\s+(?P<time>\d{1,2}:\d{2}\s?(?:am|pm)?)",
        re.IGNORECASE,
    ),
    re.compile(r"(?P<time>\d{1,2}:\d{2}\s?(?:am|pm))", re.IGNORECASE),
)
LAST_AUTONOMOUS_QUEUE_AT: dict[int, datetime] = {}


def _normalize_utc(value: datetime | None) -> datetime | None:
    """Normalize datetimes loaded from the DB to timezone-aware UTC values."""
    if not value:
        return None
    if value.tzinfo is None:
        return value.replace(tzinfo=UTC)
    return value.astimezone(UTC)


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


ALLOWED_ARTIFACT_EXTENSIONS = {
    ".md",
    ".txt",
    ".json",
    ".yaml",
    ".yml",
    ".toml",
    ".py",
    ".js",
    ".ts",
    ".tsx",
    ".jsx",
    ".css",
    ".scss",
    ".html",
    ".sql",
    ".sh",
    ".go",
    ".java",
    ".kt",
    ".rs",
    ".rb",
    ".php",
    ".cs",
    ".vue",
    ".svelte",
    ".xml",
    ".ini",
    ".cfg",
    ".graphql",
}

# Fenced block whose info string carries ``path=<relative path>``, e.g.
# ```python path=src/auth/login.py
_MANIFEST_BLOCK = re.compile(
    r"^```(?P<info>[^\n`]*\bpath=[^\n`]*)\n(?P<body>.*?)^```[ \t]*$",
    flags=re.DOTALL | re.MULTILINE,
)
_MANIFEST_PATH = re.compile(r"""\bpath=(?:"([^"]+)"|'([^']+)'|(\S+))""")

FILE_MANIFEST_INSTRUCTIONS = (
    "\n\n## Output format for files\n"
    "When your deliverable creates or changes files, write each file in full as its own "
    "fenced code block whose opening line carries the path relative to the project root, "
    "for example:\n\n"
    "```python path=src/example/module.py\n<entire file content>\n```\n\n"
    "Use one block per file, give the complete content (not a diff or an excerpt), and "
    "keep explanations outside the blocks. Files are written to the project exactly as "
    "given."
)


# ---------------------------------------------------------------------------
# Memory augmentation helpers — fail silently, never block task execution
# ---------------------------------------------------------------------------


def _fetch_memory_context(item: dict[str, Any], agent: dict[str, Any]) -> str:
    """Query engram RAG for past decisions relevant to this task and agent role."""
    task_text = f"{item.get('title', '')} {item.get('description', '')}".strip()
    role = agent.get("role") or None
    try:
        from memory.engram_processor import get_engram_processor

        processor = get_engram_processor()
        return processor.query_relevant_engrams(task_text, limit=3, role_filter=role)
    except Exception as exc:  # noqa: BLE001 - Optional memory context must not block task execution.
        logger.debug("Memory context fetch failed for item=%s: %s", item.get("id"), exc)
        return ""


def _load_sprint_completed_tasks(
    sprint_id: int, exclude_item_id: int, limit: int = 5
) -> str:
    """Return a brief summary of done tasks in the same sprint for cross-task context."""
    try:
        with Session(engine) as session:
            done_items = session.exec(
                select(BacklogItem).where(
                    BacklogItem.sprint_id == sprint_id,
                    BacklogItem.id != exclude_item_id,
                    BacklogItem.status == "done",
                )
            ).all()
            if not done_items:
                return ""
            lines = [
                "Completed tasks in this sprint (build on this work, avoid duplication):"
            ]
            for it in done_items[:limit]:
                comments = it.comments or []
                if isinstance(comments, str):
                    try:
                        comments = json.loads(comments)
                    except Exception:  # noqa: BLE001 - Malformed legacy comments fall back to an empty list.
                        comments = []
                deliverable_excerpt = _extract_latest_reviewable_comment(
                    comments, assignee_role=it.assignee
                )
                excerpt = (
                    deliverable_excerpt[:280].replace("\n", " ")
                    if deliverable_excerpt
                    else "(no deliverable)"
                )
                lines.append(f"- [{it.assignee or 'Agent'}] {it.title}: {excerpt}")
            return "\n".join(lines)
    except Exception as exc:  # noqa: BLE001 - Completed-task context is best-effort.
        logger.debug(
            "Failed to load sprint completed tasks for sprint=%s: %s", sprint_id, exc
        )
        return ""


# ---------------------------------------------------------------------------
# Sprint retrospective — written to memory after sprint completion
# ---------------------------------------------------------------------------


def _build_sprint_retrospective(sprint_id: int) -> dict[str, Any]:
    """Compute sprint health metrics for retrospective engram and next-sprint planning."""
    try:
        with Session(engine) as session:
            items = session.exec(
                select(BacklogItem).where(BacklogItem.sprint_id == sprint_id)
            ).all()
            if not items:
                return {}

            sprint = session.get(Sprint, sprint_id)
            total = len(items)
            done = sum(1 for i in items if i.status == "done")
            blocked = sum(1 for i in items if i.status == "blocked")
            rework_count = 0
            qa_fail_count = 0
            blocked_reasons: list[str] = []

            for item in items:
                comments = item.comments or []
                if isinstance(comments, str):
                    try:
                        comments = json.loads(comments)
                    except Exception:  # noqa: BLE001 - Malformed legacy comments fall back to an empty list.
                        comments = []
                for comment in comments:
                    text = str(comment.get("text", ""))
                    if "NEEDS WORK:" in text.upper():
                        rework_count += 1
                    if (
                        "qa quality gate failed" in text.lower()
                        or "qa review failed" in text.lower()
                    ):
                        qa_fail_count += 1
                if item.status == "blocked":
                    reason = _extract_latest_reviewable_comment(
                        comments, assignee_role=item.assignee
                    )
                    if reason:
                        blocked_reasons.append(f"{item.title[:60]}: {reason[:120]}")

            return {
                "company_id": sprint.company_id if sprint else None,
                "sprint_id": sprint_id,
                "total_tasks": total,
                "done_tasks": done,
                "blocked_tasks": blocked,
                "completion_rate": round(done / total * 100) if total else 0,
                "rework_cycles": rework_count,
                "qa_failures": qa_fail_count,
                "blocked_reasons": blocked_reasons[:5],
            }
    except Exception as exc:  # noqa: BLE001 - Retrospective context is best-effort.
        logger.warning(
            "Sprint retrospective build failed for sprint=%s: %s", sprint_id, exc
        )
        return {}


def _format_retrospective_summary(metrics: dict[str, Any]) -> str:
    lines = [
        f"Sprint {metrics.get('sprint_id')} retrospective:",
        f"- Completed {metrics.get('done_tasks', 0)}/{metrics.get('total_tasks', 0)} tasks ({metrics.get('completion_rate', 0)}%)",
        f"- Blocked: {metrics.get('blocked_tasks', 0)} tasks",
        f"- QA rework cycles: {metrics.get('rework_cycles', 0)}",
        f"- QA failures: {metrics.get('qa_failures', 0)}",
    ]
    for reason in metrics.get("blocked_reasons") or []:
        lines.append(f"  • {reason}")
    return "\n".join(lines)


# ---------------------------------------------------------------------------
# LLM interaction — runs in a thread to avoid blocking the event loop
# ---------------------------------------------------------------------------


class LLMCallError(RuntimeError):
    """An LLM call that produced no usable answer; its text goes on the board."""


def _resolve_agent_model(agent: dict[str, Any], default_role: str = "Dev") -> str:
    """The agent's configured model, or its role's default from model_factory."""
    configured = (agent.get("model") or "").strip()
    return configured or default_model_for_role(agent.get("role") or default_role)


def _llm_content(result: dict[str, Any]) -> str:
    choices = result.get("choices") or [{}]
    message = (choices[0] or {}).get("message") or {}
    content = message.get("content") or ""
    return (content if isinstance(content, str) else str(content)).strip()


def _llm_failure(result: dict[str, Any], model: str) -> str | None:
    """
    Describe why *result* carries no usable answer, or return ``None``.

    The text reads ``LLM call failed: <provider> <status> <message>``.  The
    proxy reports provider errors as ``{"provider", "status", "message"}``;
    errors of the proxy itself (unknown model, quota) name ``llm-proxy`` and
    the HTTP status.  A 2xx answer with empty content is a failure too, so it
    never reaches the word-count gate as an empty deliverable.
    """
    if result.get("error"):
        detail = result.get("detail")
        detail = detail if isinstance(detail, dict) else {}
        provider = detail.get("provider") or "llm-proxy"
        status = detail.get("status") or result.get("status_code") or "unreachable"
        message = detail.get("message") or detail.get("error") or result["error"]
        return f"LLM call failed: {provider} {status} {message}"
    if not _llm_content(result):
        provider = result.get("provider") or "llm-proxy"
        served = result.get("served_model") or model
        return f"LLM call failed: {provider} 200 empty response from {served}"
    return None


def _execute_task(
    item: dict[str, Any],
    agent: dict[str, Any],
    sprint_name: str,
    external_advice: str | None = None,
    memory_context: str | None = None,
    completed_tasks_context: str | None = None,
    architect_advice: str | None = None,
    company_id: int | None = None,
    sprint_id: int | None = None,
) -> str:
    """
    Call the LLM to produce a deliverable for the given backlog item.

    Runs synchronously (called via ``asyncio.to_thread``).
    Returns the LLM response text.  An empty agent model resolves to the
    role's default from ``llm.model_factory``.  The model the proxy reports
    as having answered is stored as ``agent["served_model"]`` (``None`` when
    not reported) so the AgentRun records it.  Raises ``LLMCallError`` when
    the call fails or returns no content.

    When ``company_id`` is provided the turn is also written to ``audit_log``
    (role, company, token cost) so ``GET /v1/analytics/orchestration_health``
    can compute the Developer token KPI.
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
        f"{FILE_MANIFEST_INSTRUCTIONS}"
    )

    rework_feedback = _pending_rework_feedback(item.get("comments"))
    if rework_feedback:
        user_prompt += (
            "\n\n## Review feedback to address\n"
            "Your previous deliverable for this task was sent back. Fix every point "
            "below and deliver the complete result again.\n\n"
            + "\n".join(f"- {text[:1500]}" for text in rework_feedback)
        )

    if memory_context:
        user_prompt += (
            "\n\n## Memory: Relevant Past Decisions\n"
            "These are decisions and deliverables from previous sprints in this factory. "
            "Use them to maintain consistency and avoid repeating past mistakes.\n\n"
            f"{memory_context[:3000]}"
        )

    if completed_tasks_context:
        user_prompt += (
            "\n\n## Sprint Context: Already Completed Tasks\n"
            f"{completed_tasks_context[:2000]}"
        )

    if architect_advice:
        user_prompt += (
            "\n\n## Architect Guidance\n"
            "The team Architect reviewed this task before implementation. "
            "Follow this brief unless it conflicts with the task requirements.\n\n"
            f"{architect_advice[:3000]}"
        )

    if external_advice:
        user_prompt += (
            "\n\n## External Console Assistant Notes\n"
            "The following advice was gathered from configured terminal assistants such as GitHub Copilot CLI or Claude CLI. "
            "Treat it as optional supporting context, not as a replacement for reasoning about the current repository. "
            "Prefer preserving working behavior over blindly following suggestions.\n\n"
            f"{external_advice[:4000]}"
        )

    model = _resolve_agent_model(agent)
    role = agent.get("role", "Dev")

    logger.info(
        "Agent %s (%s) working on: %s [model=%s]",
        agent.get("name") or role,
        role,
        item["title"],
        model,
    )

    result = client.chat(
        messages=[
            {"role": "system", "content": system_prompt},
            {"role": "user", "content": user_prompt},
        ],
        model=model,
        role=role,
    )

    failure = _llm_failure(result, model)
    if failure:
        raise LLMCallError(failure)

    content = _llm_content(result)
    agent["served_model"] = result.get("served_model") or None

    if company_id is not None:
        record_agent_turn(
            company_id=company_id,
            sprint_id=sprint_id,
            backlog_item_id=item.get("id"),
            agent={**agent, "model": agent["served_model"] or model},
            action=ACTION_EXECUTE_TASK,
            llm_result=result,
            result_preview=content,
        )
    return content


def _consult_architect(
    item: dict[str, Any],
    developer: dict[str, Any],
    sprint: dict[str, Any],
    prior_feedback: list[str] | None = None,
) -> str:
    """
    Ask the company's Architect agent for a short technical brief on *item*.

    Runs synchronously (called via ``asyncio.to_thread``).  Returns the advice
    text, or ``""`` when no Architect agent exists or the call fails; callers
    treat an empty brief as "no consultation happened".  Successful calls are
    written to ``audit_log`` with ``role=Architect``.
    """
    with Session(engine) as session:
        architect_row = session.exec(
            select(Agent).where(
                Agent.company_id == sprint["company_id"],
                Agent.role == KPI_ROLE_ARCHITECT,
            )
        ).first()

    if not architect_row:
        logger.info(
            "No Architect agent for company=%s; skipping consultation for item=%s",
            sprint["company_id"],
            item.get("id"),
        )
        return ""

    architect = {
        "role": architect_row.role or KPI_ROLE_ARCHITECT,
        "name": architect_row.name or KPI_ROLE_ARCHITECT,
        "model": _resolve_agent_model(
            {"model": architect_row.model, "role": KPI_ROLE_ARCHITECT}
        ),
        "seniority": architect_row.seniority,
        "specialty": architect_row.specialty,
        "system_prompt": architect_row.system_prompt,
    }
    system_prompt = architect["system_prompt"] or (
        f"You are a {architect.get('seniority') or 'Senior'} Software Architect "
        f"specializing in {architect.get('specialty') or 'system design'}. "
        "You advise developers with concise, decisive technical briefs."
    )
    prompt = build_architect_consult_prompt(
        item, developer, sprint.get("name", ""), prior_feedback or ()
    )

    client = LLMProxyClient(
        base_url=os.getenv("LLM_PROXY_URL", "http://llm-proxy:5100")
    )
    client.timeout = 90
    try:
        result = client.chat(
            messages=[
                {"role": "system", "content": system_prompt},
                {"role": "user", "content": prompt},
            ],
            model=architect["model"],
            role=architect["role"],
        )
    except Exception as exc:  # noqa: BLE001 - Network/proxy failure must not block the developer.
        logger.warning(
            "Architect consultation failed for item=%s: %s", item.get("id"), exc
        )
        return ""

    failure = _llm_failure(result, architect["model"])
    if failure:
        logger.warning(
            "Architect consultation returned no advice for item=%s: %s",
            item.get("id"),
            failure,
        )
        return ""

    advice = _llm_content(result)

    record_agent_turn(
        company_id=sprint["company_id"],
        sprint_id=sprint.get("id"),
        backlog_item_id=item.get("id"),
        agent={**architect, "model": result.get("served_model") or architect["model"]},
        action=ACTION_ARCHITECT_CONSULT,
        llm_result=result,
        result_preview=advice,
    )
    return advice


def _validate_minimum_deliverable(
    item: dict[str, Any], deliverable: str
) -> tuple[bool, str]:
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


def _sanitize_deliverable_text(deliverable: str) -> str:
    """Repair small formatting truncations that should not block autonomous progress."""
    text = (deliverable or "").strip()
    if not text:
        return ""

    if text.count("```") % 2 != 0:
        text = f"{text}\n```"

    return text


def _resolve_workspace_root(company_id: int) -> str | None:
    """
    Return where this company's artifacts should be written.

    The company's discovered project wins; ``AGENT_WORKSPACE_ROOT`` is only a
    fallback for companies without one (e.g. self-improvement sprints that
    work on this repository).
    """
    with Session(engine) as session:
        discovery = session.exec(
            select(ProjectDiscovery)
            .where(ProjectDiscovery.company_id == company_id)
            .order_by(text("created_at DESC"))
        ).first()
        if discovery and discovery.project_path:
            return discovery.project_path

    return os.getenv("AGENT_WORKSPACE_ROOT") or None


def _sanitize_relative_path(path: str) -> str | None:
    """Normalize a relative path and reject unsafe values."""
    p = (path or "").strip().replace("\\", "/")
    if not p or p.startswith("/") or ".." in p.split("/"):
        return None
    return p


def _normalize_artifact_content(path: str, deliverable: str) -> str:
    """Trim wrapper text so persisted files contain only final artifact content."""
    text_content = (deliverable or "").strip().replace("\r\n", "\n")

    fenced = re.search(
        r"```(?:[a-zA-Z0-9_+-]+)?\s*(.*?)```", text_content, flags=re.DOTALL
    )
    if fenced and len(fenced.group(1).strip()) > 40:
        text_content = fenced.group(1).strip()

    if path.lower().endswith(".md"):
        text_content = re.sub(r"(?m)^\*\*\*\s*$", "", text_content).strip()
        heading = re.search(r"(?m)^#\s+", text_content)
        if heading and heading.start() > 0:
            text_content = text_content[heading.start() :].strip()

    return text_content


def _extract_artifact_targets(
    item: dict[str, Any], deliverable: str
) -> list[dict[str, str]]:
    """Infer concrete file targets from task metadata and generated content."""
    title_desc = f"{item.get('title', '')} {item.get('description', '')}"
    candidates = re.findall(r"([A-Za-z0-9_./-]+\.[A-Za-z0-9]+)", title_desc)

    artifacts: list[dict[str, str]] = []
    for c in candidates:
        rel = _sanitize_relative_path(c)
        if not rel:
            continue
        _, ext = os.path.splitext(rel)
        if ext.lower() not in ALLOWED_ARTIFACT_EXTENSIONS:
            continue
        artifacts.append(
            {"path": rel, "content": _normalize_artifact_content(rel, deliverable)}
        )

    # Remove duplicates while preserving order.
    dedup: list[dict[str, str]] = []
    seen: set[str] = set()
    for a in artifacts:
        if a["path"] in seen:
            continue
        seen.add(a["path"])
        dedup.append(a)

    return dedup


def parse_file_manifest(deliverable: str) -> list[dict[str, str]]:
    """
    Extract the files an agent declared with ```` ```<lang> path=<file> ```` blocks.

    Each block becomes one ``{"path", "content"}`` entry with its own content.
    Unsafe paths (absolute, ``..``) and disallowed extensions are dropped.  When
    the same path appears twice the last block wins, keeping the position of
    the first.  Returns ``[]`` when the answer declares no files.
    """
    files: dict[str, str] = {}
    for block in _MANIFEST_BLOCK.finditer(deliverable or ""):
        match = _MANIFEST_PATH.search(block.group("info"))
        if not match:
            continue
        rel = _sanitize_relative_path(next(g for g in match.groups() if g))
        if not rel:
            continue
        if os.path.splitext(rel)[1].lower() not in ALLOWED_ARTIFACT_EXTENSIONS:
            continue
        files[rel] = block.group("body")
    return [{"path": path, "content": content} for path, content in files.items()]


def _build_review_payload(
    deliverable: str, artifact_targets: list[dict[str, str]]
) -> str:
    """Prefer normalized artifact content for QA review when files were materialized."""
    if not artifact_targets:
        return (deliverable or "").strip()

    sections: list[str] = []
    for artifact in artifact_targets:
        path = artifact.get("path", "artifact")
        content = (artifact.get("content") or "").strip()
        sections.append(f"# Artifact: {path}\n\n{content}")

    return "\n\n".join(section for section in sections if section.strip())


def _extract_latest_reviewable_comment(
    comments: Any, assignee_role: str | None = None
) -> str:
    """Return the latest non-system, non-QA deliverable comment for review."""
    if isinstance(comments, str):
        try:
            comments = json.loads(comments)
        except Exception:  # noqa: BLE001 - Malformed legacy comments fall back to an empty list.
            comments = []

    if not isinstance(comments, list):
        return ""

    assignee_normalized = (assignee_role or "").strip().lower()

    for comment in reversed(comments):
        author = str(comment.get("author", "")).strip().lower()
        text = str(comment.get("text", "")).strip()
        if not text:
            continue
        if (
            text.lower().startswith("freeze after")
            or "sleep-induced churn" in text.lower()
        ):
            continue
        if author == "system":
            continue
        if text.upper().startswith("NEEDS WORK:"):
            # A rework request after the latest deliverable makes it stale:
            # the task must run again before anything is reviewed.
            return ""
        if author == "qa" and assignee_normalized != "qa":
            continue
        if text.upper().startswith("APPROVED:"):
            continue
        if is_architect_consult_comment(comment):
            continue
        return text

    return ""


def _pending_rework_feedback(comments: Any) -> list[str]:
    """The ``NEEDS WORK:`` requests made since the task's latest deliverable."""
    if not isinstance(comments, list):
        return []
    feedback: list[str] = []
    for comment in reversed(comments):
        text = str(comment.get("text", "")).strip()
        if text.upper().startswith("NEEDS WORK:"):
            feedback.append(f"{comment.get('author') or 'Reviewer'}: {text}")
            continue
        author = str(comment.get("author", "")).strip().lower()
        if (
            text
            and author != "system"
            and not text.upper().startswith("APPROVED:")
            and not is_architect_consult_comment(comment)
        ):
            break  # reached the deliverable those requests were about
    return list(reversed(feedback))


def _is_explicit_block_reason(text: str) -> bool:
    """Return True when a comment clearly indicates the task must remain blocked."""
    normalized = (text or "").strip().lower()
    return any(
        token in normalized
        for token in (
            "manual triage",
            "pre-review quality gate failed",
            "qa quality gate failed",
            "qa review failed",
            "agent error:",
            "missing deliverable",
            "freeze after",
            "sleep-induced churn",
        )
    )


def _can_resume_blocked_item_from_deliverable(
    comments: Any, assignee_role: str | None = None
) -> bool:
    """Detect blocked items that already have a deliverable and no explicit block reason."""
    if isinstance(comments, str):
        try:
            comments = json.loads(comments)
        except Exception:  # noqa: BLE001 - Malformed legacy comments fall back to an empty list.
            comments = []

    if not isinstance(comments, list) or not comments:
        return False

    if _has_manual_freeze_marker(comments):
        return False

    if any(
        _is_explicit_block_reason(str(comment.get("text", ""))) for comment in comments
    ):
        return False

    return bool(
        _extract_latest_reviewable_comment(comments, assignee_role=assignee_role)
    )


def _has_manual_freeze_marker(comments: Any) -> bool:
    """Detect operator comments that intentionally freeze a task out of automation."""
    if isinstance(comments, str):
        try:
            comments = json.loads(comments)
        except Exception:  # noqa: BLE001 - Malformed legacy comments fall back to an empty list.
            comments = []

    if not isinstance(comments, list):
        return False

    return any(
        "sleep-induced churn" in str(comment.get("text", "")).lower()
        or str(comment.get("text", "")).strip().lower().startswith("freeze after")
        for comment in comments
    )


def _load_item_runtime_state(item_id: int) -> dict[str, Any] | None:
    """Load the latest mutable state so stale in-flight work can be discarded."""
    with Session(engine) as session:
        item = session.get(BacklogItem, item_id)
        if not item:
            return None

        comments = item.comments or []
        if isinstance(comments, str):
            try:
                comments = json.loads(comments)
            except Exception:  # noqa: BLE001 - Malformed legacy comments fall back to an empty list.
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
            except Exception:  # noqa: BLE001 - Malformed legacy comments fall back to an empty list.
                comments = []

        if not _has_manual_freeze_marker(comments):
            return False

        changed = item.status != "blocked"
        item.status = "blocked"
        item.updated_at = datetime.now(UTC)
        session.add(item)
        session.commit()
        return changed


def _recover_inflight_items_for_restart() -> list[dict[str, Any]]:
    """Return unfinished in-progress work to todo so the worker can resume after restart."""
    recovered: list[dict[str, Any]] = []
    recovery_time = datetime.now(UTC)

    with Session(engine) as session:
        inflight_items = session.exec(
            select(BacklogItem).where(BacklogItem.status == "in_progress")
        ).all()

        for item in inflight_items:
            comments = item.comments or []
            if isinstance(comments, str):
                try:
                    comments = json.loads(comments)
                except Exception:  # noqa: BLE001 - Malformed legacy comments fall back to an empty list.
                    comments = []

            if _has_manual_freeze_marker(comments):
                item.status = "blocked"
                item.updated_at = recovery_time
                session.add(item)
                continue

            comments.append(
                {
                    "author": "System",
                    "text": "Worker recovery after backend restart — unfinished in-progress task returned to todo for redispatch.",
                    "created_at": recovery_time.isoformat(),
                }
            )
            item.comments = comments
            item.status = "todo"
            item.updated_at = recovery_time
            session.add(item)
            recovered.append(
                {
                    "id": item.id,
                    "sprint_id": item.sprint_id,
                    "title": item.title,
                    "assignee": item.assignee or "Dev",
                }
            )

        session.commit()

    return recovered


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


def _complete_sprint_and_plan_follow_up(
    completed_sprint_id: int,
    retro_metrics: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """Mark a sprint completed when all items are done and optionally open an autonomous follow-up sprint."""
    with Session(engine) as session:
        sprint = session.get(Sprint, completed_sprint_id)
        if not sprint or sprint.status != "active":
            return {"completed": False, "auto_opened": False, "reason": "not_active"}

        items = session.exec(
            select(BacklogItem).where(BacklogItem.sprint_id == completed_sprint_id)
        ).all()
        if not items or any(item.status != "done" for item in items):
            return {"completed": False, "auto_opened": False, "reason": "not_all_done"}

        sprint.status = "completed"
        session.add(sprint)

        result = {
            "completed": True,
            "auto_opened": False,
            "reason": "completed_no_follow_up",
            "sprint_id": sprint.id,
            "sprint_name": sprint.sprint_name,
            "company_id": sprint.company_id,
        }

        company = session.get(Company, sprint.company_id)
        if not company:
            result["reason"] = "company_missing"
            session.commit()
            return result

        if company.autonomy_level < 3:
            result["reason"] = "autonomy_below_threshold"
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
                Sprint.status.in_(("active", "needs_split")),
            )
        ).first()
        if active_sprint:
            result["reason"] = "active_sprint_already_exists"
            result["blocking_sprint_id"] = active_sprint.id
            session.commit()
            return result

        discovery = session.exec(
            select(ProjectDiscovery)
            .where(ProjectDiscovery.company_id == company.id)
            .order_by(text("created_at DESC"))
        ).first()
        detected_stack = _coerce_json_dict(
            discovery.detected_stack if discovery else None
        )
        project_path = discovery.project_path if discovery else None

        agents = session.exec(select(Agent).where(Agent.company_id == company.id)).all()
        agent_dicts = [
            {
                "role": agent.role,
                "name": agent.name,
                "model": agent.model,
                "seniority": agent.seniority,
                "specialty": agent.specialty,
            }
            for agent in agents
        ]

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
        tasks = plan.get("tasks") or []
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
        )
        session.add(next_sprint)
        session.flush()

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

        session.add(
            OutboxEvent(
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
        )

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


def _build_console_assistant_prompt(
    item: dict[str, Any], agent: dict[str, Any], sprint_name: str
) -> str:
    """Build a concise prompt for external console assistants."""
    return (
        f"Repository task for {agent.get('role', 'Developer')} in sprint '{sprint_name}'.\n"
        f"Task: {item.get('title', '')}\n"
        f"Description: {item.get('description', '')}\n"
        f"Priority: {item.get('priority', 'medium')}\n\n"
        "Provide concise actionable advice for implementing this task in the current repository. "
        "Prefer incremental changes, mention relevant existing code or GitHub/MCP-backed ideas when useful, and avoid suggesting destructive rewrites."
    )


def _assistant_purpose(item: dict[str, Any], agent: dict[str, Any], label: str) -> str:
    """Describe why an external assistant is being consulted for the current task."""
    role = (agent.get("role") or "Developer").strip()
    priority = (item.get("priority") or "medium").strip().lower()
    task_text = f"{item.get('title', '')}\n{item.get('description', '')}".lower()

    if label == "Claude CLI":
        if role in {"Architect", "PO", "ScrumMaster"}:
            return "planning and architecture advice"
        if role == "QA" or any(
            token in task_text for token in ("validate", "review", "security", "risk")
        ):
            return "review and risk analysis advice"
        return "high-value implementation guidance"

    if priority in {"high", "critical"}:
        return "high-priority implementation guidance"
    return "incremental implementation guidance"


def _claude_is_high_value(item: dict[str, Any], agent: dict[str, Any]) -> bool:
    """Use Claude selectively for strategic, review-heavy, or high-priority work."""
    role = (agent.get("role") or "Developer").strip()
    priority = (item.get("priority") or "medium").strip().lower()
    task_text = f"{item.get('title', '')}\n{item.get('description', '')}".lower()
    strategic_keywords = (
        "architecture",
        "plan",
        "planning",
        "review",
        "security",
        "risk",
        "refactor",
        "scrumban",
        "autonomy",
        "self-improvement",
        "design",
    )
    return (
        role in CLAUDE_CLI_HIGH_VALUE_ROLES
        or priority in {"high", "critical"}
        or any(keyword in task_text for keyword in strategic_keywords)
    )


def _get_assistant_runtime_state(
    company_id: int, assistant_key: str
) -> AssistantRuntimeState | None:
    """Load the persisted runtime state for a company/assistant pair."""
    with Session(engine) as session:
        return session.exec(
            select(AssistantRuntimeState).where(
                AssistantRuntimeState.company_id == company_id,
                AssistantRuntimeState.assistant_key == assistant_key,
            )
        ).first()


def _set_assistant_runtime_state(
    company_id: int,
    assistant_key: str,
    *,
    cooldown_until: datetime | None,
    last_error: str | None = None,
) -> datetime | None:
    """Persist runtime state updates so assistant cooldown survives backend restarts."""
    normalized_cooldown = _normalize_utc(cooldown_until)
    with Session(engine) as session:
        state = session.exec(
            select(AssistantRuntimeState).where(
                AssistantRuntimeState.company_id == company_id,
                AssistantRuntimeState.assistant_key == assistant_key,
            )
        ).first()
        if not state:
            state = AssistantRuntimeState(
                company_id=company_id, assistant_key=assistant_key
            )
        state.cooldown_until = normalized_cooldown
        state.last_error = (last_error or "")[:1000] or None
        state.updated_at = datetime.now(UTC)
        session.add(state)
        session.commit()
    return normalized_cooldown


def _get_claude_cooldown_until(company_id: int) -> datetime | None:
    """Return the persisted Claude cooldown deadline when quota was recently hit."""
    now = datetime.now(UTC)
    state = _get_assistant_runtime_state(company_id, "claude")
    cooldown_until = _normalize_utc(state.cooldown_until) if state else None
    if cooldown_until and cooldown_until <= now:
        _set_assistant_runtime_state(
            company_id, "claude", cooldown_until=None, last_error=None
        )
        return None
    return cooldown_until


def _get_assistant_cooldown_until(
    company_id: int, assistant_key: str
) -> datetime | None:
    """Return the persisted cooldown deadline for any assistant key."""
    now = datetime.now(UTC)
    state = _get_assistant_runtime_state(company_id, assistant_key)
    cooldown_until = _normalize_utc(state.cooldown_until) if state else None
    if cooldown_until and cooldown_until <= now:
        _set_assistant_runtime_state(
            company_id, assistant_key, cooldown_until=None, last_error=None
        )
        return None
    return cooldown_until


def _parse_claude_reset_time(text: str) -> datetime | None:
    """Best-effort parse for reset times embedded in Claude CLI quota messages."""
    now = datetime.now().astimezone()
    for pattern in CLAUDE_RESET_TIME_PATTERNS:
        match = pattern.search(text or "")
        if not match:
            continue
        raw_time = (match.group("time") or "").strip().upper().replace(" ", "")
        formats = ["%I:%M%p", "%H:%M"]
        for fmt in formats:
            try:
                parsed = datetime.strptime(raw_time, fmt)
            except ValueError:
                continue
            candidate = now.replace(
                hour=parsed.hour, minute=parsed.minute, second=0, microsecond=0
            )
            if candidate <= now:
                candidate += timedelta(days=1)
            return candidate.astimezone(UTC)
    return None


def _maybe_activate_claude_cooldown(company_id: int, output: str) -> datetime | None:
    """Detect Claude quota exhaustion and persist a cooldown window for the company."""
    lowered = (output or "").lower()
    if not any(token in lowered for token in CLAUDE_QUOTA_PATTERNS):
        return None
    cooldown_until = _parse_claude_reset_time(output) or (
        datetime.now(UTC) + timedelta(minutes=CLAUDE_CLI_DEFAULT_COOLDOWN_MINUTES)
    )
    return _set_assistant_runtime_state(
        company_id,
        "claude",
        cooldown_until=cooldown_until,
        last_error=output,
    )


def _assistant_key_for_label(label: str) -> str:
    if label == "Claude CLI":
        return "claude"
    if label == "GitHub Copilot CLI":
        return "copilot"
    return re.sub(r"[^a-z0-9]+", "_", label.lower()).strip("_") or "assistant"


def _maybe_activate_assistant_unavailable_cooldown(
    company_id: int,
    label: str,
    output: str,
) -> datetime | None:
    """Persist a short cooldown when an assistant binary is missing or unusable."""
    lowered = (output or "").lower()
    if not any(token in lowered for token in ASSISTANT_UNAVAILABLE_PATTERNS):
        return None
    return _set_assistant_runtime_state(
        company_id,
        _assistant_key_for_label(label),
        cooldown_until=datetime.now(UTC)
        + timedelta(minutes=max(1, ASSISTANT_UNAVAILABLE_COOLDOWN_MINUTES)),
        last_error=output,
    )


async def _publish_console_assistant_event(
    nats_client: NATSClient,
    sprint_id: int,
    item: dict[str, Any],
    label: str,
    purpose: str,
    message: str,
    *,
    decision: str,
    detail: str | None = None,
) -> None:
    """Publish a GlassRoom status event explaining assistant usage decisions."""
    await _publish_glassroom(
        nats_client,
        sprint_id,
        {
            "type": "status",
            "semantic_type": "assistant.telemetry",
            "source": "task_worker",
            "agent": "System",
            "role": "System",
            "task_id": item.get("id"),
            "task": item.get("title", "Task"),
            "message": message,
            "assistant_label": label,
            "assistant_purpose": purpose,
            "assistant_decision": decision,
            "assistant_detail": detail or "",
        },
    )


def _select_console_assistants(
    item: dict[str, Any], agent: dict[str, Any]
) -> list[tuple[str, str, str]]:
    """Choose assistants efficiently so Copilot is the default and Claude stays budget-aware."""
    assistants: list[tuple[str, str, str]] = []
    if CONSOLE_ASSISTANT_STRATEGY == "copilot_first" and COPILOT_CLI_COMMAND_TEMPLATE:
        assistants.append(
            (
                "GitHub Copilot CLI",
                COPILOT_CLI_COMMAND_TEMPLATE,
                _assistant_purpose(item, agent, "GitHub Copilot CLI"),
            )
        )

    if CLAUDE_CLI_COMMAND_TEMPLATE and _claude_is_high_value(item, agent):
        assistants.append(
            (
                "Claude CLI",
                CLAUDE_CLI_COMMAND_TEMPLATE,
                _assistant_purpose(item, agent, "Claude CLI"),
            )
        )

    if not assistants and COPILOT_CLI_COMMAND_TEMPLATE:
        assistants.append(
            (
                "GitHub Copilot CLI",
                COPILOT_CLI_COMMAND_TEMPLATE,
                _assistant_purpose(item, agent, "GitHub Copilot CLI"),
            )
        )

    return assistants


def _render_console_command(
    template: str, prompt: str, workspace_root: str
) -> str | None:
    """Render a shell command template with a safely quoted prompt."""
    normalized = (template or "").strip()
    if not normalized:
        return None
    quoted_values = {
        "prompt": shlex.quote(prompt),
        "workspace_root": shlex.quote(workspace_root),
        "allowed_tools": shlex.quote(CLAUDE_CLI_ALLOWED_TOOLS),
        "system_prompt": shlex.quote(CLAUDE_CLI_APPEND_SYSTEM_PROMPT),
    }
    if any(
        token in normalized
        for token in (
            "{prompt}",
            "{workspace_root}",
            "{allowed_tools}",
            "{system_prompt}",
        )
    ):
        return normalized.format(**quoted_values)
    return f"{normalized} {quoted_values['prompt']}"


def _replace_command_binary(command: str, original: str, replacement: str) -> str:
    """Swap the first command token while preserving the rest of the shell command."""
    if command == original:
        return replacement
    prefix = f"{original} "
    if command.startswith(prefix):
        return f"{replacement}{command[len(original) :]}"
    return command


def _wrap_console_command_with_fallback(label: str, command: str) -> str:
    """Add host-specific binary fallback paths for known assistants."""
    if label == "GitHub Copilot CLI":
        fallback = '"$HOME/Library/Application Support/Code/User/globalStorage/github.copilot-chat/copilotCli/copilot"'
        fallback_command = _replace_command_binary(command, "copilot", fallback)
        return (
            f"if [ -x {fallback} ]; then {fallback_command}; "
            f"elif command -v copilot >/dev/null 2>&1; then {command}; "
            f"else {command}; fi"
        )

    if label == "Claude CLI":
        fallback = '"$HOME/.local/bin/claude"'
        fallback_command = _replace_command_binary(command, "claude", fallback)
        return (
            f"if command -v claude >/dev/null 2>&1; then {command}; "
            f"elif [ -x {fallback} ]; then {fallback_command}; "
            f"else {command}; fi"
        )

    return command


async def _collect_console_assistant_advice(
    nats_client: NATSClient,
    item: dict[str, Any],
    agent: dict[str, Any],
    sprint: dict[str, Any],
) -> dict[str, Any]:
    """Query configured terminal assistants via the VS Code daemon and return their advice."""
    if not CONSOLE_ASSISTANTS_ENABLED:
        return {"combined": "", "sources": []}

    role = agent.get("role", "Developer")
    if role not in CONSOLE_ASSISTANT_ALLOWED_ROLES:
        return {"combined": "", "sources": []}

    # Only consult assistants the daemon reported it can run; the capability
    # probe runs once when workers start (see lifecycle).
    available = set(console_capabilities.available_console_assistants())
    if not available:
        console_capabilities.log_skip_once()
        return {"combined": "", "sources": []}
    assistants = [
        assistant
        for assistant in _select_console_assistants(item, agent)
        if console_capabilities.command_binary(assistant[1]) in available
    ]
    if not assistants:
        return {"combined": "", "sources": []}

    workspace_root = await asyncio.to_thread(
        _resolve_workspace_root, sprint["company_id"]
    )
    if not workspace_root:
        return {"combined": "", "sources": []}

    prompt = _build_console_assistant_prompt(item, agent, sprint["name"])
    notes: list[str] = []
    sources: list[dict[str, str]] = []

    for label, template, purpose in assistants:
        assistant_key = _assistant_key_for_label(label)
        cooldown_until = await asyncio.to_thread(
            _get_assistant_cooldown_until, sprint["company_id"], assistant_key
        )
        if cooldown_until:
            cooldown_local = cooldown_until.astimezone().strftime("%H:%M")
            if label == "Claude CLI":
                await _publish_console_assistant_event(
                    nats_client,
                    sprint["id"],
                    item,
                    label,
                    purpose,
                    f"Claude CLI skipped for now due to quota cooldown until {cooldown_local}. Copilot remains the default assistant.",
                    decision="skipped_quota_cooldown",
                    detail=f"cooldown_until={cooldown_until.isoformat()}",
                )
            else:
                await _publish_console_assistant_event(
                    nats_client,
                    sprint["id"],
                    item,
                    label,
                    purpose,
                    f"{label} skipped temporarily after repeated local availability failures. Cooldown until {cooldown_local}.",
                    decision="skipped_unavailable_cooldown",
                    detail=f"cooldown_until={cooldown_until.isoformat()}",
                )
            continue

        await _publish_console_assistant_event(
            nats_client,
            sprint["id"],
            item,
            label,
            purpose,
            f"Consulting {label} for {purpose} on: {item.get('title', 'task')}",
            decision="requested",
        )

        command = _render_console_command(template, prompt, workspace_root)
        if not command:
            continue
        command = _wrap_console_command_with_fallback(label, command)

        try:
            result = await nats_client.request(
                "workspace.execute",
                {
                    "workspace_root": workspace_root,
                    "command": command,
                    "sprint_id": sprint["id"],
                },
                timeout=CONSOLE_ASSISTANT_TIMEOUT,
            )
        except Exception as exc:  # noqa: BLE001 - Assistant failures fall back without aborting task execution.
            logger.warning(
                "Console assistant request failed for %s on item=%s: %s",
                label,
                item.get("id"),
                exc,
            )
            await _publish_console_assistant_event(
                nats_client,
                sprint["id"],
                item,
                label,
                purpose,
                f"{label} request failed while gathering {purpose}.",
                decision="failed",
                detail=str(exc),
            )
            continue

        output = str(result.get("stdout") or result.get("stderr") or "").strip()
        if not result.get("success"):
            logger.info(
                "Console assistant %s unavailable for item=%s exit=%s",
                label,
                item.get("id"),
                result.get("exit_code"),
            )
            if label == "Claude CLI":
                cooldown_until = await asyncio.to_thread(
                    _maybe_activate_claude_cooldown, sprint["company_id"], output
                )
                if cooldown_until:
                    cooldown_local = cooldown_until.astimezone().strftime("%H:%M")
                    await _publish_console_assistant_event(
                        nats_client,
                        sprint["id"],
                        item,
                        label,
                        purpose,
                        f"Claude CLI hit quota limits and entered cooldown until {cooldown_local}. Future tasks will prefer Copilot until then.",
                        decision="quota_cooldown",
                        detail=output[:CONSOLE_ASSISTANT_EXCERPT_CHARS],
                    )
                    continue
            unavailable_cooldown = await asyncio.to_thread(
                _maybe_activate_assistant_unavailable_cooldown,
                sprint["company_id"],
                label,
                output,
            )
            if unavailable_cooldown:
                cooldown_local = unavailable_cooldown.astimezone().strftime("%H:%M")
                await _publish_console_assistant_event(
                    nats_client,
                    sprint["id"],
                    item,
                    label,
                    purpose,
                    f"{label} is unavailable in the local runtime and entered cooldown until {cooldown_local}.",
                    decision="unavailable_cooldown",
                    detail=output[:CONSOLE_ASSISTANT_EXCERPT_CHARS],
                )
                continue
            await _publish_console_assistant_event(
                nats_client,
                sprint["id"],
                item,
                label,
                purpose,
                f"{label} was unavailable for {purpose}.",
                decision="unavailable",
                detail=output[:CONSOLE_ASSISTANT_EXCERPT_CHARS],
            )
            continue

        if output:
            notes.append(f"[{label}]\n{output[:2500]}")
            sources.append(
                {
                    "label": label,
                    "purpose": purpose,
                    "excerpt": output[:CONSOLE_ASSISTANT_EXCERPT_CHARS],
                }
            )
            await _publish_console_assistant_event(
                nats_client,
                sprint["id"],
                item,
                label,
                purpose,
                f"{label} contributed {purpose} for: {item.get('title', 'task')}",
                decision="used",
                detail=output[:CONSOLE_ASSISTANT_EXCERPT_CHARS],
            )

    return {
        "combined": "\n\n".join(notes),
        "sources": sources,
    }


def _build_console_assistant_usage_comment(sources: list[dict[str, str]]) -> str:
    """Summarize which external assistants were used and their immediate value."""
    if not sources:
        return ""

    lines = ["External assistant context used for this task:"]
    for source in sources:
        label = source.get("label", "Assistant")
        purpose = source.get("purpose", "advice")
        excerpt = (source.get("excerpt") or "").strip().replace("\n", " ")
        lines.append(f"- {label} ({purpose}): {excerpt}")
    return "\n".join(lines)[:1800]


def _describe_follow_up_reason(result: dict[str, Any]) -> str:
    """Render a human-readable reason for autonomous follow-up decisions."""
    reason = result.get("reason")
    if reason == "auto_follow_up_opened":
        return f"Autonomous follow-up opened: {result.get('next_sprint_name', 'Self-improvement sprint')}"
    if reason == "autonomy_below_threshold":
        return "Autonomous follow-up skipped: company autonomy level is below 3."
    if reason == "auto_self_improvement_disabled":
        return "Autonomous follow-up skipped: auto self-improvement is disabled."
    if reason == "active_sprint_already_exists":
        return f"Autonomous follow-up skipped: sprint {result.get('blocking_sprint_id')} is still active or needs split."
    if reason == "planner_returned_no_tasks":
        return "Autonomous follow-up skipped: planner did not produce a usable backlog."
    if reason == "company_missing":
        return "Autonomous follow-up skipped: company context was missing."
    if reason == "completed_no_follow_up":
        return "Sprint completed with no autonomous follow-up trigger."
    return "Autonomous follow-up evaluation completed."


async def _finalize_sprint_if_ready(nats_client: NATSClient, sprint_id: int) -> None:
    """Close a sprint once all cards are done and optionally open the next autonomous self-improvement sprint."""
    # Build retrospective metrics before closing so the planner can use them.
    retro_metrics = await asyncio.to_thread(_build_sprint_retrospective, sprint_id)

    result = await asyncio.to_thread(
        _complete_sprint_and_plan_follow_up, sprint_id, retro_metrics
    )
    if not result.get("completed"):
        return

    # Burn retrospective engram to memory so future sprints can learn from it.
    if retro_metrics:
        try:
            from memory.engram_processor import get_engram_processor

            processor = get_engram_processor()
            processor.burn_engram(
                company_id=retro_metrics.get("company_id"),
                agent_id=f"retrospective_{sprint_id}",
                role="ScrumMaster",
                decision_summary=_format_retrospective_summary(retro_metrics),
                metadata={
                    "action_type": "sprint_retrospective",
                    **retro_metrics,
                },
            )
        except Exception as exc:  # noqa: BLE001 - Optional engram writes must not abort sprint completion.
            logger.warning(
                "Retrospective engram write failed for sprint=%s: %s", sprint_id, exc
            )

        await _publish_glassroom(
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

    await _publish_glassroom(
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

    await _publish_glassroom(
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
        await _publish_glassroom(
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
        if getattr(sprint, "status", None) == "paused":
            logger.info(
                "Sprint %s is paused — skipping item %s",
                item.sprint_id,
                backlog_item_id,
            )
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
                "model": _resolve_agent_model(
                    {"model": agent_row.model, "role": agent_row.role}
                ),
                "seniority": agent_row.seniority,
                "specialty": agent_row.specialty,
                "system_prompt": agent_row.system_prompt,
            }
        else:
            # Fallback: use default config if no matching agent found.
            agent_dict = {
                "role": item.assignee or "Dev",
                "name": "",
                "model": default_model_for_role(item.assignee or "Dev"),
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
    comment_text: str | None = None,
    comment_author: str | None = None,
):
    """Update a backlog item's status and optionally append a comment."""
    with Session(engine) as session:
        item = session.get(BacklogItem, item_id)
        if not item:
            return

        item.status = status
        item.updated_at = datetime.now(UTC)

        if comment_text and comment_author:
            existing = item.comments or []
            if isinstance(existing, str):
                try:
                    existing = json.loads(existing)
                except (json.JSONDecodeError, TypeError):
                    existing = []
            existing.append(
                {
                    "author": comment_author,
                    "text": comment_text[:4000],
                    "created_at": datetime.now(UTC).isoformat(),
                }
            )
            item.comments = existing

        session.add(item)
        session.commit()


# ---------------------------------------------------------------------------
# NATS consumer — the main entry point started from main.py
# ---------------------------------------------------------------------------


TASK_WORKER_PRODUCER = "le-fabrique-task-worker"
DELIVERY_GATE_AUTHOR = "Verifier"


def _delivery_gate(item_id: int) -> tuple[str, str] | None:
    """
    ``(outcome, observation)`` when the task's latest delivery must not reach QA.

    Only the ``files_written`` check gates: a refuted delivery (files missing,
    rejected or different on disk) or an unconfirmed one (daemon silent).
    Other checks — e.g. an unevidenced "tests pass" claim — are left to the
    evidence page, so an honest claim never forces a rework.
    """
    with Session(engine) as session:
        run = session.exec(
            select(AgentRun)
            .where(
                AgentRun.external_ref == f"task-{item_id}",
                AgentRun.producer == TASK_WORKER_PRODUCER,
            )
            .order_by(AgentRun.version.desc())
        ).first()
        if run is None:
            return None
        verification = session.exec(
            select(Verification)
            .where(Verification.agent_run_id == run.id)
            .order_by(Verification.id.desc())
        ).first()
    if verification is None:
        return None
    for check in verification.checks or []:
        if check.get("name") == "files_written" and check.get("outcome") in (
            VERDICT_REFUTED,
            VERDICT_UNVERIFIABLE,
        ):
            return check["outcome"], check.get("observation", "")
    return None


def _sha256_text(content: str) -> str:
    return hashlib.sha256((content or "").encode("utf-8")).hexdigest()


def _summarize_deliverable(deliverable: str, limit: int = 500) -> str:
    """The agent's prose outside the file blocks: its own account of the work."""
    prose = _MANIFEST_BLOCK.sub("", deliverable or "").strip()
    return prose[:limit]


def _record_task_run(
    item: dict[str, Any],
    agent: dict[str, Any],
    sprint: dict[str, Any],
    deliverable: str,
    targets: list[dict[str, str]],
    evidence: dict[str, Any] | None,
) -> tuple[int, str] | None:
    """
    Ingest the deliverable as an ``AgentRun`` and verify it deterministically.

    The claims are what the agent produced (declared paths, its summary); the
    evidence is what the daemon observed on disk.  Returns ``(run_id, verdict)``
    or ``None`` when ingestion fails — recording never breaks the task flow.
    """
    payload = AgentRunCreate(
        company_id=sprint["company_id"],
        external_ref=f"task-{item['id']}",
        producer=TASK_WORKER_PRODUCER,
        # The model the proxy says answered, when it says so.
        model=agent.get("served_model") or agent.get("model") or None,
        agent_role=agent.get("role"),
        subject={
            "sprint_id": sprint["id"],
            "backlog_item_id": item["id"],
            "title": item.get("title"),
        },
        claims={
            "files": [target["path"] for target in targets],
            "summary": _summarize_deliverable(deliverable),
        },
        artifact=deliverable or "",
        finished_at=datetime.now(UTC),
    )
    try:
        with Session(engine) as session:
            run, _created = agent_run_service.ingest_agent_run(session, payload)
            session.commit()
            verification = verify_agent_run(session, run, evidence)
            return run.id, verification.verdict
    except Exception as e:  # noqa: BLE001 - Evidence recording must not block delivery.
        logger.warning("AgentRun recording failed for item=%s: %s", item.get("id"), e)
        return None


def _delivery_comment(
    targets: list[dict[str, str]],
    evidence: dict[str, Any] | None,
    recorded: tuple[int, str] | None,
) -> str:
    run_note = f" — evidence run #{recorded[0]} ({recorded[1]})" if recorded else ""
    outcome, observation = evaluate_workspace_writes(
        [target["path"] for target in targets], evidence
    )
    if outcome == "pass":
        return f"Delivered to {(evidence or {}).get('workspace_root')}: {observation}{run_note}"
    if outcome == VERDICT_REFUTED:
        return f"Delivery refuted: {observation}{run_note}"
    return f"Delivery unconfirmed: {observation}{run_note}"


async def _deliver_artifacts(
    nats_client: NATSClient,
    item: dict[str, Any],
    agent: dict[str, Any],
    sprint: dict[str, Any],
    deliverable: str,
) -> str:
    """
    Write the agent's files into the company's project and record the outcome.

    Files come from the agent's ``path=`` manifest, or — when it declared none —
    from file names in the task text.  The daemon is asked with a NATS request
    and reports, per file, the sha256 of the bytes it read back from disk; the
    worker compares that with the sha256 of the content it sent.  Returns the
    payload for QA review.
    """
    targets = parse_file_manifest(deliverable)
    if not targets:
        targets = await asyncio.to_thread(_extract_artifact_targets, item, deliverable)
    review_payload = _build_review_payload(deliverable, targets)

    evidence: dict[str, Any] | None = None
    if targets:
        workspace_root = await asyncio.to_thread(
            _resolve_workspace_root, sprint["company_id"]
        )
        evidence = {
            "workspace_root": workspace_root,
            "expected": {t["path"]: _sha256_text(t["content"]) for t in targets},
            "workspace_writes": None,
        }
        if not workspace_root:
            evidence["reason"] = "workspace path not configured"
        else:
            try:
                reply = await nats_client.request(
                    "workspace.generate",
                    {
                        "workspace_root": workspace_root,
                        "files": targets,
                        "interaction_id": f"task-{item['id']}",
                        "sprint_id": sprint["id"],
                    },
                    timeout=settings.WORKSPACE_WRITE_TIMEOUT_SECONDS,
                )
                writes = reply.get("files") if isinstance(reply, dict) else None
                evidence["workspace_writes"] = (
                    writes if isinstance(writes, list) else None
                )
                if evidence["workspace_writes"] is None:
                    evidence["reason"] = "the workspace daemon sent no per-file results"
            except Exception as e:  # noqa: BLE001 - A missing daemon leaves delivery unverifiable.
                evidence["reason"] = (
                    f"the workspace daemon did not reply ({type(e).__name__})"
                )
                logger.warning(
                    "Workspace write request failed for item=%s: %s", item["id"], e
                )

    recorded = await asyncio.to_thread(
        _record_task_run, item, agent, sprint, deliverable, targets, evidence
    )

    if targets:
        comment = _delivery_comment(targets, evidence, recorded)
        await asyncio.to_thread(
            _update_item_status,
            item["id"],
            "in_progress",
            comment_text=comment,
            comment_author="System",
        )
        await _publish_glassroom(
            nats_client,
            sprint["id"],
            {
                "type": "status",
                "semantic_type": "task.artifact_delivery",
                "source": "task_worker",
                "agent": "System",
                "role": "System",
                "task_id": item["id"],
                "task_status": "in_progress",
                "phase": "artifact_delivery",
                "task": item["title"],
                "agent_run_id": recorded[0] if recorded else None,
                "verdict": recorded[1] if recorded else None,
                "message": f"🧱 {comment}",
            },
        )

    return review_payload


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

            if _has_manual_freeze_marker(item.get("comments") or []):
                await asyncio.to_thread(_sync_manual_freeze_state, item["id"])
                logger.info(
                    "Skipping manually frozen assignment for item=%s", item["id"]
                )
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
            await asyncio.to_thread(_update_item_status, item["id"], "in_progress")

            await _publish_glassroom(
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

            await _publish_glassroom(
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
            memory_ctx, completed_ctx, assistant_context = await asyncio.gather(
                asyncio.to_thread(_fetch_memory_context, item, agent),
                asyncio.to_thread(
                    _load_sprint_completed_tasks, sprint["id"], item["id"]
                ),
                _collect_console_assistant_advice(nats_client, item, agent, sprint),
            )
            external_advice = assistant_context.get("combined", "")
            assistant_usage_comment = _build_console_assistant_usage_comment(
                assistant_context.get("sources", [])
            )

            if memory_ctx:
                await _publish_glassroom(
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
                    _update_item_status,
                    item["id"],
                    "in_progress",
                    comment_text=assistant_usage_comment,
                    comment_author="System",
                )

            # Architect consultation for design-sensitive or reworked Developer tasks.
            # Recorded as an ``ARCHITECT_CONSULTED:`` comment so the orchestration
            # health analytics can count it.
            architect_advice = ""
            if should_consult_architect(
                item, agent, item.get("comments"), enabled=ARCHITECT_CONSULT_ENABLED
            ):
                prior_feedback = [
                    str(c.get("text", ""))
                    for c in (item.get("comments") or [])
                    if isinstance(c, dict)
                    and classify_review_result(str(c.get("text", ""))) == "NEEDS_WORK"
                ][-2:]
                await _publish_glassroom(
                    nats_client,
                    sprint["id"],
                    {
                        "type": "status",
                        "semantic_type": "task.architect_consult_started",
                        "source": "task_worker",
                        "agent": ARCHITECT_COMMENT_AUTHOR,
                        "role": ARCHITECT_COMMENT_AUTHOR,
                        "task_id": item["id"],
                        "task_status": "in_progress",
                        "phase": "architect_consult",
                        "task": item["title"],
                        "message": f"📐 {agent.get('name') or agent['role']} is consulting the Architect on: {item['title']}",
                    },
                )
                architect_advice = await asyncio.to_thread(
                    _consult_architect, item, agent, sprint, prior_feedback
                )
                if architect_advice:
                    await asyncio.to_thread(
                        _update_item_status,
                        item["id"],
                        "in_progress",
                        comment_text=format_architect_consult_comment(architect_advice),
                        comment_author=ARCHITECT_COMMENT_AUTHOR,
                    )
                    await _publish_glassroom(
                        nats_client,
                        sprint["id"],
                        {
                            "type": "message",
                            "semantic_type": "task.architect_consulted",
                            "source": "task_worker",
                            "agent": ARCHITECT_COMMENT_AUTHOR,
                            "role": ARCHITECT_COMMENT_AUTHOR,
                            "task_id": item["id"],
                            "task_status": "in_progress",
                            "phase": "architect_consult",
                            "task": item["title"],
                            "message": f"📐 Architect brief delivered for: {item['title']}",
                            "preview": architect_advice[:200],
                        },
                    )

            await _publish_glassroom(
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

            # 3. Call the LLM (in a thread).
            deliverable = await asyncio.to_thread(
                _execute_task,
                item,
                agent,
                sprint["name"],
                external_advice,
                memory_ctx,
                completed_ctx,
                architect_advice or None,
                sprint["company_id"],
                sprint["id"],
            )
            deliverable = await asyncio.to_thread(
                _sanitize_deliverable_text, deliverable
            )

            await _publish_glassroom(
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
                item, deliverable
            )
            if not is_valid:
                await asyncio.to_thread(
                    _update_item_status,
                    item["id"],
                    "blocked",
                    comment_text=f"Pre-review quality gate failed: {validation_reason}",
                    comment_author="System",
                )

                await _publish_glassroom(
                    nats_client,
                    sprint["id"],
                    {
                        "type": "error",
                        "semantic_type": "task.blocked",
                        "source": "task_worker",
                        "agent": agent.get("name") or agent["role"],
                        "role": agent["role"],
                        "task_id": item["id"],
                        "task_status": "blocked",
                        "phase": "quality_gate",
                        "task": item["title"],
                        "message": f"❌ Pre-review gate blocked deliverable for: {item['title']} ({validation_reason}).",
                        "preview": deliverable[:200] if deliverable else "",
                    },
                )

                logger.warning(
                    "Pre-review gate blocked item=%s by %s: %s",
                    backlog_item_id,
                    agent.get("name") or agent["role"],
                    validation_reason,
                )
                return

            review_payload = await _deliver_artifacts(
                nats_client, item, agent, sprint, deliverable
            )

            # 4. Save result and mark as in_review.
            # Ensure the deliverable is persisted as a reviewable comment even if
            # artifact normalization produced an empty payload.
            review_text = (review_payload or deliverable or "").strip()
            if not review_text:
                review_text = "(no deliverable text produced)"
            await asyncio.to_thread(
                _update_item_status,
                item["id"],
                "in_review",
                comment_text=review_text,
                comment_author=agent.get("name") or agent["role"],
            )

            await _publish_glassroom(
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

            await _publish_glassroom(
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

            # Mark as blocked on failure.
            if data.get("backlog_item_id"):
                try:
                    await asyncio.to_thread(
                        _update_item_status,
                        data["backlog_item_id"],
                        "blocked",
                        comment_text=(
                            str(e)[:500]
                            if isinstance(e, LLMCallError)
                            else f"Agent error: {str(e)[:500]}"
                        ),
                        comment_author="System",
                    )
                except Exception:  # noqa: BLE001, S110 - Recovery bookkeeping must not mask the task failure.
                    pass

            if data.get("sprint_id"):
                await _publish_glassroom(
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

        finally:
            pass  # msg.ack() is handled by NATSClient._listen()

    logger.info("Task Worker subscribed to task.assign")
    await nats_client.subscribe("task.assign", _on_task_assign)

    recovered_items = await asyncio.to_thread(_recover_inflight_items_for_restart)
    for recovered in recovered_items:
        await _publish_glassroom(
            nats_client,
            recovered["sprint_id"],
            {
                "type": "status",
                "semantic_type": "task.recovered",
                "source": "task_worker",
                "agent": "System",
                "role": "System",
                "task_id": recovered.get("id"),
                "task_status": "todo",
                "phase": "restart_recovery",
                "task": recovered["title"],
                "message": f"♻️ Recovered after backend restart: {recovered['title']} returned to todo for autonomous redispatch.",
            },
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
                "model": _resolve_agent_model(
                    {"model": reviewer.model if reviewer else None, "role": "QA"}
                ),
                "seniority": reviewer.seniority if reviewer else "Senior",
                "system_prompt": reviewer.system_prompt if reviewer else None,
            }

            # Get the deliverable from comments
            comments = item.comments or []
            if isinstance(comments, str):
                try:
                    comments = json.loads(comments)
                except Exception:  # noqa: BLE001 - Malformed legacy comments fall back to an empty list.
                    comments = []

            if _has_manual_freeze_marker(comments):
                item.status = "blocked"
                item.updated_at = datetime.now(UTC)
                session.add(item)
                session.commit()
                logger.info("Skipping QA review for manually frozen item=%s", item.id)
                continue

            deliverable = _extract_latest_reviewable_comment(
                comments, assignee_role=item.assignee
            )

            # Prevent infinite review loops when QA repeatedly fails.
            qa_review_failures = sum(
                1
                for c in comments
                if c.get("author") == "System"
                and "qa review failed" in c.get("text", "").lower()
            )
            if qa_review_failures >= MAX_QA_REVIEW_FAILURES:
                item.status = "blocked"
                item.updated_at = datetime.now(UTC)
                session.add(item)
                session.commit()

                await _publish_glassroom(
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
                comments.append(
                    {
                        "author": "System",
                        "text": "QA review aborted: no deliverable found in task comments.",
                        "created_at": datetime.now(UTC).isoformat(),
                    }
                )
                item.comments = comments
                item.status = "blocked"
                item.updated_at = datetime.now(UTC)
                session.add(item)
                session.commit()

                await _publish_glassroom(
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
            gate = await asyncio.to_thread(_delivery_gate, item.id)
            if gate is not None:
                outcome, observation = gate
                previous_gate_reworks = sum(
                    1
                    for c in comments
                    if c.get("author") == DELIVERY_GATE_AUTHOR
                    and str(c.get("text", "")).upper().startswith("NEEDS WORK:")
                )
                rework = previous_gate_reworks < AUTOPILOT_MAX_REWORK_CYCLES
                label = "refuted" if outcome == VERDICT_REFUTED else "unconfirmed"
                comments.append(
                    {
                        "author": DELIVERY_GATE_AUTHOR,
                        "text": (
                            f"NEEDS WORK: delivery {label} — {observation}. "
                            "Re-run the task and give every file in full as a "
                            "path= block."
                            if rework
                            else f"Delivery {label} after {previous_gate_reworks} "
                            f"rework(s) — blocked for manual triage: {observation}"
                        ),
                        "created_at": datetime.now(UTC).isoformat(),
                    }
                )
                item.comments = comments
                item.status = "todo" if rework else "blocked"
                item.updated_at = datetime.now(UTC)
                session.add(item)
                session.commit()
                if not rework:
                    quality_gate_blocked_ids.add(item.id)
                await _publish_glassroom(
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
                        "message": f"🧱 Delivery {label} for {item.title}: {observation}",
                    },
                )
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
                await _publish_glassroom(
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

                # Append review comment
                comments.append(
                    {
                        "author": reviewer_dict["name"],
                        "text": normalized_review[:2000],
                        "created_at": datetime.now(UTC).isoformat(),
                    }
                )

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
                item.updated_at = datetime.now(UTC)

                if needs_work:
                    if autopilot_rework_allowed:
                        comments.append(
                            {
                                "author": "System",
                                "text": f"Autopilot rework scheduled after QA feedback ({previous_reworks + 1}/{AUTOPILOT_MAX_REWORK_CYCLES}).",
                                "created_at": datetime.now(UTC).isoformat(),
                            }
                        )
                    else:
                        comments.append(
                            {
                                "author": "System",
                                "text": "QA quality gate failed — task blocked pending manual triage.",
                                "created_at": datetime.now(UTC).isoformat(),
                            }
                        )
                        quality_gate_blocked_ids.add(item.id)

                    item.comments = comments

                session.add(item)
                session.commit()

                if needs_work:
                    if autopilot_rework_allowed:
                        await _publish_glassroom(
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
                        await _publish_glassroom(
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
                else:
                    await _publish_glassroom(
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
                    await _finalize_sprint_if_ready(nats_client, sprint.id)

            except Exception as e:  # noqa: BLE001 - Isolate review failures to the affected item.
                logger.warning("Review failed for item=%s: %s", item.id, e)

                comments.append(
                    {
                        "author": "System",
                        "text": f"QA review failed — will retry automatically. Reason: {str(e)[:300]}",
                        "created_at": datetime.now(UTC).isoformat(),
                    }
                )
                item.comments = comments
                item.updated_at = datetime.now(UTC)
                session.add(item)
                session.commit()

                await _publish_glassroom(
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

        for item in blocked:
            if item.id in quality_gate_blocked_ids:
                continue

            comments = item.comments or []
            if isinstance(comments, str):
                try:
                    comments = json.loads(comments)
                except Exception:  # noqa: BLE001 - Malformed legacy comments fall back to an empty list.
                    comments = []

            if _has_manual_freeze_marker(comments):
                continue

            if _can_resume_blocked_item_from_deliverable(
                comments, assignee_role=item.assignee
            ):
                comments.append(
                    {
                        "author": "System",
                        "text": "Autonomous recovery detected a blocked task with an existing deliverable and no explicit block reason. Returning it to QA review.",
                        "created_at": datetime.now(UTC).isoformat(),
                    }
                )
                item.comments = comments
                item.status = "in_review"
                item.updated_at = datetime.now(UTC)
                session.add(item)

                sprint = session.get(Sprint, item.sprint_id)
                if sprint:
                    await _publish_glassroom(
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

            # QA quality-gate blocks require manual triage; do not auto-retry.
            qa_quality_gate_block = any(
                (
                    c.get("author") == "System"
                    and "manual triage" in str(c.get("text", "")).lower()
                )
                or (
                    c.get("author") == "System"
                    and "qa quality gate failed" in str(c.get("text", "")).lower()
                )
                or (
                    c.get("author") == "QA"
                    and str(c.get("text", "")).upper().startswith("NEEDS WORK:")
                )
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
                    or "no deliverable found" in str(c.get("text", "")).lower()
                    or "unfinished in-progress task returned to todo for redispatch"
                    in str(c.get("text", "")).lower()
                )
                for c in comments
            )
            if not retryable_system_block:
                continue

            # Only retry once — check if there's already a retry comment
            retry_count = sum(
                1
                for c in comments
                if c.get("author") == "System" and "retry" in c.get("text", "").lower()
            )
            if retry_count >= 1:
                continue

            comments.append(
                {
                    "author": "System",
                    "text": "Automatic retry — re-queuing blocked task.",
                    "created_at": datetime.now(UTC).isoformat(),
                }
            )
            item.comments = comments
            item.status = "todo"
            item.updated_at = datetime.now(UTC)
            session.add(item)

            sprint = session.get(Sprint, item.sprint_id)
            if sprint:
                await _publish_glassroom(
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
    messages = [{"role": "user", "content": prompt}]
    if agent.get("system_prompt"):
        messages.insert(0, {"role": "system", "content": agent["system_prompt"]})
    model = _resolve_agent_model(agent, default_role="QA")
    result = client.chat(
        model=model,
        messages=messages,
        role=agent.get("role", "QA"),
    )

    failure = _llm_failure(result, model)
    if failure:
        raise LLMCallError(failure)

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
    with Session(engine) as session:
        todo_items = session.exec(
            select(BacklogItem).where(BacklogItem.status == "todo")
        ).all()
        for item in todo_items:
            dispatch_time = datetime.now(UTC)
            comments = item.comments or []
            if isinstance(comments, str):
                try:
                    comments = json.loads(comments)
                except Exception:  # noqa: BLE001 - Malformed legacy comments fall back to an empty list.
                    comments = []

            if _has_manual_freeze_marker(comments):
                item.status = "blocked"
                item.updated_at = datetime.now(UTC)
                session.add(item)
                logger.info(
                    "Keeping manually frozen todo item=%s blocked during dispatch",
                    item.id,
                )
                continue

            existing_deliverable = _extract_latest_reviewable_comment(
                comments,
                assignee_role=item.assignee,
            )
            if existing_deliverable:
                item.status = "in_review"
                item.updated_at = dispatch_time
                session.add(item)
                _clear_queue_marker(item.id)
                await _publish_glassroom(
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

            from uuid import uuid4

            await nats_client.publish(
                "task.assign",
                {
                    "agent": item.assignee or "Dev",
                    "sprint_id": item.sprint_id,
                    "interaction_id": str(uuid4()),
                    "backlog_item_id": item.id,
                },
            )
            _mark_queued(item.id, dispatch_time)
            await _publish_glassroom(
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


async def _publish_glassroom(
    nats_client: NATSClient,
    sprint_id: int,
    event: dict[str, Any],
):
    """Publish a real-time event to the Glassroom SSE stream for a sprint."""
    try:
        event.setdefault("event_id", str(uuid4()))
        event.setdefault("source", "backend")
        event.setdefault("semantic_version", 1)
        event["timestamp"] = datetime.now(UTC).isoformat()
        await nats_client.publish(f"glassroom.{sprint_id}", event)
    except Exception as e:  # noqa: BLE001 - Glassroom notifications are best-effort.
        logger.warning("Failed to publish glassroom event: %s", e)
