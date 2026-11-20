"""The local copy of a company's repository against its remote (issue #745,
``services/release/base_sync.py``).

- ``POST /v1/workspace/git_check``                what the New company form shows: the folder's remote,
  branches and checkout, and, with ``contact_remote``, how far its base is behind (authenticated,
  then held to ``folder_check.may_check``, ``GIT_CHECK_RATE_LIMIT`` per account, and audited)
- ``POST /v1/companies/{id}/git/sync_base``       "Bring the local copy up to date" on the settings (Manager)

and the two halves of ``POST /v1/init_company`` that concern it
(``before_creation``, ``after_creation``): the Owner's forge policy checked
against the ceiling before anything is created and audited
(``forge_policy_changed``, ``at: creation``) once the company exists, and,
when the Owner ticked "Bring my local copy up to date" under a policy that
allows a fetch, the daemon's fast-forward run before the folder is read,
then recorded on the new company (``base_sync_observed``). A refusal never
stops the creation.
"""

from __future__ import annotations

import asyncio
from pathlib import Path
from typing import Any

from auth_policy import (
    current_user_if_required,
    require_authenticated,
    require_company_role,
)
from config import settings
from database import get_session
from fastapi import APIRouter, Depends, HTTPException, Request
from models import Company, User
from pydantic import Field
from schemas import CompanyInitRequest, RequestModel
from security import limiter
from services.contracts import forge_policy
from services.planning.discovery_service import detect_stack
from services.platform import forge_policy_setting, sod
from services.release import base_sync, folder_check, git_setup
from services.workspace.workspace_daemon import materialized_snapshot
from slowapi.util import get_remote_address
from sqlmodel import Session

router = APIRouter(prefix="/v1", tags=["Company"])


class FolderGitCheck(RequestModel):
    """Payload for ``POST /v1/workspace/git_check``."""

    project_path: str = Field(min_length=1, max_length=4096)
    git_base_branch: str | None = Field(default=None, max_length=200)
    # The Owner pressed "Check remote": the daemon fetches the base's one branch.
    contact_remote: bool = False


def _actor(session: Session, user: User | None) -> dict[str, Any]:
    actor = sod.actor_of(session, user)
    return {
        "by": (actor.label if actor else "") or "A person",
        "user_id": actor.user_id if actor else None,
        "token_id": actor.token_id if actor else None,
    }


def _requested_policy(req: CompanyInitRequest) -> str:
    return forge_policy.parse(req.forge_policy)


async def before_creation(
    nats_client: Any, req: CompanyInitRequest, base: str | None
) -> dict[str, Any] | None:
    """Check the chosen policy, then, when asked and allowed, fast-forward the local base; its observation."""
    if req.forge_policy is not None:
        try:
            forge_policy_setting.require_allowed(req.forge_policy)
        except forge_policy_setting.ForgePolicyRefused as exc:
            raise HTTPException(status_code=exc.status, detail=exc.detail) from exc
    path = (req.project_path or "").strip()
    if not req.sync_base or not path:
        return None
    return await base_sync.request_sync(nats_client, path, base, _requested_policy(req))


async def stack_after_sync(
    synced: dict[str, Any] | None,
    project: dict[str, Any] | None,
    requested: dict[str, Any] | None,
) -> dict[str, Any] | None:
    """The stack read again from the inspection after a fast-forward that moved the base; else *requested*.

    The wizard's Scan ran on the folder before "Bring my local copy up to
    date"; once the base moved, the deterministic scan runs on the daemon's
    new snapshot, so the stack recorded is the one the agents will read. The
    team the Scan suggested is kept (``team_source`` too): the Owner chose it.
    """
    if (
        not synced
        or synced.get("outcome") != "updated"
        or not isinstance(project, dict)
    ):
        return requested
    snapshot = project.get("snapshot")
    if not isinstance(snapshot, dict) or not project.get("is_dir"):
        return requested

    def scan() -> dict[str, Any]:
        with materialized_snapshot(snapshot) as mirror:
            return detect_stack(Path(mirror), project.get("name"))

    stack = await asyncio.to_thread(scan)
    if isinstance(requested, dict) and requested.get("team_source"):
        stack["team_source"] = requested["team_source"]
    stack["read_after_sync"] = True
    return stack


def after_creation(
    session: Session,
    user: User | None,
    result: dict[str, Any],
    req: CompanyInitRequest,
    synced: dict[str, Any] | None,
) -> dict[str, Any]:
    """The Owner's policy and the sync's observation on the new company's record; the response."""
    company_id = result.get("company_id")
    if company_id is None:
        return result
    actor = _actor(session, user)
    if req.forge_policy is not None:
        result["forge_policy"] = forge_policy_setting.set_policy(
            session, company_id, req.forge_policy, at_creation=True, **actor
        )
    if synced is not None:
        base_sync.record(session, company_id, synced, at="creation", **actor)
        result["base_sync"] = synced
    return result


def _git_check_limit() -> str:
    return settings.GIT_CHECK_RATE_LIMIT


def _per_account(request: Request) -> str:
    """The limiter's key: the signed-in account, else the client's address."""
    return getattr(request.state, "rate_account", None) or get_remote_address(request)


def _account(
    request: Request,
    user: User | None = Depends(current_user_if_required),  # noqa: B008 - FastAPI dependency marker.
) -> User | None:
    """The caller, named on the request for the limiter (which runs after the dependencies)."""
    request.state.rate_account = f"user:{user.id}" if user is not None else None
    return user


@router.post("/workspace/git_check")
@limiter.limit(_git_check_limit, key_func=_per_account)
async def check_folder_git(
    request: Request,
    payload: FolderGitCheck,
    session: Session = Depends(get_session),  # noqa: B008 - FastAPI dependency marker.
    user: User | None = Depends(_account),  # noqa: B008 - FastAPI dependency marker.
):
    """The folder's base as the daemon observed it now, limited to what the form shows (``folder_check.py``)."""
    require_authenticated(user)
    if not folder_check.may_check(session, user):
        raise HTTPException(
            status_code=403,
            detail=(
                "On a deployment with several accounts, only an account that "
                "already belongs to a company, or the deployment's first "
                "account, may check a folder before creating a company"
            ),
        )
    base = git_setup.clean_base(payload.git_base_branch)
    path = payload.project_path.strip()
    observed = await folder_check.check_folder(
        getattr(request.app.state, "nats_client", None),
        path,
        base,
        payload.contact_remote,
    )
    answer = folder_check.form_answer(observed)
    if payload.contact_remote:
        folder_check.record(session, path, answer, **_actor(session, user))
    return answer


@router.post("/companies/{company_id}/git/sync_base")
async def sync_company_base(
    company_id: int,
    request: Request,
    session: Session = Depends(get_session),  # noqa: B008 - FastAPI dependency marker.
    user: User | None = Depends(current_user_if_required),  # noqa: B008 - FastAPI dependency marker.
):
    """Fast-forward the company's local base to its remote, held to its forge policy (Manager; recorded)."""
    require_company_role(session, user, company_id, "Manager")
    company = session.get(Company, company_id)
    if company is None:
        raise HTTPException(status_code=404, detail="Company not found")
    return await base_sync.sync_company(
        getattr(request.app.state, "nats_client", None),
        session,
        company,
        **_actor(session, user),
    )
