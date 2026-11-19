"""What a task's prompt is built from, read from the company's record.

The task worker's second stage (ADR 0014): read-only prompt context, never a
decision. The output format for files, the company's past decisions from
memory, the lessons of its latest completed sprints (read from the board),
the done tasks of the same sprint, and the standards and procedures in force
for the agent's role. Each reader is best-effort where it was before: a
failure leaves its section out and never stops the task. Moved out of
``task_worker``, which still re-exports these names.
"""

import json
from typing import Any

from database import engine
from logging_config import setup_logger
from models import BacklogItem, Sprint
from sqlmodel import Session, select

from services.contracts import edit_blocks
from services.evidence import standards
from services.learning import procedures, retrospective
from services.models import edit_format as edit_formats
from services.tasks.board import DELIVERY_GATE_AUTHOR, completed_tasks_context

logger = setup_logger("fabrique-task-worker")

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

_WHOLE_FILES_ONLY = (
    "\n\n## Output format for files\n"
    "When your deliverable creates or changes files, write each file in full as its own "
    "fenced code block whose opening line carries the path relative to the project root, "
    "for example:\n\n"
    "```python path=src/example/module.py\n<entire file content>\n```\n\n"
    "Use one block per file, give the complete content (not a diff, an excerpt or "
    "SEARCH/REPLACE edits), and keep explanations outside the blocks. Files are written "
    "to the project exactly as given."
)
_SEARCH_REPLACE = (
    "\n\n## Output format for files\n"
    "When your deliverable changes an existing file, send only the changes: a fenced "
    "code block whose opening line carries the path relative to the project root and "
    "`edit`, holding one or more blocks of the form\n"
    "<<<<<<< SEARCH\n<exact lines from the current file>\n=======\n<their replacement>\n"
    ">>>>>>> REPLACE\n"
    "for example:\n\n"
    "```python path=src/example/module.py edit\n<<<<<<< SEARCH\nold line\n=======\n"
    "new line\n>>>>>>> REPLACE\n```\n\n"
    "Each SEARCH text must match the current file exactly once (include enough "
    "surrounding lines), and the edited file must still parse. Write a new file in "
    "full, as a block with its path and no `edit`. Keep explanations outside the blocks."
)


def file_instructions(edit_format: str | None) -> str:
    """The output format section for *edit_format* (``services/models/edit_format``):
    ``auto`` (and anything else) is the format every task had before #689."""
    by_format = {
        edit_formats.WHOLE: _WHOLE_FILES_ONLY,
        edit_formats.SEARCH_REPLACE: _SEARCH_REPLACE,
    }
    return by_format.get(str(edit_format or ""), FILE_MANIFEST_INSTRUCTIONS)


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
        from services.platform.engram_processor import get_engram_processor

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
        retrospective.summary(metrics)
        for metrics in (retrospective.build(engine, sid) for sid in sprint_ids)
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


def _load_procedures_context(company_id: int | None, role: str | None) -> str:
    """The factory's standards for *role*, then the company's active procedures, as prompt sections."""
    return standards.prompt_section(role) + procedures.prompt_context(
        engine, company_id, role
    )
