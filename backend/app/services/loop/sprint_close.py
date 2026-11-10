"""What the worker says when a sprint closes, and whether it closed before.

A sprint a person reopened (``services/loop/board_moves.py``: a card moved back to
To do in a ``completed`` sprint) closes again once its tasks settle. What a
sprint does once happened at its first close: the follow-up sprint at
autonomy 3, the retrospective, the procedures measured and proposed and the
memory engram. ``RECLOSED`` is the close's answer for a second close, and the
worker skips them for it.
"""

from __future__ import annotations

from typing import Any

from sqlmodel import Session

from services.loop import board_moves

RECLOSED = "reclosed_after_reopen"

_REASONS = {
    "autonomy_below_threshold": "Autonomous follow-up skipped: company autonomy level is below 3.",
    "auto_self_improvement_disabled": "Autonomous follow-up skipped: auto self-improvement is disabled.",
    "planner_returned_no_tasks": "Autonomous follow-up skipped: planner did not produce a usable backlog.",
    "company_missing": "Autonomous follow-up skipped: company context was missing.",
    "completed_no_follow_up": "Sprint completed with no autonomous follow-up trigger.",
    RECLOSED: (
        "Sprint closed again after a person reopened it; its retrospective, "
        "procedures and follow-up were done at its first close."
    ),
}


def reclosed(session: Session, sprint_id: int) -> bool:
    """Whether this close is a second one, after a person reopened the sprint."""
    return board_moves.closed_before(session, sprint_id)


def describe_follow_up_reason(result: dict[str, Any]) -> str:
    """Render a human-readable reason for autonomous follow-up decisions."""
    reason = result.get("reason")
    if reason == "auto_follow_up_opened":
        return f"Autonomous follow-up opened: {result.get('next_sprint_name', 'Self-improvement sprint')}"
    if reason == "active_sprint_already_exists":
        return f"Autonomous follow-up skipped: sprint {result.get('blocking_sprint_id')} is still active or needs split."
    return _REASONS.get(str(reason), "Autonomous follow-up evaluation completed.")
