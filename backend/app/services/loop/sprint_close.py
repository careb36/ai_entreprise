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
# A sprint that settled with nothing done pauses instead of completing
# (owner, 2026-11-24: "que un sprint así se marque como bloqueado o se pause").
NOTHING_DONE = "paused_nothing_done"
# docs/design/rsi-loop.md, REQ-21: whatever AUTO_SELF_IMPROVEMENT_ENABLED says.
PROTECTED_PATHS_ON = "protected_paths_enabled"

_REASONS = {
    "autonomy_below_threshold": "Autonomous follow-up skipped: company autonomy level is below 3.",
    "auto_self_improvement_disabled": "Autonomous follow-up skipped: auto self-improvement is disabled.",
    PROTECTED_PATHS_ON: (
        "Autonomous follow-up skipped: this company hosts the factory "
        "(protected paths on), so no model-planned sprint runs for it."
    ),
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


def nothing_done_reason(statuses: list[str | None]) -> str | None:
    """Why a settled sprint pauses instead of completing, or ``None``.

    Settled means every task is done, discarded or blocked until a person
    acts. With no task done and at least one blocked, completing would read as
    finished work; the sprint pauses with this reason, and a person resumes it
    once the blocked tasks are unblocked, done or discarded.
    """
    if "done" in statuses or "blocked" not in statuses:
        return None
    blocked = statuses.count("blocked")
    return (
        f"No task of this sprint is done and {blocked} "
        f"{'waits' if blocked == 1 else 'wait'} for a person: unblock, finish "
        "or discard them, then resume the sprint."
    )
