"""Where a sprint's integration stands, read from what the backend recorded (#555, F8).

A sprint's ``status`` turns ``completed`` when its tasks settle; its
integration (``integration_jobs``) starts from that close and runs after it.
So a reader of the status alone took a sprint for finished while its branches
were still being merged or its suite had failed (F8 of the self-hosting
retrospective). The state below is derived from the sprint's latest
``SprintIntegration``, which holds what git and the daemon answered (the merge
and the suite's exit), never what an agent reported; nothing new is stored.

- ``integrating``: the latest attempt is ``running`` or ``pending`` (a daemon
  that did not answer is tried again).
- ``integrated``: its branches merged and the suite passed, or ran no tests
  (``untested``; ``integration_status`` says which).
- ``failed``: a conflict, a failed suite, a done task's branch missing or a
  daemon's refusal: it waits for a person.
- ``none``: no integration was recorded (the Git workflow off, nothing done
  to integrate, or the sprint not closed yet).

The job is written just after the close, by the ``sprint.closed`` handler, so
for that moment a completed sprint still reads ``none``.
"""

from __future__ import annotations

from typing import Any

from models.integration import SprintIntegration
from sqlmodel import Session, select

INTEGRATING = ("running", "pending")
INTEGRATED = ("tests_passed", "untested")
FAILED = ("tests_failed", "conflict", "incomplete", "error")
STATES = ("none", "integrating", "integrated", "failed")


def state_of(status: str | None) -> str:
    """The integration state of a latest integration whose status is *status*."""
    if status in INTEGRATING:
        return "integrating"
    if status in INTEGRATED:
        return "integrated"
    if status in FAILED:
        return "failed"
    return "none"


def fields(status: str | None) -> dict[str, Any]:
    """The fields a sprint's answer adds: the derived state and the status it reads."""
    return {"integration_state": state_of(status), "integration_status": status}


def latest_statuses(session: Session, sprint_ids: list[int]) -> dict[int, str]:
    """Each sprint's latest integration status, for the sprints that have one."""
    if not sprint_ids:
        return {}
    rows = session.exec(
        select(SprintIntegration.sprint_id, SprintIntegration.status)
        .where(SprintIntegration.sprint_id.in_(sprint_ids))
        .order_by(SprintIntegration.created_at.desc(), SprintIntegration.id.desc())
    ).all()
    latest: dict[int, str] = {}
    for sprint_id, status in rows:
        latest.setdefault(sprint_id, status)
    return latest


def of_sprint(session: Session, sprint_id: int) -> dict[str, Any]:
    """The fields for one sprint."""
    return fields(latest_statuses(session, [sprint_id]).get(sprint_id))
