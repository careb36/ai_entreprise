"""Owner actions from the app (issue #701, ``docs/design/owner-actions.md``).

- ``GET  /v1/owner_actions?company_id=``                     the catalogue and each action's last runs
- ``POST /v1/owner_actions/{name}/preview``                  the confirmation: effect, cost, digest; writes nothing
- ``POST /v1/owner_actions/{name}/runs``                     start a run (digest echoed); 409 while one runs
- ``GET  /v1/owner_actions/{name}/runs/{run_id}?company_id=`` one run, with its observations
- ``POST /v1/owner_actions/{name}/runs/{run_id}/cancel``     ask it to stop
- ``GET  /v1/owner_actions/{name}/options?company_id=``      what the Owner picks from (branches, open issues)

``self_host_container_run`` observes the segregation-of-duties policy of
the company it will use (``owner_action_sod.py``): the digest covers it, and
a reused company that would refuse the harness's approvals is a blocker.
``land_branch``, ``push_main`` and ``close_issue`` observe before they show
(the daemon's view of the repository, GitHub's open issues): the preview's
digest covers those facts, and the start reads them again, so a branch or a
``main`` that moved, or another comment, is a 409. A protected land needs the
Owner's step-up in the start request (``step_up.py``); a land is refused
while a sprint is active.

Every route needs a signed-in person who is Owner of the company the request
names: a Manager gets 403, and so does an API token whatever its role (as the
Owner-only settings), and so does an owner action's own in-process call (an
action never starts another). Without ``AUTH_REQUIRED`` there is still no
anonymous run. Start, cancel and finish are audited in the company's chain
(``services/management/owner_actions.py``).
"""

from __future__ import annotations

from typing import Any

import step_up
from auth_policy import current_user_if_required, require_company_role
from database import get_session
from fastapi import APIRouter, Depends, HTTPException, Query, Request
from models import OwnerActionRun, User
from pydantic import Field
from schemas import RequestModel
from services.management import owner_action_runner as runner
from services.management import owner_actions as catalogue
from services.management import owner_git_actions as git
from services.management import owner_git_runner, self_host_snapshot
from services.producers import github_adapter
from services.workspace import daemon_liveness, workspace_daemon
from sqlmodel import Session

router = APIRouter(prefix="/v1", tags=["Company"])


class PreviewRequest(RequestModel):
    company_id: int
    params: dict[str, Any] = Field(default_factory=dict)


class StartRequest(PreviewRequest):
    confirmation: str = ""
    # A protected land's step-up (``step_up.py``): never stored, logged or audited.
    step_up_password: str | None = Field(default=None, max_length=1024)


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


def _github_token() -> str | None:
    return github_adapter.configured_token()


def _context(request: Request) -> git.Context:
    nats_client = getattr(request.app.state, "nats_client", None)

    async def daemon(subject: str, payload: dict[str, Any], timeout: float) -> Any:
        return await workspace_daemon.daemon_request(
            nats_client, subject, payload, timeout
        )

    return git.Context(daemon=daemon, github=github_adapter, token=_github_token())


def _availability(name: str) -> dict[str, Any]:
    if name in git.SUBJECTS:
        if any(daemon_liveness.announces(s) is False for s in git.SUBJECTS[name]):
            return {"available": False, "reason": "daemon_lacks_self_host_git"}
        if name == git.CLOSE and not _github_token():
            return {"available": False, "reason": "github_token_missing"}
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
async def preview_owner_action(
    name: str,
    payload: PreviewRequest,
    request: Request,
    session: Session = Depends(get_session),  # noqa: B008 - FastAPI dependency marker.
    user: User | None = Depends(current_user_if_required),  # noqa: B008 - FastAPI dependency marker.
):
    """What a run would do and cost, what it observed, and the digest its start must echo."""
    owner = require_human_owner(session, user, payload.company_id)
    try:
        spec = catalogue.action(name)
        params = catalogue.validate(spec, payload.params)
        if spec.local_facts is not None:
            _, shown = catalogue.observe_locally(
                spec, params, session, int(owner.id or 0)
            )
            return shown
        if spec.facts is None:
            return catalogue.preview(spec, params)
        _, shown = await catalogue.observe_and_preview(spec, params, _context(request))
        return shown
    except catalogue.RunRefused as exc:
        raise _refused(exc) from exc
    except workspace_daemon.DaemonUnavailable as exc:
        raise HTTPException(status_code=503, detail=f"daemon: {exc}") from exc
    except workspace_daemon.DaemonError as exc:
        raise HTTPException(status_code=502, detail=f"daemon: {exc.message}") from exc


