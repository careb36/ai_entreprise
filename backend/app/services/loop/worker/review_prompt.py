"""What QA is asked about one delivery (``qa_review``).

The task, the delivery (the run's whole files when the board holds a digest,
``review_digest.for_review``), the verdict markers, what blocked a previous
attempt, the specification's requirements, the standards and procedures for
the reviewer's role, and the company's language for the review's reasons
(``services.contracts.language``).
"""

from __future__ import annotations

from typing import Any

from models import BacklogItem, Company, Sprint
from models.agent_run import TASK_WORKER_PRODUCER, AgentRun
from sqlmodel import Session, select

from services.contracts import language
from services.evidence import standards
from services.learning import procedures
from services.loop import review_digest
from services.loop.answers import review_format_reminder as _review_format_reminder
from services.loop.carry_over import previous_attempt_context
from services.planning import spec_planning


def build(
    session: Session,
    item: BacklogItem,
    sprint: Sprint,
    reviewer_dict: dict[str, Any],
    deliverable: str,
    qa_review_failures: int,
) -> str:
    """What QA is asked about one delivery, with the task's context and procedures."""
    if review_digest.is_digest(deliverable):
        # The run's whole files, within REVIEW_CHARS; the digest bounds itself.
        shown = review_digest.for_review(
            deliverable, _run_artifact(session, sprint.company_id, item.id)
        )
    else:
        shown = deliverable[: review_digest.DIGEST_CHARS]
    return (
        (
            f"You are reviewing the following deliverable for the task: '{item.title}'.\n"
            f"Description: {item.description or 'N/A'}\n\n"
            f"--- DELIVERABLE ---\n{shown}\n--- END ---"
            + (review_digest.NOTE if review_digest.is_digest(shown) else "")
            + "\n\nProvide a brief review (2-3 sentences). If acceptable, start with 'APPROVED:'. "
            "If not, start with 'NEEDS WORK:' and explain what's missing."
            + _review_format_reminder(qa_review_failures)
            # A second attempt is judged against what blocked the first and
            # the ScrumMaster's brief for it, the same context the agent had.
            + previous_attempt_context(item)
            + spec_planning.requirements_for(session, sprint, item)
            + standards.prompt_section(reviewer_dict["role"])
        )
        + procedures.procedures_prompt(
            procedures.active_procedures(
                session, sprint.company_id, reviewer_dict["role"]
            )
        )
        + _language_note(session, sprint.company_id)
    )


def _language_note(session: Session, company_id: int) -> str:
    """The review's reasons in the company's language; the verdict markers stay."""
    company = session.get(Company, company_id)
    return language.person_note(company.doc_language if company else None)


def _run_artifact(session: Session, company_id: int, item_id: int) -> str | None:
    """The company's latest task-worker run's artifact for the task: every file whole."""
    run = session.exec(
        select(AgentRun)
        .where(
            AgentRun.company_id == company_id,
            AgentRun.external_ref == f"task-{item_id}",
            AgentRun.producer == TASK_WORKER_PRODUCER,
        )
        .order_by(AgentRun.version.desc())
    ).first()
    return run.artifact_text if run is not None else None
