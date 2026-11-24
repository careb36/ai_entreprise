"""The jobs of ``land_branch``, ``push_main`` and ``close_issue`` (``docs/design/owner-actions.md``).

Each job does one thing on behalf of the Owner who confirmed it, with what
she confirmed (the run's parameters and the facts the preview showed, kept
as the run's ``confirmed`` observation), and finishes the run's record:

- ``land_branch``: ``workspace.self_host.land`` with the branch, the head the
  preview showed and ``land_owner`` (true only for a protected land whose
  start passed the step-up). Records the daemon's observations (exit code,
  ``main`` before and after) and land.sh's verification record as land.sh
  wrote it, under ``verification_record`` (a claim, it says so itself), with
  ``check_summary`` taken from it.
- ``push_main``: ``workspace.self_host.push`` with the ``main`` and
  ``origin/main`` the preview showed; records the pushed range the daemon
  read back from the remote-tracking ref.
- ``close_issue``: the confirmed comment posted exactly as confirmed, then
  the close, through the reviewed GitHub adapter; records the issue URL, the
  comment's URL, the text sent and the text GitHub stored, and the close.

A cancel is honoured before the job's one call; once the daemon or GitHub
has the request, the run finishes with what happened.
"""

from __future__ import annotations

import asyncio
import logging
from collections.abc import Callable
from dataclasses import dataclass
from typing import Any

from models import OwnerActionRun
from sqlmodel import Session

from services.management import owner_actions
from services.management import owner_git_actions as git
from services.workspace import workspace_daemon

logger = logging.getLogger(__name__)


@dataclass
class GitJob:
    run_id: int
    action: str
    params: dict[str, Any]
    facts: dict[str, Any]
    land_owner: bool
    session_factory: Callable[[], Session]
    ctx: git.Context


def _check_summary(record: dict[str, Any] | None) -> dict[str, Any] | None:
    if not isinstance(record, dict):
        return None
    keys = ("status", "exit_code", "command", "duration_seconds", "steps", "commit")
    return {k: record.get(k) for k in keys}


async def _land(job: GitJob) -> tuple[str, int | None, str | None, dict[str, Any]]:
    f = job.facts
    land_owner = bool(job.land_owner and f.get("needs_owner"))
    reply = await job.ctx.daemon(
        git.LAND_SUBJECT,
        {
            "branch": job.params["branch"],
            "expected_head": f["head"],
            "land_owner": land_owner,
        },
        git.LAND_SECONDS,
    )
    observed = {
        "branch": reply.get("branch"),
        "branch_head": reply.get("head"),
        "land_owner": reply.get("land_owner"),
        "exit_code": reply.get("exit_code"),
        "outcome": reply.get("outcome"),
        "landed": reply.get("landed"),
        "main_before": reply.get("main_before"),
        "main_after": reply.get("main_after"),
        "verification_record": reply.get("record"),
        "check_summary": _check_summary(reply.get("record")),
        "output_tail": reply.get("output_tail"),
    }
    landed = bool(reply.get("landed"))
    return (
        "succeeded" if landed else "failed",
        reply.get("exit_code"),
        None if landed else str(reply.get("outcome") or "not_landed"),
        observed,
    )


async def _push(job: GitJob) -> tuple[str, int | None, str | None, dict[str, Any]]:
    f = job.facts
    reply = await job.ctx.daemon(
        git.PUSH_SUBJECT,
        {"main": f["main"], "origin_main": f.get("origin_main")},
        git.PUSH_SECONDS,
    )
    observed = {
        "pushed_range": {"from": reply.get("before"), "to": reply.get("after")},
        "pushed": reply.get("pushed"),
        "notes_ref": reply.get("notes_ref"),
        "refspecs": reply.get("refspecs"),
        "remote_url": reply.get("remote_url"),
    }
    pushed = bool(reply.get("pushed"))
    return (
        "succeeded" if pushed else "failed",
        None,
        None if pushed else "origin_not_at_main",
        observed,
    )


async def _close(job: GitJob) -> tuple[str, int | None, str | None, dict[str, Any]]:
    f, p, gh = job.facts, job.params, job.ctx.github
    owner, repo = f["repository"].split("/", 1)
    number, text = int(p["issue_number"]), p["comment"]
    observed: dict[str, Any] = {
        "issue_url": (f.get("issue") or {}).get("url"),
        "comment_sent": text,
        "fix_commit": f.get("fix_commit"),
        "fix_on_origin": f.get("fix_on_origin"),
        "override_not_on_origin": bool(p.get("override_not_on_origin")),
    }
    comment = await asyncio.to_thread(
        gh.comment_on_issue, owner, repo, number, text, token=job.ctx.token
    )
    observed.update(
        comment_url=comment.get("url"),
        comment_id=comment.get("id"),
        comment_stored=comment.get("body"),
        comment_stored_matches=comment.get("body") == text,
    )
    try:
        closed = await asyncio.to_thread(
            gh.close_issue, owner, repo, number, token=job.ctx.token
        )
    except (gh.GitHubApiError, gh.GitHubRateLimited) as exc:
        observed["closed"] = False
        return "failed", None, f"close_failed: {str(exc)[:300]}", observed
    observed.update(issue_url=closed.get("url") or observed["issue_url"])
    observed["closed"] = closed.get("state") == "closed"
    status = "succeeded" if observed["closed"] else "failed"
    return status, None, None if observed["closed"] else "not_closed", observed


JOBS = {git.LAND: _land, git.PUSH: _push, git.CLOSE: _close}


async def run(job: GitJob) -> OwnerActionRun | None:
    """The job's one call; always finishes the run's record."""
    status, exit_status, reason, observed = "failed", None, None, {}
    gh = job.ctx.github
    try:
        with job.session_factory() as session:
            cancelled = owner_actions.cancel_requested(session, job.run_id)
        if cancelled:
            status, reason = "cancelled", "cancelled"
        else:
            status, exit_status, reason, observed = await JOBS[job.action](job)
    except workspace_daemon.DaemonUnavailable as exc:
        reason = f"daemon_unavailable: {str(exc)[:300]}"
    except workspace_daemon.DaemonError as exc:
        reason = f"daemon_refused ({exc.status}): {exc.message[:300]}"
    except (gh.GitHubApiError, gh.GitHubRateLimited) as exc:
        reason = f"github: {str(exc)[:300]}"
    except Exception as exc:
        logger.exception("owner action run %s failed", job.run_id)
        reason = f"error: {type(exc).__name__}: {str(exc)[:300]}"
    with job.session_factory() as session:
        return owner_actions.finish(
            session, job.run_id, status, exit_status, reason, observed
        )


_TASKS: set[asyncio.Task] = set()


def launch(
    run_row: OwnerActionRun,
    engine: Any,
    ctx: git.Context,
    facts: dict[str, Any],
    land_owner: bool = False,
) -> asyncio.Task:
    """Start *run_row*'s job on the running loop (``POST …/runs``)."""
    job = GitJob(
        run_id=int(run_row.id or 0),
        action=run_row.action,
        params=dict(run_row.params or {}),
        facts=facts,
        land_owner=land_owner,
        session_factory=lambda: Session(engine),
        ctx=ctx,
    )
    task = asyncio.get_running_loop().create_task(run(job))
    _TASKS.add(task)
    task.add_done_callback(_TASKS.discard)
    return task
