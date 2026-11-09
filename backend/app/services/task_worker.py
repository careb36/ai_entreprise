"""
Task Worker: the NATS consumer that has an AI agent execute each backlog item.

Per task: receive ``task.assign`` (from the outbox), load the item and the agent
for its ``assignee`` (role and company, with its configured model and prompt),
call the LLM proxy, move the item to ``in_review`` with the deliverable as a
comment, and publish to Glassroom.
"""

import asyncio
import functools
import hashlib
import json
import os
import re
from datetime import UTC, datetime
from typing import Any
from uuid import uuid4

from config import settings
from database import engine
from llm_proxy_client import LLMProxyClient
from logging_config import setup_logger
from models import (
    Agent,
    AgentMessage,
    BacklogItem,
    Company,
    OutboxEvent,
    ProjectDiscovery,
    Sprint,
)
from models.agent_run import TASK_WORKER_PRODUCER, AgentRun, Verification
from nats_client import NATSClient
from schemas import SCOPE_HARD_MAX, SCOPE_LIMITS, AgentRunCreate
from sqlmodel import Session, select, text

from services import (
    acceptance,
    agent_run_service,
    console_assistants,
    console_producer,
    delivery_gate,
    dependencies,
    developer_tools,
    edit_blocks,
    edit_plan,
    git_workflow,
    integration_jobs,
    locks,
    mcp_tools,
    model_routing,
    procedures,
    replicas,
    retrospective,
    review_digest,
    risk,
    scrum_master,
    spec_planning,
    sprint_control,
    sprint_team,
    standards,
    task_events,
    task_projection,
    workspace_daemon,
)
from services.answers import BRIEF_MAX_TOKENS, REVIEW_MAX_TOKENS
from services.answers import (
    gate_reason as _gate_reason,
)
from services.answers import (
    llm_content as _llm_content,
)
from services.answers import (
    llm_failure as _llm_failure,
)
from services.answers import (
    llm_finish_reason as _llm_finish_reason,
)
from services.answers import (
    review_format_reminder as _review_format_reminder,
)
from services.architect import track_record as architect_track_record
from services.architect import track_record_context as architect_record_context
from services.board import (
    DELIVERY_GATE_AUTHOR,
    QUALITY_GATE_AUTHOR,
    RISK_HOLD_PREFIX,
    SCRUM_MASTER_ROLE,
    completed_tasks_context,
)
from services.carry_over import carry_over_on_the_companys_own, previous_attempt_context
from services.console_assistants import normalize_utc
from services.glassroom import publish_glassroom
from services.orchestration_metrics import (
    ACTION_ARCHITECT_CONSULT,
    ACTION_EXECUTE_TASK,
    ACTION_QA_REVIEW,
    ARCHITECT_COMMENT_AUTHOR,
    KPI_ROLE_ARCHITECT,
    RESULT_NEEDS_WORK,
    VERDICT_REFUTED,
    build_architect_consult_prompt,
    classify_review_result,
    format_architect_consult_comment,
    record_agent_turn,
    should_consult_architect,
)
from services.sprint_close import RECLOSED
from services.sprint_close import (
    describe_follow_up_reason as _describe_follow_up_reason,
)
from services.sprint_close import reclosed as _reclosed
from services.sprint_planner import (
    build_self_improvement_objective,
    plan_self_improvement_sprint,
)
from services.verification import evaluate_workspace_writes, verify_agent_run
from services.workspace_daemon import detect_test_command
from services.workspace_edits import delivered, observe_tools, with_tool_edits

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
ARCHITECT_CONSULT_ENABLED = settings.ARCHITECT_CONSULT_ENABLED
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
) + edit_blocks.INSTRUCTIONS


# ---------------------------------------------------------------------------
# Memory augmentation helpers — fail silently, never block task execution
# ---------------------------------------------------------------------------


def _fetch_memory_context(
    item: dict[str, Any], agent: dict[str, Any], company_id: int | None
) -> str:
    """Query engram RAG for this company's past decisions relevant to the task.

    Without a company there is nothing to scope the search to, so nothing is
    returned rather than another tenant's decisions.
    """
    if company_id is None:
        return ""
    task_text = f"{item.get('title', '')} {item.get('description', '')}".strip()
    role = agent.get("role") or None
    try:
        from memory.engram_processor import get_engram_processor

        processor = get_engram_processor()
        return processor.query_relevant_engrams(
            task_text, limit=3, role_filter=role, company_id=company_id
        )
    except Exception as exc:  # noqa: BLE001 - Optional memory context must not block task execution.
        logger.debug("Memory context fetch failed for item=%s: %s", item.get("id"), exc)
        return ""


LESSON_SPRINTS = 2
LESSON_FEEDBACK_LIMIT = 6
LESSON_FEEDBACK_CHARS = 300


def _is_lesson(comment: dict[str, Any]) -> bool:
    """Review feedback worth carrying to later tasks.

    Reviewers' ``NEEDS WORK:`` requests, and the verifier's failed test runs.
    The verifier's other requests (a delivery the daemon did not confirm) are
    about infrastructure, not about how the work was done.
    """
    text = str(comment.get("text", "")).strip()
    if not text.upper().startswith("NEEDS WORK:"):
        return False
    if comment.get("author") == DELIVERY_GATE_AUTHOR:
        return text[len("NEEDS WORK:") :].strip().lower().startswith("tests failed")
    return True


