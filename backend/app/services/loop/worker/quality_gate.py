"""The pre-review quality gate on a deliverable's text (ADR 0014, the task worker's review stage).

``_validate_minimum_deliverable`` looks at the text's form (length, sections,
fences, an abrupt end) before a delivery goes to QA; ``_send_back_from_quality_gate``
sends a rejected one back with the reason as ``Quality gate``, and blocks it for
a person and escalates it to the ScrumMaster once the rework budget it shares
with the delivery gate and QA (``qa_review.MAX_REWORKS``) is
spent. The intake calls both before a delivery reaches the daemon. Moved out of
``task_worker``, which still re-exports these names.
"""

import asyncio
import re
from typing import Any

from logging_config import setup_logger
from nats_client import NATSClient

from services.contracts.manifest import parse_file_manifest
from services.loop.worker import board_writes, messages, qa_review
from services.platform.glassroom import publish_glassroom
from services.tasks import task_projection
from services.tasks.board import QUALITY_GATE_AUTHOR, SCRUM_MASTER_ROLE
from services.tools.workspace_edits import delivered

logger = setup_logger("fabrique-task-worker")

MIN_DELIVERABLE_WORDS = 80
MIN_DOC_DELIVERABLE_WORDS = 180

# Words that make a task read as documentation, matched as whole words: as
# substrings "api" caught "notesapi" or "rapid", "process" "processor" (#508).
# "api" is not one of them: an API in a task is code far more often than prose.
DOC_KEYWORDS = re.compile(
    r"\b(?:document|documentation|readme|architecture|strategy|process|"
    r"pipeline|standards)\b"
)
# Files a documentation deliverable writes.
DOC_SUFFIXES = (".md", ".mdx", ".rst", ".txt", ".adoc")


def _reads_as_documentation(item: dict[str, Any], text: str) -> bool:
    """Whether the deliverable is held to the documentation rules (more words,
    two headings): a delivery whose files are all documents, or a task whose
    title or description names documentation by whole words (#508). A task
    that asks for a document stays one whatever files it delivers, so code
    in place of the document is still sent back."""
    paths = [entry["path"] for entry in parse_file_manifest(text)]
    if paths and all(path.lower().endswith(DOC_SUFFIXES) for path in paths):
        return True
    title_desc = f"{item.get('title', '')} {item.get('description', '')}".lower()
    return bool(DOC_KEYWORDS.search(title_desc))


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
    doc_like = _reads_as_documentation(item, text)
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
    sprint open forever. After ``qa_review.MAX_REWORKS``
    attempts the task is blocked for a person and escalated to the ScrumMaster.
    """
    previous_reworks = task_projection.record_from_dict(item).reworks()
    rework = previous_reworks < qa_review.MAX_REWORKS
    text = (
        f"NEEDS WORK: {reason}. {qa_review.EDIT_INSTRUCTION}"
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
            "message_key": "glassroom.events."
            + ("gate_rework" if rework else "gate_blocked"),
            "message_params": {"task": item["title"], "detail": reason},
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
