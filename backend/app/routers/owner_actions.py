"""Owner actions from the app (issue #701, ``docs/design/owner-actions.md``).

- ``GET  /v1/owner_actions?company_id=``                     the catalogue and each action's last runs
- ``POST /v1/owner_actions/{name}/preview``                  the confirmation: effect, cost, digest; writes nothing
- ``POST /v1/owner_actions/{name}/runs``                     start a run (digest echoed); 409 while one runs
- ``GET  /v1/owner_actions/{name}/runs/{run_id}?company_id=`` one run, with its observations
- ``POST /v1/owner_actions/{name}/runs/{run_id}/cancel``     ask it to stop

Every route needs a signed-in person who is Owner of the company the request
names: a Manager gets 403, and so does an API token whatever its role (as the
Owner-only settings), and so does an owner action's own in-process call (an
action never starts another). Without ``AUTH_REQUIRED`` there is still no
anonymous run. Start, cancel and finish are audited in the company's chain
(``services/management/owner_actions.py``).
"""

from __future__ import annotations

from typing import Any

from auth_policy import current_user_if_required, require_company_role
from database import get_session
from fastapi import APIRouter, Depends, HTTPException, Query, Request
from models import OwnerActionRun, User
from pydantic import Field
from schemas import RequestModel
from services.management import owner_action_runner as runner
from services.management import owner_actions as catalogue
from services.management import self_host_snapshot
from services.workspace import daemon_liveness
from sqlmodel import Session

router = APIRouter(prefix="/v1", tags=["Company"])


class PreviewRequest(RequestModel):
    company_id: int
    params: dict[str, Any] = Field(default_factory=dict)


class StartRequest(PreviewRequest):
    confirmation: str = ""


class CancelRequest(RequestModel):
    company_id: int


def require_human_owner(session: Session, user: User | None, company_id: int) -> User:
    """A signed-in person who is Owner of *company_id*; never a token (O1)."""
    if user is None:
        raise HTTPException(
            status_code=401,
            detail="Not authenticated",
            headers={"WWW-Authenticate": "Bearer"},
        )
    if getattr(user, "_api_token_company_id", None) is not None:
        raise HTTPException(
            status_code=403,
            detail="An owner action needs a signed-in Owner, not an API token",
        )
    if getattr(user, "_owner_action_run_id", None) is not None:
        raise HTTPException(
            status_code=403, detail="An owner action cannot start another"
        )
    require_company_role(session, user, company_id, "Owner")
    return user


def _refused(exc: catalogue.RunRefused) -> HTTPException:
    return HTTPException(status_code=exc.status, detail=exc.detail)


def _availability(name: str) -> dict[str, Any]:
    if name == catalogue.SELF_HOST:
        if self_host_snapshot.harness_dir() is None:
            return {"available": False, "reason": "harness_not_found"}
        if daemon_liveness.announces(self_host_snapshot.CHECKOUT) is False:
            return {"available": False, "reason": "daemon_lacks_checkout"}
    return {"available": True, "reason": None}


def _run_of(
    session: Session, name: str, run_id: int, company_id: int
) -> OwnerActionRun:
    run = session.get(OwnerActionRun, run_id)
    if run is None or run.company_id != company_id or run.action != name:
        raise HTTPException(status_code=404, detail="No such run")
    return run


@router.get("/owner_actions")
def list_owner_actions(
    company_id: int = Query(...),
    session: Session = Depends(get_session),  # noqa: B008 - FastAPI dependency marker.
    user: User | None = Depends(current_user_if_required),  # noqa: B008 - FastAPI dependency marker.
):
    """The catalogue: each action's parameters, records, availability and last runs."""
    require_human_owner(session, user, company_id)
    actions = []
    for spec in catalogue.CATALOGUE.values():
        busy = catalogue.running(session, (spec.name,))
        actions.append(
            {
                **catalogue.describe(spec),
                **_availability(spec.name),
                "running_run_id": busy.id if busy is not None else None,
                "last_runs": [
                    catalogue.serialize(r)
                    for r in catalogue.last_runs(session, company_id, spec.name)
                ],
            }
        )
    return {"actions": actions}


@router.post("/owner_actions/{name}/preview")
def preview_owner_action(
    name: str,
    payload: PreviewRequest,
    session: Session = Depends(get_session),  # noqa: B008 - FastAPI dependency marker.
    user: User | None = Depends(current_user_if_required),  # noqa: B008 - FastAPI dependency marker.
):
    """What a run would do and cost, and the digest its start must echo."""
    require_human_owner(session, user, payload.company_id)
    try:
        spec = catalogue.action(name)
        return catalogue.preview(spec, catalogue.validate(spec, payload.params))
    except catalogue.RunRefused as exc:
        raise _refused(exc) from exc


@router.post("/owner_actions/{name}/runs", status_code=202)
async def start_owner_action(
    name: str,
    payload: StartRequest,
    request: Request,
    session: Session = Depends(get_session),  # noqa: B008 - FastAPI dependency marker.
    user: User | None = Depends(current_user_if_required),  # noqa: B008 - FastAPI dependency marker.
):
    """Start a run (Owner; audited ``owner_action_started``)."""
    owner = require_human_owner(session, user, payload.company_id)
    try:
        spec = catalogue.action(name)
        params = catalogue.validate(spec, payload.params)
        available = _availability(spec.name)
        if not available["available"]:
            raise catalogue.RunRefused(503, available)
        run = catalogue.start(
            session,
            payload.company_id,
            int(owner.id or 0),
            spec,
            params,
            payload.confirmation,
        )
    except catalogue.RunRefused as exc:
        raise _refused(exc) from exc
    try:
        runner.launch(
            request.app,
            getattr(request.app.state, "nats_client", None),
            run,
            session.get_bind(),
        )
    except Exception as exc:
        catalogue.finish(
            session, int(run.id or 0), "failed", None, f"not_started: {exc}", {}
        )
        raise HTTPException(
            status_code=503, detail=f"The run could not start: {exc}"
        ) from exc
    return catalogue.serialize(run)


@router.get("/owner_actions/{name}/runs/{run_id}")
def get_owner_action_run(
    name: str,
    run_id: int,
    company_id: int = Query(...),
    session: Session = Depends(get_session),  # noqa: B008 - FastAPI dependency marker.
    user: User | None = Depends(current_user_if_required),  # noqa: B008 - FastAPI dependency marker.
):
    """A run: status, times, parameters and what the platform observed."""
    require_human_owner(session, user, company_id)
    run = _run_of(session, name, run_id, company_id)
    session.refresh(run)
    return catalogue.serialize(run)


@router.post("/owner_actions/{name}/runs/{run_id}/cancel")
def cancel_owner_action_run(
    name: str,
    run_id: int,
    payload: CancelRequest,
    session: Session = Depends(get_session),  # noqa: B008 - FastAPI dependency marker.
    user: User | None = Depends(current_user_if_required),  # noqa: B008 - FastAPI dependency marker.
):
    """Ask a run to stop (Owner; audited ``owner_action_cancel_requested``)."""
    owner = require_human_owner(session, user, payload.company_id)
    run = _run_of(session, name, run_id, payload.company_id)
    try:
        run = catalogue.request_cancel(session, run, int(owner.id or 0))
    except catalogue.RunRefused as exc:
        raise _refused(exc) from exc
    return catalogue.serialize(run)