def _load_recent_lessons(company_id: int | None, current_sprint_id: int) -> str:
    """What this company's latest completed sprints taught, read from the board.

    The retrospective counts and the review feedback come from Postgres, so
    the lessons reach the agents with or without the knowledge graph. Review
    feedback is what reviewers said, not a verified fact; the prompt says so.
    """
    if company_id is None:
        return ""
    try:
        with Session(engine) as session:
            sprint_ids = session.exec(
                select(Sprint.id)
                .where(
                    Sprint.company_id == company_id,
                    Sprint.status == "completed",
                    Sprint.id != current_sprint_id,
                )
                .order_by(Sprint.id.desc())
                .limit(LESSON_SPRINTS)
            ).all()
            if not sprint_ids:
                return ""
            items = session.exec(
                select(BacklogItem).where(BacklogItem.sprint_id.in_(sprint_ids))
            ).all()
            feedback: list[tuple[str, str]] = []
            for item in items:
                comments = item.comments or []
                if isinstance(comments, str):
                    try:
                        comments = json.loads(comments)
                    except Exception:  # noqa: BLE001 - Malformed legacy comments carry no lessons.
                        comments = []
                for comment in comments:
                    if isinstance(comment, dict) and _is_lesson(comment):
                        text = str(comment.get("text", "")).strip()
                        author = comment.get("author") or "Reviewer"
                        line = f"{item.title[:60]} ({author}): {text[:LESSON_FEEDBACK_CHARS]}"
                        feedback.append((str(comment.get("created_at") or ""), line))
    except Exception as exc:  # noqa: BLE001 - Lessons are best-effort context.
        logger.debug("Lessons fetch failed for company=%s: %s", company_id, exc)
        return ""

    sections = [
        _format_retrospective_summary(metrics)
        for metrics in (_build_sprint_retrospective(sid) for sid in sprint_ids)
        if metrics
    ]
    seen: set[str] = set()
    requests: list[str] = []
    for _created_at, line in sorted(feedback, reverse=True):
        if line not in seen:
            seen.add(line)
            requests.append(f"- {line}")
        if len(requests) >= LESSON_FEEDBACK_LIMIT:
            break
    if requests:
        sections.append(
            "Work sent back for rework (most recent first):\n" + "\n".join(requests)
        )
    return "\n\n".join(sections)


def _load_sprint_completed_tasks(
    sprint_id: int, exclude_item_id: int, limit: int = 5
) -> str:
    """A brief summary of done tasks in the same sprint, for cross-task context."""
    return completed_tasks_context(engine, sprint_id, exclude_item_id, limit)


# ---------------------------------------------------------------------------
# Sprint retrospective — written to memory after sprint completion
# ---------------------------------------------------------------------------


def _build_sprint_retrospective(sprint_id: int) -> dict[str, Any]:
    """Sprint health metrics for the retrospective (``services/retrospective``)."""
    return retrospective.build(engine, sprint_id)


_format_retrospective_summary = retrospective.summary


# ---------------------------------------------------------------------------
# LLM interaction — runs in a thread to avoid blocking the event loop
# ---------------------------------------------------------------------------


