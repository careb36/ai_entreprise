"""Whether a company's folder is a git repository, and its Git workflow set from that.

The Git workflow (``services/workspace/git_workflow.py``) writes each task on a branch
in a worktree; a folder that is not a repository has no branch to write on.
So when a company is created or its folder recorded, the workflow is set
from what the workspace daemon observed in the folder (``workspace.inspect``:
``git.is_repo``), never from what the request says the folder is: on only
for a repository. A person may still turn it off at creation, and change it
later on the company's settings (``PUT /v1/companies/{id}/git``).

Without an observation (no daemon answered, and the backend cannot see the
folder either) the workflow stays off: an unobserved folder is not taken for
a repository. ``base_status`` asks the daemon how the company's base branch
stands against its remote (``workspace.git.base_status``,
``vscode-daemon/git_base.py``), for the settings page.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

from fastapi import HTTPException, Request
from models import Company
from sqlmodel import Session

from services.release.integration import _project_path
from services.workspace import git_workflow
from services.workspace.workspace_daemon import (
    DaemonError,
    DaemonUnavailable,
    daemon_request,
    inspect_project,
)

# A fetch of one branch on the daemon (15 s there) and its reply.
BASE_STATUS_TIMEOUT_SECONDS = 25.0


def clean_base(name: str | None) -> str | None:
    """A base branch a Manager named, trimmed; a name git would misread is a 422."""
    base = (name or "").strip() or None
    if not git_workflow.valid_base(base):
        raise HTTPException(status_code=422, detail=f"not a branch name: {base}")
    return base


def is_repository(project: dict[str, Any] | None, path: str | None) -> bool | None:
    """What was observed: the daemon's inspection, else the folder as this server sees it; ``None`` unobserved."""
    if isinstance(project, dict) and project.get("is_dir"):
        git = project.get("git")
        return bool(isinstance(git, dict) and git.get("is_repo"))
    if path:
        folder = Path(path).expanduser()
        if folder.is_dir():
            return (folder / ".git").exists()
    return None


async def observe(request: Request, path: str | None) -> bool | None:
    """Ask the daemon whether *path* is a repository (``None``: nobody could say)."""
    if not path or not path.strip():
        return None
    project = None
    try:
        project = await inspect_project(
            getattr(request.app.state, "nats_client", None), path.strip()
        )
    except (DaemonUnavailable, DaemonError):
        project = None
    return is_repository(project, path.strip())


def workflow_for(observed: bool | None, requested: bool | None) -> bool:
    """On only for a folder observed to be a repository, unless the person turned it off."""
    return observed is True and requested is not False


def record(
    session: Session, company_id: int, project: dict[str, Any] | None, path: str
) -> None:
    """A folder recorded for a company sets its workflow from what was observed there."""
    company = session.get(Company, company_id)
    if company is None:
        return
    company.git_workflow = workflow_for(is_repository(project, path), None)
    session.add(company)
    session.commit()


async def base_status(request: Request, session: Session, company: Company) -> dict:
    """The folder and its base against the remote, as the daemon observed it now, or why unknown."""
    path = _project_path(session, company.id)
    if not path:
        return {"state": "unknown", "reason": "The company has no project folder"}
    try:
        observed = await daemon_request(
            getattr(request.app.state, "nats_client", None),
            "workspace.git.base_status",
            {
                "workspace_root": path,
                "base": company.git_base_branch or "",
            },
            BASE_STATUS_TIMEOUT_SECONDS,
        )
    except DaemonUnavailable:
        return {"state": "unknown", "reason": "No workspace daemon answered"}
    except DaemonError as e:
        return {"state": "unknown", "reason": e.message}
    return {"state": "observed", "path": path, **observed}
