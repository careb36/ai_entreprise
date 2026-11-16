"""A person's review of a sprint's integration, as on a run (``services/release/integration_review.py``).

Viewers of the sprint's company read the reviews, the comments and the diff;
its Managers write them, as on a run (``routers/reviews.py``). The reviews and
comments are on the sprint's latest integration, the one a merge would take;
a request that names an older ``integration_id`` is refused, since what the
person read is no longer what would be merged. The diff always comes from the
workspace daemon (``workspace.git.diff``), so a comment or a review needs it.

The company's choice to merge an integration only once approved is
``GET``/``PUT /v1/companies/{id}/integration_review`` (Viewer reads, Owner writes).
"""

from auth_policy import current_user_if_required, require_company_role
from database import get_session
from fastapi import APIRouter, Depends, HTTPException, Query, Request
from models import Sprint, User
from models.integration import SprintIntegration
from schemas import (
    IntegrationCommentCreate,
    IntegrationReviewCreate,
    IntegrationReviewSetting,
)
from services.platform import sod
from services.release import integration
from services.release import integration_review as reviews
from services.workspace.workspace_daemon import (
    DAEMON_UNAVAILABLE_DETAIL,
    DaemonError,
    DaemonUnavailable,
)
from sqlmodel import Session

from routers.sod import owner_person

router = APIRouter(prefix="/v1", tags=["Integration"])


def _sprint(session: Session, user: User | None, sprint_id: int, role: str) -> Sprint:
    sprint = session.get(Sprint, sprint_id)
    if sprint is None:
        raise HTTPException(status_code=404, detail="Sprint not found")
    require_company_role(session, user, sprint.company_id, role)
    return sprint


def _latest(
    session: Session, sprint: Sprint, integration_id: int | None = None
) -> SprintIntegration:
    row = integration.latest(session, sprint.id)
    if row is None or row.company_id != sprint.company_id:
        raise HTTPException(
            status_code=409, detail="This sprint has not been integrated"
        )
    if integration_id is not None and integration_id != row.id:
        raise HTTPException(
            status_code=409,
            detail="A newer integration of this sprint replaced the one you read: "
            "read its diff again",
        )
    return row


def _author(user: User | None, given: str | None) -> tuple[str, int | None]:
    if user is not None:
        return (user.full_name or user.email), user.id
    return ((given or "").strip() or "A person"), None


def _same_company(sprint: Sprint, company_id: int | None) -> None:
    if company_id is not None and company_id != sprint.company_id:
        raise HTTPException(status_code=404, detail="Sprint not found")


def _refused(exc: Exception) -> HTTPException:
    if isinstance(exc, reviews.IntegrationReviewRefused):
        return HTTPException(status_code=exc.status, detail=exc.detail)
    if isinstance(exc, DaemonUnavailable):
        return HTTPException(status_code=503, detail=DAEMON_UNAVAILABLE_DETAIL)
    return HTTPException(status_code=exc.status, detail=exc.message)


_FAILURES = (reviews.IntegrationReviewRefused, DaemonUnavailable, DaemonError)


@router.get("/sprints/{sprint_id}/integration/diff")
async def integration_diff(
    sprint_id: int,
    request: Request,
    session: Session = Depends(get_session),  # noqa: B008 - FastAPI dependency marker.
    user: User | None = Depends(current_user_if_required),  # noqa: B008 - FastAPI dependency marker.
):
    """What the latest integration brings into its base, as the daemon reads it."""
    sprint = _sprint(session, user, sprint_id, "Viewer")
    row = _latest(session, sprint)
    try:
        return await reviews.read_diff(
            getattr(request.app.state, "nats_client", None), session, row
        )
    except _FAILURES as exc:
        raise _refused(exc) from exc


@router.get("/sprints/{sprint_id}/integration/reviews")
def integration_reviews(
    sprint_id: int,
    integration_id: int | None = Query(default=None),
    session: Session = Depends(get_session),  # noqa: B008 - FastAPI dependency marker.
    user: User | None = Depends(current_user_if_required),  # noqa: B008 - FastAPI dependency marker.
):
    """The reviews and comments of the latest integration (or of *integration_id*), and its state."""
    sprint = _sprint(session, user, sprint_id, "Viewer")
    if integration_id is None:
        row = _latest(session, sprint)
    else:
        row = session.get(SprintIntegration, integration_id)
        if row is None or row.sprint_id != sprint.id:
            raise HTTPException(status_code=404, detail="Integration not found")
    return {
        **reviews.state(session, sprint.company_id, row),
        "reviews": reviews.reviews_of(session, row),
        "comments": reviews.comments_of(session, row),
    }


