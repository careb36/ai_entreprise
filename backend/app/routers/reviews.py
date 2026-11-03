"""Reviewing a run like a pull request: line comments and submitted reviews.

Viewers of the run's company read them; its Managers write them, since a
review can send a task back to work or accept a delivery
(``services/run_review.py``).
"""

from datetime import UTC, datetime

from auth_policy import current_user_if_required, require_company_role
from database import get_session
from fastapi import APIRouter, Depends, HTTPException, Query
from models import User
from models.agent_run import AgentRun
from models.review import ReviewComment, RunReview
from schemas import ReviewCommentCreate, RunReviewCreate
from services.run_review import ReviewRefused, check_comment_target, submit_review
from sqlmodel import Session, select

router = APIRouter(prefix="/v1/agent_runs", tags=["Reviews"])


def _run(session: Session, run_id: int, company_id: int) -> AgentRun:
    run = session.exec(
        select(AgentRun).where(AgentRun.id == run_id, AgentRun.company_id == company_id)
    ).first()
    if run is None:
        raise HTTPException(status_code=404, detail="Agent run not found")
    return run


def _author(user: User | None, given: str | None) -> tuple[str, int | None]:
    if user is not None:
        return (user.full_name or user.email), user.id
    return ((given or "").strip() or "A person"), None


@router.get("/{run_id}/comments")
def list_review_comments(
    run_id: int,
    company_id: int = Query(...),
    session: Session = Depends(get_session),  # noqa: B008 - FastAPI dependency marker.
    user: User | None = Depends(current_user_if_required),  # noqa: B008 - FastAPI dependency marker.
):
    require_company_role(session, user, company_id, "Viewer")
    run = _run(session, run_id, company_id)
    comments = session.exec(
        select(ReviewComment)
        .where(
            ReviewComment.agent_run_id == run.id, ReviewComment.company_id == company_id
        )
        .order_by(ReviewComment.created_at, ReviewComment.id)
    ).all()
    return {"comments": comments}


@router.post("/{run_id}/comments", status_code=201)
def add_review_comment(
    run_id: int,
    payload: ReviewCommentCreate,
    session: Session = Depends(get_session),  # noqa: B008 - FastAPI dependency marker.
    user: User | None = Depends(current_user_if_required),  # noqa: B008 - FastAPI dependency marker.
):
    require_company_role(session, user, payload.company_id, "Manager")
    run = _run(session, run_id, payload.company_id)
    try:
        check_comment_target(session, run, payload.path)
    except ReviewRefused as refused:
        raise HTTPException(
            status_code=refused.status, detail=refused.detail
        ) from refused
    author_name, author_id = _author(user, payload.author_name)
    comment = ReviewComment(
        company_id=run.company_id,
        agent_run_id=run.id,
        path=payload.path,
        line=payload.line,
        side=payload.side,
        body=payload.body,
        author_name=author_name,
        author_user_id=author_id,
    )
    session.add(comment)
    session.commit()
    session.refresh(comment)
    return comment


@router.post("/{run_id}/comments/{comment_id}/resolve")
def resolve_review_comment(
    run_id: int,
    comment_id: int,
    company_id: int = Query(...),
    session: Session = Depends(get_session),  # noqa: B008 - FastAPI dependency marker.
    user: User | None = Depends(current_user_if_required),  # noqa: B008 - FastAPI dependency marker.
):
    """Mark a comment resolved: a review submitted later leaves it out."""
    require_company_role(session, user, company_id, "Manager")
    run = _run(session, run_id, company_id)
    comment = session.get(ReviewComment, comment_id)
    if (
        comment is None
        or comment.agent_run_id != run.id
        or comment.company_id != company_id
    ):
        raise HTTPException(status_code=404, detail="Review comment not found")
    if comment.resolved_at is None:
        comment.resolved_at = datetime.now(UTC).replace(tzinfo=None)
        session.add(comment)
        session.commit()
        session.refresh(comment)
    return comment


@router.get("/{run_id}/reviews")
def list_run_reviews(
    run_id: int,
    company_id: int = Query(...),
    session: Session = Depends(get_session),  # noqa: B008 - FastAPI dependency marker.
    user: User | None = Depends(current_user_if_required),  # noqa: B008 - FastAPI dependency marker.
):
    require_company_role(session, user, company_id, "Viewer")
    run = _run(session, run_id, company_id)
    reviews = session.exec(
        select(RunReview)
        .where(RunReview.agent_run_id == run.id, RunReview.company_id == company_id)
        .order_by(RunReview.created_at, RunReview.id)
    ).all()
    return {"reviews": reviews}


@router.post("/{run_id}/reviews", status_code=201)
def create_run_review(
    run_id: int,
    payload: RunReviewCreate,
    session: Session = Depends(get_session),  # noqa: B008 - FastAPI dependency marker.
    user: User | None = Depends(current_user_if_required),  # noqa: B008 - FastAPI dependency marker.
):
    """Submit a review: ``comment``, ``approve`` or ``request_changes`` (see ``services/run_review``)."""
    require_company_role(session, user, payload.company_id, "Manager")
    run = _run(session, run_id, payload.company_id)
    author_name, author_id = _author(user, payload.author_name)
    try:
        return submit_review(
            session,
            run,
            event=payload.event,
            body=payload.body,
            author_name=author_name,
            author_user_id=author_id,
        )
    except ReviewRefused as refused:
        session.rollback()
        raise HTTPException(
            status_code=refused.status, detail=refused.detail
        ) from refused
