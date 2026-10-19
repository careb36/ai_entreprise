"""
Reading a task's board: what its comments say about where it stands.

The task worker, the inbox and the carry-over all need the same answers
from a task's comment thread: which comment is the deliverable to review,
which rework requests are still pending, whether a blocked task is waiting
for a person or will move again on its own. They live here, with no engine
and no I/O, so every caller reads the board the same way.
"""

import json
from typing import Any

from models import BacklogItem

from services.orchestration_metrics import is_architect_consult_comment

# Who signs the text checks a deliverable passes before anyone reviews it, and
# who signs the delivery evidence check; neither ever writes a deliverable.
QUALITY_GATE_AUTHOR = "Quality gate"
DELIVERY_GATE_AUTHOR = "Verifier"
GATE_AUTHORS_LOWER = {QUALITY_GATE_AUTHOR.lower(), DELIVERY_GATE_AUTHOR.lower()}
SCRUM_MASTER_ROLE = "ScrumMaster"


def comment_list(comments: Any) -> list[dict[str, Any]]:
    """A backlog item's comments as a list, whatever the column holds."""
    if isinstance(comments, str):
        try:
            comments = json.loads(comments)
        except Exception:  # noqa: BLE001 - Malformed legacy comments fall back to an empty list.
            comments = []
    if not isinstance(comments, list):
        return []
    return [c for c in comments if isinstance(c, dict)]


def has_manual_freeze_marker(comments: Any) -> bool:
    """Detect operator comments that intentionally freeze a task out of automation."""
    return any(
        "sleep-induced churn" in str(comment.get("text", "")).lower()
        or str(comment.get("text", "")).strip().lower().startswith("freeze after")
        for comment in comment_list(comments)
    )


def extract_latest_reviewable_comment(
    comments: Any, assignee_role: str | None = None
) -> str:
    """Return the latest non-system, non-QA deliverable comment for review."""
    assignee_normalized = (assignee_role or "").strip().lower()

    for comment in reversed(comment_list(comments)):
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
        if author in GATE_AUTHORS_LOWER or author == "user":
            # The gates write verdicts and a person on the board writes notes;
            # neither is a deliverable.
            continue
        if author == "qa" and assignee_normalized != "qa":
            continue
        if text.upper().startswith("APPROVED:"):
            continue
        if is_architect_consult_comment(comment):
            continue
        return text

    return ""


def pending_rework_feedback(comments: Any) -> list[str]:
    """The ``NEEDS WORK:`` requests made since the task's latest deliverable."""
    feedback: list[str] = []
    for comment in reversed(comment_list(comments)):
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


def is_explicit_block_reason(text: str) -> bool:
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


def can_resume_blocked_item_from_deliverable(
    comments: Any, assignee_role: str | None = None
) -> bool:
    """Detect blocked items that already have a deliverable and no explicit block reason."""
    comments = comment_list(comments)
    if not comments:
        return False
    if has_manual_freeze_marker(comments):
        return False
    if any(is_explicit_block_reason(str(c.get("text", ""))) for c in comments):
        return False
    return bool(
        extract_latest_reviewable_comment(comments, assignee_role=assignee_role)
    )


def blocked_item_next_step(comments: Any, assignee_role: str | None) -> str | None:
    """What the worker still does on its own with a blocked item.

    ``"frozen"``: an operator froze it, so only a person moves it. ``"review"``:
    a deliverable exists and nothing says it must stay blocked, so it goes back
    to review. ``"retry"``: a transient failure not retried yet. ``None``: it
    stays blocked until a person acts, which is the only case a sprint may
    close around (see ``item_is_settled``).
    """
    comments = comment_list(comments)
    if has_manual_freeze_marker(comments):
        return "frozen"
    if can_resume_blocked_item_from_deliverable(comments, assignee_role=assignee_role):
        return "review"

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
        or str(c.get("text", "")).upper().startswith("NEEDS WORK:")
        for c in comments
    )
    if qa_quality_gate_block:
        return None

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
        return None

    # Only retry once — check if there's already a retry comment
    retry_count = sum(
        1
        for c in comments
        if c.get("author") == "System" and "retry" in str(c.get("text", "")).lower()
    )
    return None if retry_count >= 1 else "retry"


def block_reason(comments: Any) -> str:
    """The latest verdict on a blocked item: what a gate, the system or a reviewer said."""
    for comment in reversed(comment_list(comments)):
        text = str(comment.get("text", "")).strip()
        author = str(comment.get("author", "")).strip().lower()
        if text and (
            author == "system"
            or author in GATE_AUTHORS_LOWER
            or text.upper().startswith("NEEDS WORK:")
        ):
            return text
    return ""


def item_is_settled(item: BacklogItem) -> bool:
    """Done, or blocked until a person acts: nothing the worker does moves it."""
    if item.status == "done":
        return True
    if item.status != "blocked":
        return False
    return blocked_item_next_step(item.comments, item.assignee) is None
