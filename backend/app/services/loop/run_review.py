"""Reviewing a run the way a pull request is reviewed: comments, then a decision.

A person leaves comments on the lines of the files a run changed, then
submits a review: ``comment``, ``approve`` or ``request_changes``. What the
decision does depends on who produced the run:

- a task the company's own agents delivered is decided by the review loop
  (QA, the Verifier), so a person's decision acts only where the loop waits
  for one: ``request_changes`` sends a task blocked for a person back to
  work, with the review and its comments as the ``NEEDS WORK:`` feedback the
  Developer's next prompt carries (``services.tasks.board.pending_rework_feedback``);
  ``approve`` accepts a delivery QA approved that waits for a person (the
  high-risk hold, or every approval at autonomy level 1);
- any other producer's run gets the approval record people already write
  (``AgentRunApproval``): approved, or rejected with the review as feedback.

A review is a person's words, never evidence: it is audited like an
approval, and the verdict on the run stays the Verifier's.
"""

from __future__ import annotations

from datetime import UTC, datetime

from models.agent_run import TASK_WORKER_PRODUCER, AgentRun, AgentRunApproval
from models.review import ReviewComment, RunReview
from models.sprint import BacklogItem, Sprint
from sqlmodel import Session, select

from services.evidence.review_changes import run_changes
from services.loop.carry_over import carried_over
from services.platform.agent_service import AgentService
from services.tasks import task_events, task_projection
from services.tasks.board import SEND_BACK_MARKER

CLOSED_SPRINTS = {"completed", "cancelled"}
ACCEPT_NOTE = "High-risk delivery accepted by a person"


class ReviewRefused(Exception):
    """A review that cannot act as asked: ``status`` is the HTTP status to answer."""

    def __init__(self, status: int, detail: str):
        super().__init__(detail)
        self.status = status
        self.detail = detail


def _now() -> datetime:
    return datetime.now(UTC).replace(tzinfo=None)


def task_of(session: Session, run: AgentRun) -> BacklogItem | None:
    """The board task an in-house task run delivered, in the run's own company."""
    if run.producer != TASK_WORKER_PRODUCER:
        return None
    item_id = (run.subject or {}).get("backlog_item_id")
    if not isinstance(item_id, int):
        return None
    item = session.get(BacklogItem, item_id)
    if item is None:
        return None
    sprint = session.get(Sprint, item.sprint_id)
    if sprint is None or sprint.company_id != run.company_id:
        return None
    return item


def record_approval(
    session: Session,
    run: AgentRun,
    *,
    approver_name: str,
    decision: str,
    feedback: str | None = None,
    rejection_category: str | None = None,
    actor_id: str | None = None,
) -> AgentRunApproval:
    """A person's decision on a run, audited (``POST /approve`` and reviews).

    *actor_id* names who decided in the audit row when it is not a person
    (the Manager agent under its policy); by default ``human:<approver>``.
    """
    approval = AgentRunApproval(
        company_id=run.company_id,
        agent_run_id=run.id,
        approver_name=approver_name,
        decision=decision,
        feedback=feedback,
        rejection_category=rejection_category,
    )
    session.add(approval)
    audit_log = AgentService().audit_interaction(
        session=session,
        company_id=run.company_id,
        interaction_id=f"agent-run:{run.external_ref}",
        agent_id=actor_id or f"human:{approver_name}",
        action=f"agent_run_{decision}",
        details={
            "company_id": str(run.company_id),
            "agent_run_id": run.id,
            "version": run.version,
            "rejection_category": rejection_category,
        },
        result={"decision": decision, "feedback": feedback},
        decision_signature=None,
        cost_tokens=0,
    )
    approval.audit_log_id = audit_log.id
    session.add(approval)
    return approval


def check_comment_target(session: Session, run: AgentRun, path: str | None) -> None:
    """A line comment names a file the run changed, as a pull request's do."""
    if path is None:
        return
    changed = {f["path"] for f in run_changes(session, run)["files"]}
    if path not in changed:
        raise ReviewRefused(422, f"{path} is not among the files this run changed")


def open_comments(session: Session, run: AgentRun) -> list[ReviewComment]:
    """The comments no review has carried yet, in the order they were written."""
    return list(
        session.exec(
            select(ReviewComment)
            .where(
                ReviewComment.agent_run_id == run.id,
                ReviewComment.company_id == run.company_id,
                ReviewComment.review_id.is_(None),
                ReviewComment.resolved_at.is_(None),
            )
            .order_by(ReviewComment.created_at, ReviewComment.id)
        ).all()
    )