async def _start_observed(
    spec: catalogue.Action,
    payload: StartRequest,
    request: Request,
    session: Session,
    owner: User,
) -> OwnerActionRun:
    """Start an action that observes first: the facts are read again and must be
    the ones confirmed (409), then the blockers, the active sprints and the
    step-up; the run keeps the facts confirmed."""
    params = catalogue.validate(spec, payload.params)
    ctx = _context(request)
    params, shown = await catalogue.observe_and_preview(spec, params, ctx)
    if payload.confirmation != shown["confirmation"]:
        raise catalogue.RunRefused(
            409, "What the confirmation showed has changed: preview again"
        )
    if shown["blockers"]:
        raise catalogue.RunRefused(409, {"blockers": shown["blockers"]})
    if spec.no_active_sprint:
        active = catalogue.active_sprints(session)
        if active:
            raise catalogue.RunRefused(
                409, {"message": "A sprint is active", "active_sprint_ids": active}
            )
    proof = None
    if shown["step_up"]:
        proof = step_up.verify(session, request, owner, payload.step_up_password)
    run = catalogue.start(
        session,
        payload.company_id,
        int(owner.id or 0),
        spec,
        params,
        payload.confirmation,
        facts=shown["facts"],
        audit_extra={"step_up": proof} if proof else None,
    )
    owner_git_runner.launch(
        run, session.get_bind(), ctx, shown["facts"], land_owner=proof is not None
    )
    return run


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
        if spec.facts is not None:
            available = _availability(spec.name)
            if not available["available"]:
                raise catalogue.RunRefused(503, available)
            started = await _start_observed(spec, payload, request, session, owner)
            return catalogue.serialize(started)
        params = catalogue.validate(spec, payload.params)
        available = _availability(spec.name)
        if not available["available"]:
            raise catalogue.RunRefused(503, available)
        facts = None
        if spec.local_facts is not None:
            # Read again: a policy changed since the preview is a 409 (digest).
            facts, shown = catalogue.observe_locally(
                spec, params, session, int(owner.id or 0)
            )
            if shown["blockers"]:
                raise catalogue.RunRefused(
                    409, {"blockers": shown["blockers"], "facts": facts}
                )
        run = catalogue.start(
            session,
            payload.company_id,
            int(owner.id or 0),
            spec,
            params,
            payload.confirmation,
            facts=facts,
        )
    except catalogue.RunRefused as exc:
        raise _refused(exc) from exc
    except workspace_daemon.DaemonUnavailable as exc:
        raise HTTPException(status_code=503, detail=f"daemon: {exc}") from exc
    except workspace_daemon.DaemonError as exc:
        raise HTTPException(status_code=502, detail=f"daemon: {exc.message}") from exc
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


@router.get("/owner_actions/{name}/options")
async def owner_action_options(
    name: str,
    request: Request,
    company_id: int = Query(...),
    session: Session = Depends(get_session),  # noqa: B008 - FastAPI dependency marker.
    user: User | None = Depends(current_user_if_required),  # noqa: B008 - FastAPI dependency marker.
):
    """What the Owner picks from: the daemon's local branches, or the open issues."""
    require_human_owner(session, user, company_id)
    try:
        catalogue.action(name)
        return await git.options(_context(request), name)
    except catalogue.RunRefused as exc:
        raise _refused(exc) from exc
    except git.Refused as exc:
        raise HTTPException(status_code=exc.status, detail=exc.detail) from exc
    except workspace_daemon.DaemonUnavailable as exc:
        raise HTTPException(status_code=503, detail=f"daemon: {exc}") from exc
    except workspace_daemon.DaemonError as exc:
        raise HTTPException(status_code=502, detail=f"daemon: {exc.message}") from exc