class LLMCallError(RuntimeError):
    """An LLM call that produced no usable answer; its text goes on the board.

    ``credit_provider`` names the provider when it refused for lack of credit
    (``sprint_control.credit_failure``): the sprint is paused, not the task blocked.
    """

    def __init__(self, message: str, credit_provider: str | None = None):
        super().__init__(message)
        self.credit_provider = credit_provider


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
    lessons_context: str | None = None,
    procedures_context: str | None = None,
    requirements_context: str | None = None,
) -> str:
    """
    Call the LLM to produce a deliverable for the given backlog item.

    Runs synchronously (``asyncio.to_thread``). Returns the LLM's text. An
    empty agent model resolves to the role's default; the model the proxy
    reports is stored as ``agent["served_model"]``. Raises ``LLMCallError``
    on a failed or empty call. With ``company_id`` the turn goes to
    ``audit_log`` (role, company, token cost) for the KPIs.
    """
    client = LLMProxyClient(os.getenv("LLM_PROXY_URL", "http://llm-proxy:5100"))
    client.company_id = company_id

    system_prompt = agent.get("system_prompt") or (
        f"You are a {agent.get('seniority', 'Senior')} {agent.get('role', 'Developer')} "
        f"specializing in {agent.get('specialty', 'software engineering')}. "
        "Produce clear, actionable, production-quality deliverables."
    )

    # Sections run from the most stable to the most variable, so the providers'
    # prefix cache bills the shared start of the prompt at the cached rate:
    # format, procedures and lessons first; the task and its history next;
    # what changes between attempts (the brief, the review feedback) last.
    user_prompt = FILE_MANIFEST_INSTRUCTIONS.lstrip("\n") + (procedures_context or "")
    if lessons_context:
        user_prompt += (
            "\n\n## Lessons from Recent Sprints\n"
            "How this company's last sprints went and what reviewers sent back. "
            "The review requests are reviewers' opinions, not verified facts; use "
            "them to avoid repeating the same mistakes.\n\n"
            f"{lessons_context[:3000]}"
        )
    user_prompt += (
        f"\n\n## Task: {item['title']}\n\n"
        f"{item.get('description', '')}\n\n"
        f"Sprint: {sprint_name}\n"
        f"Priority: {item.get('priority', 'medium')}\n\n"
        "Produce a complete, professional deliverable for this task. "
        "Be thorough and specific to the project context."
    ) + (requirements_context or "")
    user_prompt += developer_tools.repository_section(
        company_id, agent.get("role"), item.get("id"), engine=engine
    )

    if memory_context:
        user_prompt += (
            "\n\n## Memory: Relevant Past Decisions\n"
            "These are decisions and deliverables from previous sprints in this factory. "
            "Use them to maintain consistency and avoid repeating past mistakes.\n\n"
            f"{memory_context[:3000]}"
        )

    # A task carried over from an earlier sprint says what blocked it there.
    user_prompt += previous_attempt_context(item)

    if completed_tasks_context:
        user_prompt += (
            "\n\n## Sprint Context: Already Completed Tasks\n"
            f"{completed_tasks_context[:2000]}"
        )

    if external_advice:
        user_prompt += (
            "\n\n## External Console Assistant Notes\n"
            "The following advice was gathered from configured terminal assistants such as GitHub Copilot CLI or Claude CLI. "
            "Treat it as optional supporting context, not as a replacement for reasoning about the current repository. "
            "Prefer preserving working behavior over blindly following suggestions.\n\n"
            f"{external_advice[:4000]}"
        )

    user_prompt += edit_plan.prompt_section(architect_advice)  # A plan, or guidance.

    rework_feedback = task_projection.record_from_dict(item).pending_rework_feedback()
    if rework_feedback:
        user_prompt += (
            "\n\n## Review feedback to address\n"
            "Your previous deliverable for this task was sent back. Fix every point "
            "below and deliver the complete result again.\n\n"
            + "\n".join(f"- {text[:1500]}" for text in rework_feedback)
        )

    model = model_routing.resolve_agent_model(
        agent,
        item=item,
        rework=bool(rework_feedback),
        editor=bool(edit_plan.plan_in(architect_advice)),
    )
    role = agent.get("role", "Dev")

    who = agent.get("name") or role
    logger.info(
        "Agent %s (%s) working on: %s [model=%s]", who, role, item["title"], model
    )

    def record(llm_result: dict[str, Any], preview: str) -> None:
        if company_id is not None:
            record_agent_turn(
                company_id=company_id,
                sprint_id=sprint_id,
                backlog_item_id=item.get("id"),
                agent={**agent, "model": llm_result.get("served_model") or model},
                action=ACTION_EXECUTE_TASK,
                llm_result=llm_result,
                result_preview=preview,
            )

    # The workspace and MCP tools for this role: each call an observation.
    toolbox = developer_tools.toolbox_for(
        company_id, role, engine=engine, item_id=item.get("id")
    )
    try:
        result, tool_calls = mcp_tools.chat_with_tools(
            client,
            [
                {"role": "system", "content": system_prompt},
                {"role": "user", "content": user_prompt},
            ],
            model,
            role,
            toolbox,
            on_round=lambda r, calls: record(
                r, "tool calls: " + mcp_tools.summarize(calls)
            ),
        )
    finally:
        if toolbox is not None:
            toolbox.close()  # The tools' edits come back as files to deliver.
    agent["tool_calls"] = tool_calls
    agent["tool_edits"] = getattr(toolbox, "edited_files", [])

    failure = _llm_failure(result, model)
    if failure:
        raise LLMCallError(failure, sprint_control.credit_failure(result))

    content = _llm_content(result)
    agent["served_model"] = result.get("served_model") or None
    agent["finish_reason"] = _llm_finish_reason(result)
    record(result, content)
    return content


