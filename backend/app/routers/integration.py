"""A sprint's integration: its task branches merged, the suite run, one request (issue #314).

``GET /v1/sprints/{id}/integration`` (Viewer) is the plan (which branches,
in which order) beside the latest integration and the earlier ones.
``POST /v1/sprints/{id}/integrate`` (Manager) integrates now; the task worker
does it on its own when a sprint closes (``services/integration.py``).
``POST /v1/sprints/{id}/integration/publish`` (Manager) pushes the
integration branch and opens one pull or merge request for the sprint
(``services/integration_publish.py``).
"""

from auth_policy import current_user_if_required, require_company_role
from database import get_session
from fastapi import APIRouter, Depends, HTTPException, Request
from models import Sprint, User
from models.integration import SprintIntegration
from services import integration
from services.git_publish import PublishRefused
from services.integration_publish import publish_spec, record_publication
from services.local_merge import integration_merge_spec, record_integration_merge
from services.workspace_daemon import (
    DAEMON_UNAVAILABLE_DETAIL,
    DaemonError,
    DaemonUnavailable,
    daemon_request,
)
from sqlmodel import Session, select

router = APIRouter(prefix="/v1", tags=["Integration"])

# A push and the platform's CLI run on the person's machine; give them time.
PUBLISH_TIMEOUT_SECONDS = 150.0
HISTORY_LIMIT = 20


def _sprint(session: Session, user: User | None, sprint_id: int, role: str) -> Sprint:
    sprint = session.get(Sprint, sprint_id)
    if sprint is None:
        raise HTTPException(status_code=404, detail="Sprint not found")
    require_company_role(session, user, sprint.company_id, role)
    return sprint


def _who(user: User | None) -> str:
    return (user.full_name or user.email) if user is not None else "A person"


@router.get("/sprints/{sprint_id}/integration")
async def sprint_integration(
    sprint_id: int,
    session: Session = Depends(get_session),  # noqa: B008 - FastAPI dependency marker.
    user: User | None = Depends(current_user_if_required),  # noqa: B008 - FastAPI dependency marker.
):
    """The plan, the latest integration and the earlier ones, newest first."""
    sprint = _sprint(session, user, sprint_id, "Viewer")
    history = session.exec(
        select(SprintIntegration)
        .where(
            SprintIntegration.sprint_id == sprint.id,
            SprintIntegration.company_id == sprint.company_id,
        )
        .order_by(SprintIntegration.created_at.desc(), SprintIntegration.id.desc())
        .limit(HISTORY_LIMIT)
    ).all()
    return {
        **integration.plan(session, sprint.id),
        "latest": history[0] if history else None,
        "history": [
            {
                "id": row.id,
                "status": row.status,
                "trigger": row.trigger,
                "commit": row.commit,
                "created_at": row.created_at,
            }
            for row in history
        ],
    }


def _refused(exc: Exception) -> HTTPException:
    if isinstance(exc, (integration.IntegrationRefused, PublishRefused)):
        return HTTPException(status_code=exc.status, detail=exc.detail)
    if isinstance(exc, DaemonUnavailable):
        return HTTPException(status_code=503, detail=DAEMON_UNAVAILABLE_DETAIL)
    return HTTPException(status_code=exc.status, detail=exc.message)


@router.post("/sprints/{sprint_id}/integrate", status_code=201)
async def integrate_sprint(
    sprint_id: int,
    request: Request,
    session: Session = Depends(get_session),  # noqa: B008 - FastAPI dependency marker.
    user: User | None = Depends(current_user_if_required),  # noqa: B008 - FastAPI dependency marker.
):
    """Merge the sprint's done task branches now and run the suite on the result."""
    sprint = _sprint(session, user, sprint_id, "Manager")
    try:
        return await integration.integrate(
            getattr(request.app.state, "nats_client", None),
            session.get_bind(),
            sprint.id,
            trigger="manual",
            requested_by=f"human:{_who(user)}",
        )
    except (integration.IntegrationRefused, DaemonUnavailable, DaemonError) as exc:
        raise _refused(exc) from exc


@router.post("/sprints/{sprint_id}/integration/publish", status_code=201)
async def publish_sprint_integration(
    sprint_id: int,
    request: Request,
    session: Session = Depends(get_session),  # noqa: B008 - FastAPI dependency marker.
    user: User | None = Depends(current_user_if_required),  # noqa: B008 - FastAPI dependency marker.
):
    """Push the latest integration branch and open the sprint's pull or merge request."""
    sprint = _sprint(session, user, sprint_id, "Manager")
    row = integration.latest(session, sprint.id)
    if row is None or row.company_id != sprint.company_id:
        raise HTTPException(
            status_code=409, detail="This sprint has not been integrated"
        )
    try:
        reply = await daemon_request(
            getattr(request.app.state, "nats_client", None),
            "workspace.git.publish",
            publish_spec(session, row),
            timeout=PUBLISH_TIMEOUT_SECONDS,
        )
        return record_publication(session, row, reply, published_by=_who(user))
    except (PublishRefused, DaemonUnavailable, DaemonError) as exc:
        session.rollback()
        raise _refused(exc) from exc


@router.post("/sprints/{sprint_id}/integration/merge", status_code=201)
async def merge_sprint_integration(
    sprint_id: int,
    request: Request,
    session: Session = Depends(get_session),  # noqa: B008 - FastAPI dependency marker.
    user: User | None = Depends(current_user_if_required),  # noqa: B008 - FastAPI dependency marker.
):
    """Merge the latest integration branch into the base branch, locally: nothing is pushed."""
    sprint = _sprint(session, user, sprint_id, "Manager")
    row = integration.latest(session, sprint.id)
    if row is None or row.company_id != sprint.company_id:
        raise HTTPException(
            status_code=409, detail="This sprint has not been integrated"
        )
    try:
        reply = await daemon_request(
            getattr(request.app.state, "nats_client", None),
            "workspace.git.merge",
            integration_merge_spec(session, row),
            timeout=PUBLISH_TIMEOUT_SECONDS,
        )
        return record_integration_merge(session, row, reply, merged_by=_who(user))
    except (PublishRefused, DaemonUnavailable, DaemonError) as exc:
        session.rollback()
        raise _refused(exc) from exc
