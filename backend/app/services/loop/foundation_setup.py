"""Whether a new company gets its foundation sprint (retrospective F8).

``POST /v1/init_company`` always planned "Sprint 0 — Foundations"; on a
folder that already holds a project — La Fabrique's own checkout in the
self-hosting run — nothing needs that sprint and it stays ``planned``
forever. The request may now ask for the sprint or decline it
(``foundation_sprint``); when it does neither, the workspace daemon's
observation of the folder decides: a repository whose files include source
code gets no foundation sprint.

The observation is the daemon's (one ``workspace.inspect`` with its file
snapshot), never a claim from the request — a request that says the folder
is a repository decides nothing by itself. An unobserved folder (no daemon
answered, or no folder was named) keeps the old behaviour: the sprint is
planned. The response carries the decision with its reason, and the
observation beside it, as separate fields.
"""

from __future__ import annotations

from typing import Any

from logging_config import logger

from services.planning.stack_detection import has_code_files
from services.workspace.workspace_daemon import (
    DaemonError,
    DaemonUnavailable,
    inspect_project,
)

# The decision's reasons, one per way it can be made; every one has its
# sentence for the response's ``detail``.
REQUESTED = "requested"
DECLINED = "declined"
REPOSITORY_WITH_CODE = "repository_with_code"
FOLDER_WITHOUT_CODE = "folder_without_code"
NO_FOLDER = "no_folder"
DAEMON_UNOBSERVED = "daemon_unobserved"

# The observation's states: no folder was named, nobody observed it, or the
# daemon answered.
_NO_FOLDER = "no_folder"
_UNOBSERVED = "unobserved"
_OBSERVED = "observed"

_PLANNED = {
    REQUESTED: True,
    DECLINED: False,
    REPOSITORY_WITH_CODE: False,
    FOLDER_WITHOUT_CODE: True,
    NO_FOLDER: True,
    DAEMON_UNOBSERVED: True,
}

_DETAILS = {
    REQUESTED: "planned: the request asked for the foundation sprint",
    DECLINED: "skipped: the request asked for no foundation sprint",
    REPOSITORY_WITH_CODE: "skipped: the daemon observed an existing repository with code",
    FOLDER_WITHOUT_CODE: (
        "planned: the daemon observed no existing repository with code in the folder"
    ),
    NO_FOLDER: "planned: no project folder to observe",
    DAEMON_UNOBSERVED: (
        "planned: the workspace daemon could not be reached, so the folder "
        "was not observed"
    ),
}


async def observe_folder(nats_client: Any, path: str | None) -> dict[str, Any]:
    """The project folder as the daemon observed it, for the decision below.

    One ``workspace.inspect`` with the file snapshot; the ``project`` it
    returns is also what ``git_setup.is_repository`` reads, so creating a
    company asks the daemon once. ``state`` is ``no_folder`` (the request
    named no folder), ``unobserved`` (no daemon answered) or ``observed``;
    ``repository_with_code`` is the observation itself — ``None`` without
    one, never a guess. A folder the daemon sees but that is not a
    directory, a repository without source files, or a plain folder are all
    observed as no code: only a repository with code skips the sprint.
    """
    if not path or not path.strip():
        return {"state": _NO_FOLDER, "project": None, "repository_with_code": None}
    try:
        project = await inspect_project(nats_client, path.strip(), snapshot=True)
    except (DaemonUnavailable, DaemonError) as exc:
        # Unknown is not "no code there": the sprint is planned, as before.
        logger.info("init_company: the folder was not observed (%s)", exc)
        return {"state": _UNOBSERVED, "project": None, "repository_with_code": None}
    if not isinstance(project, dict):
        return {"state": _UNOBSERVED, "project": None, "repository_with_code": None}
    git = project.get("git")
    is_repo = isinstance(git, dict) and bool(git.get("is_repo"))
    files = (project.get("snapshot") or {}).get("files") or []
    with_code = bool(project.get("is_dir")) and is_repo and has_code_files(files)
    return {"state": _OBSERVED, "project": project, "repository_with_code": with_code}


def decide(observation: dict[str, Any], requested: bool | None) -> dict[str, Any]:
    """Whether the foundation sprint is planned, and why, for the response.

    *requested* is the caller's own word (``foundation_sprint``): ``True``
    plans the sprint, ``False`` declines it, ``None`` leaves the decision to
    the daemon's observation. The answer keeps the decision and the
    observation apart: ``planned``/``reason``/``detail`` are the backend's
    deterministic rule, ``observed_repository_with_code`` is what the daemon
    said (``None``: nobody observed the folder).
    """
    observed = observation.get("repository_with_code")
    if requested is True:
        reason = REQUESTED
    elif requested is False:
        reason = DECLINED
    elif observed is True:
        reason = REPOSITORY_WITH_CODE
    elif observation.get("state") == _NO_FOLDER:
        reason = NO_FOLDER
    elif observation.get("state") == _UNOBSERVED:
        reason = DAEMON_UNOBSERVED
    else:
        reason = FOLDER_WITHOUT_CODE
    return {
        "planned": _PLANNED[reason],
        "reason": reason,
        "detail": _DETAILS[reason],
        "observed_repository_with_code": observed,
    }