def _where(comment: ReviewComment) -> str:
    if comment.path is None:
        return "the run"
    if comment.line is None:
        return comment.path
    side = "" if comment.side == "new" else " (old)"
    return f"{comment.path}:{comment.line}{side}"


def _said_on(comment: ReviewComment) -> str:
    """Who wrote a comment brought from the pull or merge request, and where."""
    if not comment.source:
        return ""
    platform = {"github": "GitHub", "gitlab": "GitLab"}.get(
        comment.source, comment.source
    )
    return f" ({comment.author_name} on {platform})"


def feedback_text(author: str, body: str | None, comments: list[ReviewComment]) -> str:
    """The review as one ``NEEDS WORK:`` request: what to change, file by line."""
    lines = [
        f"NEEDS WORK: review by {author}: {body or 'see the comments on the lines below.'}"
    ]
    lines += [f"- {_where(c)}{_said_on(c)}: {c.body}" for c in comments]
    return "\n".join(lines)


def _person_event(
    session: Session,
    item: BacklogItem,
    kind: str,
    text: str,
    review: RunReview,
    status: str | None = None,
) -> None:
    """A person's transition of the task, its event first, rendered as their note."""
    task_events.record(
        session,
        item,
        kind,
        task_events.PERSON,
        text=text,
        status=status,
        run_id=review.agent_run_id,
        payload={"review_id": review.id, "by": review.author_name},
        created_at=str(_now()),
        company_id=review.company_id,
    )


def _act_on_task(
    session: Session, item: BacklogItem, review: RunReview, feedback: str
) -> str:
    sprint = session.get(Sprint, item.sprint_id)
    if sprint is not None and sprint.status in CLOSED_SPRINTS:
        raise ReviewRefused(
            409,
            f"The task's sprint is {sprint.status}: carry the task into a new sprint "
            "to give it another attempt.",
        )
    if carried_over(item):
        raise ReviewRefused(
            409,
            "The task was carried into another sprint: review its new attempt there.",
        )
    if review.event == "approve":
        if item.status != "blocked" or not task_projection.awaits_risk_acceptance(
            session, item
        ):
            raise ReviewRefused(
                409,
                "QA decides this task. A person approves a delivery only when QA "
                "approved it and it waits for a person (a high-risk hold, or "
                "autonomy level 1).",
            )
        _person_event(
            session,
            item,
            "approved_by_person",
            f"{ACCEPT_NOTE}, in review #{review.id}.",
            review,
            status="done",
        )
        return "accepted"
    if not task_projection.waits_for_a_person(session, item):
        raise ReviewRefused(
            409,
            f"The task is {item.status} and still in the agents' hands; changes "
            "are requested when it waits for a person.",
        )
    _person_event(
        session,
        item,
        "sent_back",
        f"{SEND_BACK_MARKER} by a person, in review #{review.id}.",
        review,
        status="todo",
    )
    _person_event(session, item, "reviewed", feedback, review)
    return "sent_back"


def submit_review(
    session: Session,
    run: AgentRun,
    *,
    event: str,
    body: str | None,
    author_name: str,
    author_user_id: int | None,
) -> RunReview:
    """Record a review, carry its open comments, and do what its decision asks."""
    comments = open_comments(session, run)
    if event == "request_changes" and not body and not comments:
        raise ReviewRefused(
            422, "Say what to change: a comment on a line, or the review's text."
        )
    review = RunReview(
        company_id=run.company_id,
        agent_run_id=run.id,
        event=event,
        body=body,
        outcome="recorded",
        author_name=author_name,
        author_user_id=author_user_id,
    )
    session.add(review)
    session.flush()
    feedback = feedback_text(author_name, body, comments)
    if event != "comment":
        item = task_of(session, run)
        if item is not None:
            review.outcome = _act_on_task(session, item, review, feedback)
            session.add(item)
        else:
            record_approval(
                session,
                run,
                approver_name=author_name,
                decision="approved" if event == "approve" else "rejected",
                feedback=body if event == "approve" else feedback,
            )
            review.outcome = "approval_recorded"
    for comment in comments:
        comment.review_id = review.id
        session.add(comment)
    audit_log = AgentService().audit_interaction(
        session=session,
        company_id=run.company_id,
        interaction_id=f"agent-run:{run.external_ref}",
        agent_id=f"human:{author_name}",
        action=f"run_review_{event}",
        details={
            "company_id": str(run.company_id),
            "agent_run_id": run.id,
            "review_id": review.id,
        },
        result={"outcome": review.outcome, "comments": len(comments), "body": body},
        decision_signature=None,
        cost_tokens=0,
    )
    review.audit_log_id = audit_log.id
    session.add(review)
    session.commit()
    session.refresh(review)
    return review