@router.post("/sprints/{sprint_id}/integration/comments", status_code=201)
async def add_integration_comment(
    sprint_id: int,
    payload: IntegrationCommentCreate,
    request: Request,
    session: Session = Depends(get_session),  # noqa: B008 - FastAPI dependency marker.
    user: User | None = Depends(current_user_if_required),  # noqa: B008 - FastAPI dependency marker.
):
    """A note on a line of the latest integration's diff, anchored to the daemon's diff."""
    sprint = _sprint(session, user, sprint_id, "Manager")
    _same_company(sprint, payload.company_id)
    row = _latest(session, sprint, payload.integration_id)
    author_name, author_id = _author(user, payload.author_name)
    try:
        diff = await reviews.read_diff(
            getattr(request.app.state, "nats_client", None), session, row
        )
        return reviews.add_comment(
            session,
            row,
            diff,
            path=payload.path,
            line=payload.line,
            side=payload.side,
            body=payload.body,
            author_name=author_name,
            author_user_id=author_id,
        )
    except _FAILURES as exc:
        session.rollback()
        raise _refused(exc) from exc


@router.post(
    "/sprints/{sprint_id}/integration/comments/{integration_comment_id}/resolve"
)
def resolve_integration_comment(
    sprint_id: int,
    integration_comment_id: int,
    session: Session = Depends(get_session),  # noqa: B008 - FastAPI dependency marker.
    user: User | None = Depends(current_user_if_required),  # noqa: B008 - FastAPI dependency marker.
):
    """Mark a comment resolved: a review submitted later leaves it out."""
    sprint = _sprint(session, user, sprint_id, "Manager")
    row = _latest(session, sprint)
    try:
        return reviews.resolve_comment(session, row, integration_comment_id)
    except reviews.IntegrationReviewRefused as exc:
        raise _refused(exc) from exc


@router.post("/sprints/{sprint_id}/integration/reviews", status_code=201)
async def create_integration_review(
    sprint_id: int,
    payload: IntegrationReviewCreate,
    request: Request,
    session: Session = Depends(get_session),  # noqa: B008 - FastAPI dependency marker.
    user: User | None = Depends(current_user_if_required),  # noqa: B008 - FastAPI dependency marker.
):
    """Submit a review of the latest integration: ``comment``, ``approve`` or ``request_changes``.

    An approval is held to segregation of duties: the person who started the
    sprint does not approve its integration (403, audited). Its
    ``protected_paths_ack`` (a company that hosts the factory) is recorded
    only for a signed-in Owner, never an API token.
    """
    sprint = _sprint(session, user, sprint_id, "Manager")
    _same_company(sprint, payload.company_id)
    if payload.protected_paths_ack:
        # A person's decision: a signed-in Owner, never an API token.
        owner_person(session, user, sprint.company_id)
    row = _latest(session, sprint, payload.integration_id)
    author_name, author_id = _author(user, payload.author_name)
    try:
        diff = await reviews.read_diff(
            getattr(request.app.state, "nats_client", None), session, row
        )
        return reviews.submit_review(
            session,
            row,
            diff,
            event=payload.event,
            body=payload.body,
            author_name=author_name,
            author_user_id=author_id,
            actor=sod.actor_of(session, user),
            protected_paths_ack=payload.protected_paths_ack,
        )
    except _FAILURES as exc:
        session.rollback()
        raise _refused(exc) from exc


@router.get("/companies/{company_id}/integration_review")
def integration_review_setting(
    company_id: int,
    session: Session = Depends(get_session),  # noqa: B008 - FastAPI dependency marker.
    user: User | None = Depends(current_user_if_required),  # noqa: B008 - FastAPI dependency marker.
):
    """Whether the company merges an integration only once a person approved it."""
    require_company_role(session, user, company_id, "Viewer")
    return {"company_id": company_id, "required": reviews.required(session, company_id)}


@router.put("/companies/{company_id}/integration_review")
def update_integration_review_setting(
    company_id: int,
    payload: IntegrationReviewSetting,
    session: Session = Depends(get_session),  # noqa: B008 - FastAPI dependency marker.
    user: User | None = Depends(current_user_if_required),  # noqa: B008 - FastAPI dependency marker.
):
    """Turn the merge gate on or off (Owner; audited)."""
    require_company_role(session, user, company_id, "Owner")
    by, user_id = _author(user, None)
    try:
        return reviews.set_required(
            session, company_id, payload.required, by=by, user_id=user_id
        )
    except reviews.IntegrationReviewRefused as exc:
        raise _refused(exc) from exc
