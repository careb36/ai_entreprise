"""What must hold before a sprint's work runs (issue #766).

A company whose deliveries cannot be verified (tests off) or whose agent
code runs without isolation (host sandbox) runs only once an Owner has
recorded that they know it (``services/platform/acknowledgements.py``).

Two places ask:

- every endpoint that starts work (``/start_sprint``, ``/start``,
  ``/resume``, ``/dispatch``, the Manager's resume) refuses with 409 and the
  missing acknowledgements, before changing anything (``unmet``);
- the task worker, before it claims a task (``hold_task``), so a task queued
  by any other path (a follow-up sprint, a task reopened on the board, a
  dependency that finished, an acknowledgement withdrawn or expired
  meanwhile) does not run either: the sprint is paused with the reason, as
  a sprint out of credit is, and resuming it asks again.
"""

from __future__ import annotations

from typing import Any

from config import settings
from models import BacklogItem, Company, Sprint
from sqlmodel import Session

from services.loop import sprint_control
from services.platform import acknowledgements
from services.tasks import task_events
from services.workspace import sandbox

PAUSE_PREFIX = "Missing acknowledgement:"


def required(company: Company | None) -> list[str]:
    """The acknowledgements *company*'s configuration needs."""
    return acknowledgements.required(
        bool(settings.DELIVERY_TESTS_ENABLED), sandbox.mode_for(company)
    )


def unmet(session: Session, company_id: int) -> list[dict[str, str]]:
    """Each acknowledgement the company needs and has not recorded."""
    company = session.get(Company, company_id)
    return acknowledgements.missing(session, company_id, required(company))


def refusal(missing: list[dict[str, str]]) -> dict[str, Any]:
    """The 409's detail: what is missing and how to record it."""
    kinds = ", ".join(m["kind"] for m in missing)
    return {
        "message": (
            f"{PAUSE_PREFIX} {kinds}. An Owner records it with "
            "POST /v1/companies/{id}/acknowledgements, or the deployment "
            "turns the tests on and isolates agent code."
        ),
        "missing_acknowledgements": missing,
    }


def pause_reason(missing: list[dict[str, str]]) -> str:
    return f"{PAUSE_PREFIX} {', '.join(m['kind'] for m in missing)}"


def hold_task(engine: Any, item_id: int) -> bool:
    """``True`` when the task must not run: its sprint is paused with the reason."""
    with Session(engine) as session:
        item = session.get(BacklogItem, item_id)
        sprint = session.get(Sprint, item.sprint_id) if item else None
        if item is None or sprint is None:
            return False
        missing = unmet(session, sprint.company_id)
        if not missing:
            return False
        reason = pause_reason(missing)
        if sprint_control.pause(session, sprint, reason):
            task_events.record(
                session,
                item,
                "paused",
                task_events.SYSTEM,
                status="todo",
                text=f"Paused: {reason}",
            )
        session.commit()
        return True
