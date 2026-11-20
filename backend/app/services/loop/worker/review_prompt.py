"""What QA is asked about one delivery (``qa_review``).

The task, the delivery, the verdict markers, what blocked a previous
attempt, the specification's requirements, the standards and procedures for
the reviewer's role, and the company's language for the review's reasons
(``services.contracts.language``).

The delivery is the task branch when the Git workflow committed it: the
daemon's diff of every attempt against the base (``workspace.branch_view``),
an observation, followed by this attempt's message, which is the agent's
claim. A rework writes only what it touches, so the latest run alone hid the
earlier attempts' files and QA sent back work the branch held (sprint 3,
task 17; issue #766). Without the workflow, or when the daemon cannot
answer, QA reads the run's whole files (``review_digest.for_review``).
"""

from __future__ import annotations

from typing import Any

from models import BacklogItem, Company, Sprint
from models.agent_run import TASK_WORKER_PRODUCER, AgentRun, Verification
from sqlmodel import Session, select

from services.contracts import language
from services.evidence import standards
from services.learning import procedures
from services.loop import review_digest
from services.loop.answers import review_format_reminder as _review_format_reminder
from services.loop.carry_over import previous_attempt_context
from services.planning import spec_planning
from services.platform import forge_policy_setting
from services.workspace import branch_view

BRANCH_NOTE = (
    "\n\nThe task branch above is what the repository holds, read by the system "
    "from git: judge the work from it. The agent's message is its own account "
    "of the attempt; a file it claims and the branch does not hold is missing."
)


def build(
    session: Session,
    item: BacklogItem,
    sprint: Sprint,
    reviewer_dict: dict[str, Any],
    deliverable: str,
    qa_review_failures: int,
    branch: str | None = None,
) -> str:
    """What QA is asked about one delivery, with the task's context and procedures."""
    if branch:
        shown = (
            f"{branch}\n\n--- THIS ATTEMPT'S MESSAGE (the agent's claim) ---\n"
            f"{deliverable[: review_digest.DIGEST_CHARS]}"
        )
    elif review_digest.is_digest(deliverable):
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
            + (
                BRANCH_NOTE
                if branch
                else review_digest.NOTE
                if review_digest.is_digest(shown)
                else ""
            )
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


async def task_branch(
    session: Session, nats_client: Any, item: BacklogItem, sprint: Sprint
) -> str | None:
    """The task branch as the daemon reads it, or ``None`` without a committed delivery."""
    run = _latest_run(session, sprint.company_id, item.id)
    if run is None:
        return None
    verification = session.exec(
        select(Verification)
        .where(Verification.agent_run_id == run.id)
        .order_by(Verification.id.desc())
    ).first()
    git = (verification.evidence or {}).get("git") if verification else None
    spec = branch_view.request(
        git, forge_policy_setting.effective(session, sprint.company_id)
    )
    if spec is None:
        return None
    return await branch_view.read(
        nats_client, spec, str(git.get("branch") or ""), review_digest.REVIEW_CHARS
    )


def _latest_run(session: Session, company_id: int, item_id: int) -> AgentRun | None:
    return session.exec(
        select(AgentRun)
        .where(
            AgentRun.company_id == company_id,
            AgentRun.external_ref == f"task-{item_id}",
            AgentRun.producer == TASK_WORKER_PRODUCER,
        )
        .order_by(AgentRun.version.desc())
    ).first()


def _run_artifact(session: Session, company_id: int, item_id: int) -> str | None:
    """The company's latest task-worker run's artifact for the task: every file whole."""
    run = _latest_run(session, company_id, item_id)
    return run.artifact_text if run is not None else None
