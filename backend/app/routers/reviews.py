"""Reviewing a run like a pull request: line comments, submitted reviews and publishing.

Viewers of the run's company read them; its Managers write them, since a
review can send a task back to work or accept a delivery
(``services/loop/run_review.py``). A Manager also publishes a run's task branch
as a pull or merge request on the repository's platform
(``services/git_publish.py``), or, without the Git workflow, undoes its
delivery from the daemon's snapshot (``services/delivery_undo.py``).
"""

from datetime import UTC, datetime

from auth_policy import current_user_if_required, require_company_role
from database import get_session
from fastapi import APIRouter, Depends, HTTPException, Query, Request
from models import User
from models.agent_run import AgentRun
from models.review import ReviewComment, RunPublication, RunReview
from schemas import ReviewCommentCreate, RunReviewCreate
from services.delivery_undo import UndoRefused, record_undo, undo_spec
from services.git_publish import PublishRefused, publish_spec, record_publication
from services.local_merge import record_run_merge, run_merge_spec
from services.review_sync import SyncRefused, import_comments, sync_spec
from services.run_review import ReviewRefused, check_comment_target, submit_review
from services.workspace_daemon import (
    DAEMON_UNAVAILABLE_DETAIL,
    DaemonError,
    DaemonUnavailable,
    daemon_request,
)
from sqlmodel import Session, select

router = APIRouter(prefix="/v1/agent_runs", tags=["Reviews"])

# A push and the platform's CLI run on the person's machine; give them time.
PUBLISH_TIMEOUT_SECONDS = 150.0
# Restoring a few files from the daemon's own store.
UNDO_TIMEOUT_SECONDS = 60.0
# Three paginated API reads (line comments, reviews, conversation) on GitHub.
SYNC_TIMEOUT_SECONDS = 200.0


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


@router.get("/{run_id}/publications")
def list_run_publications(
    run_id: int,
    company_id: int = Query(...),
    session: Session = Depends(get_session),  # noqa: B008 - FastAPI dependency marker.
    user: User | None = Depends(current_user_if_required),  # noqa: B008 - FastAPI dependency marker.
):
    """Where the run's branch was published: the pull or merge request, newest first."""
    require_company_role(session, user, company_id, "Viewer")
    run = _run(session, run_id, company_id)
    publications = session.exec(
        select(RunPublication)
        .where(
            RunPublication.agent_run_id == run.id,
            RunPublication.company_id == company_id,
        )
        .order_by(RunPublication.created_at.desc(), RunPublication.id.desc())
    ).all()
    return {"publications": publications}


@router.post("/{run_id}/publish", status_code=201)
async def publish_run(
    run_id: int,
    request: Request,
    company_id: int = Query(...),
    session: Session = Depends(get_session),  # noqa: B008 - FastAPI dependency marker.
    user: User | None = Depends(current_user_if_required),  # noqa: B008 - FastAPI dependency marker.
):
    """Push the run's task branch and open its pull or merge request (``services/git_publish``)."""
    require_company_role(session, user, company_id, "Manager")
    run = _run(session, run_id, company_id)
    author_name, author_id = _author(user, None)
    try:
        spec = publish_spec(session, run)
        reply = await daemon_request(
            getattr(request.app.state, "nats_client", None),
            "workspace.git.publish",
            spec,
            timeout=PUBLISH_TIMEOUT_SECONDS,
        )
        return record_publication(
            session,
            run,
            reply,
            published_by=author_name,
            published_by_user_id=author_id,
        )
    except PublishRefused as refused:
        session.rollback()
        raise HTTPException(
            status_code=refused.status, detail=refused.detail
        ) from refused
    except DaemonUnavailable as exc:
        raise HTTPException(status_code=503, detail=DAEMON_UNAVAILABLE_DETAIL) from exc
    except DaemonError as exc:
        raise HTTPException(status_code=exc.status, detail=exc.message) from exc


