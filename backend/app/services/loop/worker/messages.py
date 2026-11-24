"""The conversation between roles: messages are claims; the evidence decides.

The task worker's first stage (ADR 0014): the ``AgentMessage`` rows a task's
hand-overs leave (question and answer, handoff, request, decision,
escalation, retrospective), and the ScrumMaster's triage of an escalation.
Moved out of ``task_worker``, which still re-exports these names.
"""

from typing import Any

from database import engine
from logging_config import setup_logger
from models import AgentMessage, BacklogItem
from sqlmodel import Session

from services.contracts.manifest import parse_file_manifest
from services.learning import scrum_master
from services.loop import replicas
from services.loop.worker import triage_reads
from services.tasks import task_events

logger = setup_logger("fabrique-task-worker")

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
            # The ScrumMaster may read the task's files first (triage_reads).
            decision = scrum_master.decide(
                triage_reads.answer(
                    model, system_prompt, prompt, company_id, item_id, sprint_id
                ),
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