def _consult_architect(
    item: dict[str, Any],
    developer: dict[str, Any],
    sprint: dict[str, Any],
    prior_feedback: list[str] | None = None,
) -> str:
    """Ask the company's Architect agent for a short brief on *item*, with the
    repository map. Synchronous (via ``asyncio.to_thread``); ``""`` when no
    Architect exists or the call fails (no consultation happened). Successful
    calls are written to ``audit_log`` with ``role=Architect``."""
    with Session(engine) as session:
        architect_row = session.exec(
            select(Agent).where(
                Agent.company_id == sprint["company_id"],
                Agent.role == KPI_ROLE_ARCHITECT,
            )
        ).first()
        cm = model_routing.company_model(session, sprint["company_id"], "Architect")
        # Its record from the boards; a failed count only leaves it out.
        record = ""
        if architect_row:
            try:
                record = architect_record_context(
                    architect_track_record(session, sprint["company_id"])
                )
            except Exception as exc:  # noqa: BLE001 - The brief goes without its record.
                logger.warning(
                    "Architect record unavailable for company=%s: %s",
                    sprint["company_id"],
                    exc,
                )

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
        "model": model_routing.resolve_agent_model(
            {"model": architect_row.model or cm, "role": KPI_ROLE_ARCHITECT}
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
        item, developer, sprint.get("name", ""), prior_feedback or (), record
    )
    prompt += edit_plan.request_section(item)
    prompt += developer_tools.repository_section(
        sprint["company_id"], KPI_ROLE_ARCHITECT, item.get("id"), engine=engine
    )

    client = LLMProxyClient(os.getenv("LLM_PROXY_URL", "http://llm-proxy:5100"))
    client.max_tokens, client.company_id = BRIEF_MAX_TOKENS, sprint["company_id"]
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

    Each block becomes one ``{"path", "content"}`` entry (plus ``edits`` when marked ``edit``).
    Unsafe paths (absolute, ``..``) and disallowed extensions are dropped.  When
    the same path appears twice the last block wins, keeping the position of
    the first.  Returns ``[]`` when the answer declares no files.
    """
    files: dict[str, dict] = {}
    for block in _MANIFEST_BLOCK.finditer(deliverable or ""):
        match = _MANIFEST_PATH.search(block.group("info"))
        if not match:
            continue
        rel = _sanitize_relative_path(next(g for g in match.groups() if g))
        if not rel:
            continue
        if os.path.splitext(rel)[1].lower() not in ALLOWED_ARTIFACT_EXTENSIONS:
            continue
        files[rel] = {"path": rel, "content": block.group("body")}
        if edit_blocks.is_edit_block(block.group("info"), match):
            files[rel]["edits"] = edit_blocks.parse(block.group("body"))
    return list(files.values())


def expected_hashes(targets: list[dict[str, Any]]) -> dict[str, str]:
    """The sha256 of what was sent; none for an edit, whose result only the daemon knows."""
    return {t["path"]: _sha256_text(t["content"]) for t in targets if "edits" not in t}


def _build_review_payload(
    deliverable: str, artifact_targets: list[dict[str, str]]
) -> str:
    """What QA reviews: a digest of every file when files were written
    (``review_digest``), else the deliverable text."""
    if not artifact_targets:
        return (deliverable or "").strip()
    return review_digest.build(artifact_targets, _summarize_deliverable(deliverable))


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

        # The agents carry their company, so a connection model routes with its key.
        _, agent_dicts, discovery = spec_planning.planning_context_of(
            session, company.id
        )
        detected_stack = _coerce_json_dict(
            discovery.detected_stack if discovery else None
        )
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
            specification_id=sprint.specification_id,
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
                    SCOPE_LIMITS.get(scope_size, SCOPE_HARD_MAX) - len(tasks),
                )
        except Exception as exc:  # noqa: BLE001 - A failed carry-over never stops the follow-up sprint.
            logger.warning("Carry-over into sprint=%s failed: %s", next_sprint.id, exc)
            carried = []
        backlog_payloads.extend(
            {"title": item.title, "description": item.description or ""}
            for item in carried
        )
        result["carried_over"] = sprint_team.automatic_team(next_sprint, plan, carried)

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
    integration_jobs.schedule_after_close(nats_client, engine, sprint_id)  # issue #314
    if result.get("reason") == RECLOSED:
        retro_metrics = None  # learned once, at the first close
    if retro_metrics and retro_metrics.get("company_id") is not None:
        await asyncio.to_thread(
            _post_message,
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
                _evaluate_procedures, retro_metrics["company_id"], sprint_id
            )
        except Exception as exc:  # noqa: BLE001
            logger.warning(
                "Procedure evaluation failed for sprint=%s: %s", sprint_id, exc
            )
        try:
            await asyncio.to_thread(
                _propose_procedures,
                retro_metrics["company_id"],
                sprint_id,
                retro_metrics,
            )
        except Exception as exc:  # noqa: BLE001
            logger.warning(
                "Procedure proposal failed for sprint=%s: %s", sprint_id, exc
            )

    # Burn retrospective engram to memory so future sprints can learn from it.
    if retro_metrics:
        try:
            from memory.engram_processor import (
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
            )
        except Exception as exc:  # noqa: BLE001 - Optional engram writes must not abort sprint completion.
            logger.warning(
                "Retrospective engram write failed for sprint=%s: %s", sprint_id, exc
            )

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


def _append_item_comment(
    item_id: int, text: str, author: str, kind: str = "note", **fields: Any
) -> None:
    """Record an event on a backlog item, rendered as a comment, without changing its status."""
    task_events.record_by_id(engine, item_id, kind, text=text, author=author, **fields)


def _update_item_status(
    item_id: int,
    status: str,
    comment_text: str | None = None,
    comment_author: str | None = None,
    kind: str | None = None,
    **fields: Any,
):
    """Record a transition of a backlog item: its event, its status and the comment rendering it."""
    text = comment_text if comment_text and comment_author else None
    task_events.record_by_id(
        engine,
        item_id,
        kind or ("note" if text else "moved"),
        status=status,
        text=text,
        author=comment_author if text else None,
        **fields,
    )


# ---------------------------------------------------------------------------
# The conversation between roles: messages are claims; the evidence decides.
# ---------------------------------------------------------------------------

MESSAGE_BODY_LIMIT = 4000


def _message(
    company_id: int,
    sprint_id: int | None,
    item_id: int | None,
    from_name: str,
    from_role: str,
    to_role: str | None,
    kind: str,
    body: str,
) -> AgentMessage:
    return AgentMessage(
        company_id=company_id,
        sprint_id=sprint_id,
        backlog_item_id=item_id,
        from_name=from_name,
        from_role=from_role,
        to_role=to_role,
        kind=kind,
        body=body[:MESSAGE_BODY_LIMIT],
    )


def _post_message(**fields: Any) -> int | None:
    """Record a message in its own transaction; never stops the task.

    Returns the message's id, or ``None`` when it was not recorded.
    """
    try:
        with Session(engine) as session:
            message = _message(**fields)
            session.add(message)
            session.commit()
            return message.id
    except Exception as exc:  # noqa: BLE001 - The conversation record is best-effort.
        logger.warning("Agent message not recorded (%s): %s", fields.get("kind"), exc)
        return None


def _triage_blocked_task(
    company_id: int,
    sprint_id: int,
    item_id: int,
    reason: str,
    escalation_id: int | None = None,
) -> None:
    """The ScrumMaster answers an escalation; a failed answer changes nothing.

    The prompt is read and the decision written in two short sessions, so no
    database connection waits while the model answers. The decision names the
    escalation it answers (*escalation_id*), so a late answer is never read
    against a newer escalation. On Postgres the escalation's advisory lock
    keeps one idle connection while the model answers.
    """
    try:
        # Once per escalation, even with several replicas (issue #328).
        with replicas.triage_slot(engine, item_id, escalation_id) as mine:
            if not mine:
                return
            with Session(engine) as session:
                item = session.get(BacklogItem, item_id)
                if item is None:
                    return
                task_events.escalated(session, item, escalation_id, reason)
                session.commit()
                assignee = item.assignee
                prompt, model, system_prompt = scrum_master.prepare_triage(
                    session, company_id, item, reason
                )
            decision = scrum_master.decide(
                scrum_master.ask_triage(model, system_prompt, prompt, company_id),
                item_id,
            )
            if decision is None:
                return
            with Session(engine) as session:
                scrum_master.record_decision(
                    session,
                    company_id,
                    sprint_id,
                    item_id,
                    assignee,
                    decision,
                    answers=escalation_id,
                )
                session.commit()
    except Exception as exc:  # noqa: BLE001 - The triage is advice; the task stays blocked for a person either way.
        logger.warning("ScrumMaster triage not recorded for item=%s: %s", item_id, exc)


def _handoff_body(title: str, deliverable: str) -> str:
    """What the developer hands to review: the files it declared, by path."""
    paths = [entry["path"] for entry in parse_file_manifest(deliverable or "")]
    if not paths:
        return f'Delivered "{title}" for review (no files declared).'
    listed = ", ".join(paths[:10]) + (
        f" and {len(paths) - 10} more" if len(paths) > 10 else ""
    )
    return f'Delivered "{title}" for review: {len(paths)} file(s) declared — {listed}.'


def _load_procedures_context(company_id: int | None, role: str | None) -> str:
    """The factory's standards for *role*, then the company's active procedures, as prompt sections."""
    return standards.prompt_section(role) + procedures.prompt_context(
        engine, company_id, role
    )


def _evaluate_procedures(company_id: int, sprint_id: int) -> None:
    """Keep or revert procedures with enough sprints since adoption, and tell the team."""
    with Session(engine) as session:
        decided = procedures.evaluate(session, company_id)
        for procedure in decided:
            session.add(
                _message(
                    company_id,
                    sprint_id,
                    None,
                    SCRUM_MASTER_ROLE,
                    SCRUM_MASTER_ROLE,
                    None,
                    "proposal",
                    procedures.describe_outcome(procedure),
                )
            )
        session.commit()


def _ask_scrum_master_for_procedures(
    company_id: int, sprint_id: int, retro_metrics: dict[str, Any]
) -> tuple[str, list[str]]:
    """The ScrumMaster's answer, and the company's roles it may propose for."""
    lessons = _load_recent_lessons(company_id, 0)
    with Session(engine) as session:
        roles = sorted(
            {
                role
                for role in session.exec(
                    select(Agent.role).where(Agent.company_id == company_id)
                ).all()
                if role
            }
        )
        current = procedures.procedures_prompt(
            list(
                session.exec(
                    select(procedures.Procedure).where(
                        procedures.Procedure.company_id == company_id,
                        procedures.Procedure.status == "active",
                    )
                ).all()
            )
        )
        history = procedures.history_prompt(session, company_id)
        prompt = (
            "You are the ScrumMaster. A sprint just finished. Read how it went and decide "
            "whether a written procedure for one of the roles would have prevented its "
            "rework or blocked tasks.\n\n"
            f"## What happened\n{_format_retrospective_summary(retro_metrics)}\n\n"
            f"## Lessons from the last sprints\n{lessons or '(none)'}\n"
            f"{current or chr(10) + '## Company Procedures' + chr(10) + '(none yet)'}"
            f"{history}\n\n"
            "A procedure is a short, concrete rule a role can follow on every task. Do not "
            "repeat an existing one, nor one that was reverted or rejected, unless you "
            "change what made it fail.\n\n" + procedures.proposal_format(roles)
        )
        model, system_prompt = scrum_master.profile(session, company_id)
    # Outside the session, usual retries: a transient error must not lose proposals.
    answer = scrum_master.ask_model(model, system_prompt, prompt, company_id=company_id)
    return answer, roles


def _propose_procedures(
    company_id: int, sprint_id: int, retro_metrics: dict[str, Any]
) -> None:
    """After a sprint with rework or blocked tasks, the ScrumMaster proposes procedures.

    With ``autonomy_level`` 3 a proposal is adopted at once; otherwise it waits
    for a Manager. Either way the board's numbers keep or revert it later.
    """
    if not (retro_metrics.get("rework_cycles") or retro_metrics.get("blocked_tasks")):
        return  # nothing went wrong: nothing to learn
    answer, roles = _ask_scrum_master_for_procedures(
        company_id, sprint_id, retro_metrics
    )
    proposals = procedures.parse_proposals(answer, roles)
    if not proposals:
        return
    with Session(engine) as session:
        company = session.get(Company, company_id)
        autonomous = bool(company and (company.autonomy_level or 0) >= 3)
        created = procedures.propose(
            session,
            company_id,
            proposals,
            SCRUM_MASTER_ROLE,
            source_sprint_id=sprint_id,
            skip_failed=True,
        )
        for procedure in created:
            if autonomous:
                procedures.adopt(session, procedure, "autonomy")
            state = (
                "Adopted (autonomy level 3)"
                if autonomous
                else "Proposed, waiting for a Manager"
            )
            session.add(
                _message(
                    company_id,
                    sprint_id,
                    None,
                    SCRUM_MASTER_ROLE,
                    SCRUM_MASTER_ROLE,
                    None if procedure.role == procedures.ALL_ROLES else procedure.role,
                    "proposal",
                    f'{state}: "{procedure.title}" (v{procedure.version})\n'
                    f"{procedure.body}\nWhy: {procedure.rationale or '-'}",
                )
            )
        session.commit()


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


# The gate before QA, the risk tier and the latest verification: services/delivery_gate.
def _delivery_gate(company_id: int, item_id: int) -> tuple[str, str] | None:
    return delivery_gate.gate(engine, company_id, item_id)


def _delivery_risk(company_id: int, item_id: int) -> dict[str, Any] | None:
    return delivery_gate.risk(engine, company_id, item_id)


def _latest_task_verification(company_id: int, item_id: int) -> Verification | None:
    return delivery_gate.latest_verification(engine, company_id, item_id)


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
        # Only the model the proxy reported as having answered: an observation.
        # When it reported none the field stays empty rather than defaulting
        # to the model that was requested (POSITIONING rule 7).
        model=agent.get("served_model") or None,
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
    # Tool calls and edited files: observations beside the daemon's, not claims.
    evidence = observe_tools(evidence, agent)
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
    if (evidence or {}).get("delivery_queued"):
        return f"Delivery queued: {observation}{run_note}"
    return f"Delivery unconfirmed: {observation}{run_note}"


# The project's test command is detected in services/workspace_daemon.
_detect_test_command = detect_test_command
_detect_project_test_command = workspace_daemon.detect_project_test_command


_run_delivery_tests = workspace_daemon.run_delivery_tests


def _plan_queued_delivery(
    item: dict[str, Any],
    sprint: dict[str, Any],
    workspace_root: str,
    targets: list[dict[str, str]],
    evidence: dict[str, Any],
) -> dict[str, Any]:
    """The ``workspace.deliver`` job that keeps the files for an offline daemon.

    Marks *evidence* as waiting for it, so the delivery gate holds the task; the
    job is published only once that record exists, for a daemon reporting at once.
    """
    job_id = str(uuid4())
    evidence["delivery_queued"] = {"job_id": job_id}
    evidence["reason"] = (
        f"{evidence.get('reason') or 'the workspace daemon did not reply'}; "
        "delivery queued until the daemon connects"
    )
    return {
        "job_id": job_id,
        "workspace_root": workspace_root,
        "files": targets,
        "interaction_id": f"task-{item['id']}",
        "company_id": sprint["company_id"],
        "sprint_id": sprint["id"],
        "item_id": item["id"],
    }


async def _publish_queued_delivery(
    nats_client: NATSClient, job: dict[str, Any], evidence: dict[str, Any]
) -> bool:
    """
    Store *job* on ``workspace.deliver``; the daemon writes it when it connects
    and reports on ``workspace.delivered`` (``_on_delivery_result``).

    When it cannot be stored, *evidence* stops claiming the task waits for it.
    """
    try:
        await nats_client.publish(
            "workspace.deliver", job, msg_id=f"deliver-{job['job_id']}"
        )
        return True
    except Exception as e:  # noqa: BLE001 - Unqueued, the delivery stays unconfirmed.
        logger.warning("Could not queue delivery for item=%s: %s", job["item_id"], e)
        evidence.pop("delivery_queued", None)
        evidence["reason"] = evidence["reason"].replace(
            "delivery queued until the daemon connects",
            f"queueing the delivery failed ({type(e).__name__})",
        )
        return False


def _queued_delivery_evidence(
    company_id: int, item_id: int, job_id: str
) -> tuple[int, dict[str, Any], list[str]] | None:
    """The run whose latest verification is waiting for *job_id*, if any."""
    with Session(engine) as session:
        run = session.exec(
            select(AgentRun)
            .where(
                AgentRun.company_id == company_id,
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
        evidence = dict((verification.evidence or {}) if verification else {})
        if (evidence.get("delivery_queued") or {}).get("job_id") != job_id:
            return None
        files = [p for p in (run.claims or {}).get("files") or [] if isinstance(p, str)]
        return run.id, evidence, files


def _reverify_run(run_id: int, evidence: dict[str, Any]) -> tuple[int, str] | None:
    try:
        with Session(engine) as session:
            run = session.get(AgentRun, run_id)
            verification = verify_agent_run(session, run, evidence)
            return run.id, verification.verdict
    except Exception as e:  # noqa: BLE001 - Evidence recording must not stop the consumer.
        logger.warning("Re-verification failed for run=%s: %s", run_id, e)
        return None


async def _on_delivery_result(nats_client: NATSClient, data: dict[str, Any]) -> None:
    """
    Record what the daemon wrote for a delivery that had been queued.

    The expected hashes come from the backend's own record of what it sent;
    the result supplies only the daemon's per-file observation.  A result for
    a job the task no longer waits for (a newer delivery superseded it) is
    ignored.
    """
    job_id = data.get("job_id")
    company_id, item_id, sprint_id = (
        data.get("company_id"),
        data.get("item_id"),
        data.get("sprint_id"),
    )
    if not job_id or company_id is None or item_id is None:
        logger.warning("Ignoring malformed workspace.delivered message: %s", data)
        return
    pending = await asyncio.to_thread(
        _queued_delivery_evidence, company_id, item_id, job_id
    )
    if pending is None:
        logger.info("No task waits for delivery job=%s; ignoring", job_id)
        return
    run_id, evidence, files = pending
    evidence.pop("delivery_queued", None)
    evidence.pop("reason", None)
    writes = data.get("files")
    evidence["workspace_writes"] = writes if isinstance(writes, list) else None
    if evidence["workspace_writes"] is None:
        evidence["reason"] = data.get("error") or (
            "the workspace daemon sent no per-file results"
        )
    git_workflow.record_reply(evidence, data)
    evidence["delivered_after_queue"] = True

    written, _ = evaluate_workspace_writes(files, evidence)
    if settings.DELIVERY_TESTS_ENABLED and written == "pass":
        evidence["acceptance"] = await asyncio.to_thread(
            acceptance.evidence_for, engine, item_id
        )
        await acceptance.guard_test_files(
            nats_client, engine, evidence, settings.DELIVERY_TEST_TIMEOUT_SECONDS
        )
        await _run_delivery_tests(nats_client, {"id": sprint_id}, evidence, engine)

    recorded = await asyncio.to_thread(_reverify_run, run_id, evidence)
    comment = _delivery_comment([{"path": p} for p in files], evidence, recorded)
    await asyncio.to_thread(
        _append_item_comment,
        item_id,
        f"Queued delivery: {comment}",
        "System",
        "verified",
        run_id=recorded[0] if recorded else None,
    )
    if sprint_id is not None:
        await publish_glassroom(
            nats_client,
            sprint_id,
            {
                "type": "status",
                "semantic_type": "task.artifact_delivery",
                "source": "task_worker",
                "agent": "System",
                "role": "System",
                "task_id": item_id,
                "phase": "artifact_delivery",
                "agent_run_id": recorded[0] if recorded else None,
                "verdict": recorded[1] if recorded else None,
                "message": f"🧱 Queued delivery: {comment}",
            },
        )


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
    targets = with_tool_edits(parse_file_manifest(deliverable), agent.get("tool_edits"))
    if not targets:
        targets = await asyncio.to_thread(_extract_artifact_targets, item, deliverable)
    review_payload = _build_review_payload(deliverable, targets)

    evidence: dict[str, Any] | None = None
    queued_job: dict[str, Any] | None = None
    if targets:
        workspace_root = await asyncio.to_thread(
            _resolve_workspace_root, sprint["company_id"]
        )
        git_spec = await asyncio.to_thread(
            git_workflow.delivery_spec, engine, sprint, item
        )
        evidence = {
            "workspace_root": workspace_root,
            "expected": expected_hashes(targets),
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
                        **({"git": git_spec} if git_spec else {}),
                    },
                    timeout=settings.WORKSPACE_WRITE_TIMEOUT_SECONDS,
                )
                writes = reply.get("files") if isinstance(reply, dict) else None
                evidence["workspace_writes"] = (
                    writes if isinstance(writes, list) else None
                )
                if evidence["workspace_writes"] is None:
                    evidence["reason"] = "the workspace daemon sent no per-file results"
                git_workflow.record_reply(evidence, reply)
            except Exception as e:  # noqa: BLE001 - A missing daemon leaves delivery unverifiable.
                evidence["reason"] = (
                    f"the workspace daemon did not reply ({type(e).__name__})"
                )
                logger.warning(
                    "Workspace write request failed for item=%s: %s", item["id"], e
                )
                if nats_client.jetstream_enabled:
                    queued_job = _plan_queued_delivery(
                        item, sprint, workspace_root, targets, evidence
                    ) | ({"git": git_spec} if git_spec else {})

        written, _ = evaluate_workspace_writes([t["path"] for t in targets], evidence)
        if settings.DELIVERY_TESTS_ENABLED and written == "pass":
            evidence["acceptance"] = await asyncio.to_thread(
                acceptance.evidence_for, engine, item["id"]
            )
            await acceptance.guard_test_files(
                nats_client, engine, evidence, settings.DELIVERY_TEST_TIMEOUT_SECONDS
            )
            await _run_delivery_tests(nats_client, sprint, evidence, engine)

    recorded = await asyncio.to_thread(
        _record_task_run, item, agent, sprint, deliverable, targets, evidence
    )
    queue_failed = queued_job is not None and not await _publish_queued_delivery(
        nats_client, queued_job, evidence
    )
    if queue_failed and recorded:
        # The run was recorded as waiting for a job that does not exist.
        recorded = await asyncio.to_thread(_reverify_run, recorded[0], evidence)

    if targets:
        comment = _delivery_comment(targets, evidence, recorded)
        await asyncio.to_thread(
            _update_item_status,
            item["id"],
            "in_progress",
            comment_text=comment,
            comment_author="System",
            kind="verified",
            run_id=recorded[0] if recorded else None,
        )
        await publish_glassroom(
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
        _post_message,
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
            _triage_blocked_task,
            sprint["company_id"],
            sprint["id"],
            item["id"],
            text,
            message_id,
        )
    await asyncio.to_thread(
        _update_item_status,
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
                    _fetch_memory_context, item, agent, sprint["company_id"]
                ),
                asyncio.to_thread(
                    _load_recent_lessons, sprint["company_id"], sprint["id"]
                ),
                asyncio.to_thread(
                    _load_procedures_context, sprint["company_id"], agent.get("role")
                ),
                asyncio.to_thread(
                    _load_sprint_completed_tasks, sprint["id"], item["id"]
                ),
                spec_planning.task_requirements_context(
                    nats_client, engine, item["id"], _resolve_workspace_root
                ),
                console_assistants.collect_advice(
                    nats_client, item, agent, sprint, _resolve_workspace_root
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
                    _update_item_status,
                    item["id"],
                    "in_progress",
                    comment_text=assistant_usage_comment,
                    comment_author="System",
                    kind="advised",
                )

            # Architect consultation for design-sensitive or reworked Developer tasks.
            # Recorded as an ``ARCHITECT_CONSULTED:`` comment so the orchestration
            # health analytics can count it.
            architect_advice = ""
            if should_consult_architect(
                item, agent, item.get("comments"), enabled=ARCHITECT_CONSULT_ENABLED
            ) or (ARCHITECT_CONSULT_ENABLED and edit_plan.wanted(item, agent)):
                prior_feedback = [
                    str(c.get("text", ""))
                    for c in (item.get("comments") or [])
                    if isinstance(c, dict)
                    and classify_review_result(str(c.get("text", ""))) == "NEEDS_WORK"
                ][-2:]
                await publish_glassroom(
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
                    # Recorded once the Architect has answered: a company with
                    # no Architect asked no one.
                    question = f'Before I implement "{item["title"]}", what design should I follow?'
                    if prior_feedback:
                        question += " The last review sent it back: " + " / ".join(
                            str(text)[:300] for text in prior_feedback[:3]
                        )
                    await asyncio.to_thread(
                        _post_message,
                        company_id=sprint["company_id"],
                        sprint_id=sprint["id"],
                        item_id=item["id"],
                        from_name=agent.get("name") or agent["role"],
                        from_role=agent["role"],
                        to_role=ARCHITECT_COMMENT_AUTHOR,
                        kind="question",
                        body=question,
                    )
                    await asyncio.to_thread(
                        _post_message,
                        company_id=sprint["company_id"],
                        sprint_id=sprint["id"],
                        item_id=item["id"],
                        from_name=ARCHITECT_COMMENT_AUTHOR,
                        from_role=ARCHITECT_COMMENT_AUTHOR,
                        to_role=agent["role"],
                        kind="answer",
                        body=architect_advice,
                    )
                    await asyncio.to_thread(
                        _update_item_status,
                        item["id"],
                        "in_progress",
                        comment_text=format_architect_consult_comment(architect_advice),
                        comment_author=ARCHITECT_COMMENT_AUTHOR,
                        kind="advised",
                    )
                    await publish_glassroom(
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
            deliverable = await console_producer.produce(
                nats_client, item, agent, sprint, requirements_ctx, architect_advice
            ) or await asyncio.to_thread(
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
                lessons_ctx or None,
                procedures_ctx or None,
                requirements_ctx or None,
            )
            deliverable = _sanitize_deliverable_text(deliverable)

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

            review_payload = await _deliver_artifacts(
                nats_client, item, agent, sprint, deliverable
            )

            # 4. Save result and mark as in_review (a reviewable comment even if
            # artifact normalization produced an empty payload).
            review_text = (review_payload or deliverable or "").strip()
            if not review_text:
                review_text = "(no deliverable text produced)"
            await asyncio.to_thread(
                _update_item_status,
                item["id"],
                "in_review",
                comment_text=review_text,
                comment_author=agent.get("name") or agent["role"],
                kind="delivered",
                actor=agent["role"],
            )
            await asyncio.to_thread(
                _post_message,
                company_id=sprint["company_id"],
                sprint_id=sprint["id"],
                item_id=item["id"],
                from_name=agent.get("name") or agent["role"],
                from_role=agent["role"],
                to_role="QA",
                kind="handoff",
                body=_handoff_body(item["title"], deliverable),
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
                        _update_item_status,
                        data["backlog_item_id"],
                        "blocked",
                        comment_text=(
                            str(e)[:500]
                            if isinstance(e, LLMCallError)
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
        nats_client, asyncio.get_running_loop(), _resolve_workspace_root
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
        await _on_delivery_result(nats_client, data)

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

            record = task_projection.record_of(session, item, histories)
            if record.frozen():
                task_events.record(session, item, "blocked", "system", status="blocked")
                session.commit()
                logger.info("Skipping QA review for manually frozen item=%s", item.id)
                continue

            deliverable = record.deliverable()
            # Prevent infinite review loops when QA repeatedly fails.
            qa_review_failures = record.review_failures()
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
            gate = await asyncio.to_thread(_delivery_gate, sprint.company_id, item.id)
            if gate is not None and gate[0] == DELIVERY_QUEUED:
                # Files are written when the daemon connects; review then.
                continue
            if gate is not None:
                problem, observation = gate
                previous_gate_reworks = record.rework_requests_by(DELIVERY_GATE_AUTHOR)
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
                gate_message = _message(
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
                        _triage_blocked_task,
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
                        _delivery_risk, sprint.company_id, item.id
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

                previous_reworks = record.rework_requests_by(reviewer_dict["name"])
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
                    _message(
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
                        _message(
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
                    escalation = _message(
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
                        _triage_blocked_task,
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
                    await _finalize_sprint_if_ready(nats_client, sprint.id)
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
                    await _finalize_sprint_if_ready(nats_client, sprint.id)

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
        await _finalize_sprint_if_ready(nats_client, sprint_id)

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
        raise LLMCallError(failure, sprint_control.credit_failure(result))

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