@router.post("/{run_id}/merge", status_code=201)
async def merge_run(
    run_id: int,
    request: Request,
    company_id: int = Query(...),
    session: Session = Depends(get_session),  # noqa: B008 - FastAPI dependency marker.
    user: User | None = Depends(current_user_if_required),  # noqa: B008 - FastAPI dependency marker.
):
    """Merge the run's task branch into the base branch, locally (``services/local_merge``)."""
    require_company_role(session, user, company_id, "Manager")
    run = _run(session, run_id, company_id)
    author_name, author_id = _author(user, None)
    try:
        reply = await daemon_request(
            getattr(request.app.state, "nats_client", None),
            "workspace.git.merge",
            run_merge_spec(session, run),
            timeout=PUBLISH_TIMEOUT_SECONDS,
        )
        return record_run_merge(
            session, run, reply, merged_by=author_name, merged_by_user_id=author_id
        )
    except PublishRefused as refused:
        session.rollback()
        raise HTTPException(
            status_code=refused.status, detail=refused.detail
        ) from refused
    except DaemonUnavailable as exc:
        raise HTTPException(status_code=503, detail=DAEMON_UNAVAILABLE_DETAIL) from exc
    except DaemonError as exc:
        raise HTTPException(status_code=exc.status, detail=exc.message) from exc


@router.post("/{run_id}/undo")
async def undo_run_delivery(
    run_id: int,
    request: Request,
    company_id: int = Query(...),
    session: Session = Depends(get_session),  # noqa: B008 - FastAPI dependency marker.
    user: User | None = Depends(current_user_if_required),  # noqa: B008 - FastAPI dependency marker.
):
    """Put back the files the run's delivery changed (``services/delivery_undo``); the task's status stays."""
    require_company_role(session, user, company_id, "Manager")
    run = _run(session, run_id, company_id)
    author_name, _ = _author(user, None)
    try:
        spec = undo_spec(session, run)
        reply = await daemon_request(
            getattr(request.app.state, "nats_client", None),
            "workspace.undo",
            spec,
            timeout=UNDO_TIMEOUT_SECONDS,
        )
        return record_undo(session, run, reply, undone_by=author_name)
    except UndoRefused as refused:
        session.rollback()
        raise HTTPException(
            status_code=refused.status, detail=refused.detail
        ) from refused
    except DaemonUnavailable as exc:
        raise HTTPException(status_code=503, detail=DAEMON_UNAVAILABLE_DETAIL) from exc
    except DaemonError as exc:
        raise HTTPException(status_code=exc.status, detail=exc.message) from exc


@router.post("/{run_id}/publications/{publication_id}/sync")
async def sync_publication_comments(
    run_id: int,
    publication_id: int,
    request: Request,
    company_id: int = Query(...),
    session: Session = Depends(get_session),  # noqa: B008 - FastAPI dependency marker.
    user: User | None = Depends(current_user_if_required),  # noqa: B008 - FastAPI dependency marker.
):
    """Bring the comments of the run's pull or merge request here (``services/review_sync``)."""
    require_company_role(session, user, company_id, "Manager")
    run = _run(session, run_id, company_id)
    publication = session.get(RunPublication, publication_id)
    if (
        publication is None
        or publication.agent_run_id != run.id
        or publication.company_id != company_id
    ):
        raise HTTPException(status_code=404, detail="Publication not found")
    try:
        spec = sync_spec(session, run, publication)
        reply = await daemon_request(
            getattr(request.app.state, "nats_client", None),
            "workspace.git.reviews",
            spec,
            timeout=SYNC_TIMEOUT_SECONDS,
        )
        return import_comments(session, run, publication, reply)
    except SyncRefused as refused:
        session.rollback()
        raise HTTPException(
            status_code=refused.status, detail=refused.detail
        ) from refused
    except DaemonUnavailable as exc:
        raise HTTPException(status_code=503, detail=DAEMON_UNAVAILABLE_DETAIL) from exc
    except DaemonError as exc:
        raise HTTPException(status_code=exc.status, detail=exc.message) from exc
