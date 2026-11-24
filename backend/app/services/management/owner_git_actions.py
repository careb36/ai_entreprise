"""The owner actions on La Fabrique's own repository: ``land_branch``, ``push_main``
and ``close_issue`` (``docs/design/owner-actions.md``, threats L1-L3, P1-P3, C1-C2).

Each has typed parameters (never a free command, path or git argument) and
*facts*: what the platform observes before the confirmation, from the daemon
(``workspace.self_host.git``, PROTOCOL.md §5.11) and GitHub (the reviewed
adapter ``producers.github_adapter``). The facts are part of the preview, so
the confirmation digest covers them: a branch that moved, a ``main`` that
moved or an edited comment after the preview is a 409 at the start.

- ``land_branch``: the branch is one the daemon lists. The facts are its
  commits and diffstat against ``main`` and the protected paths it touches,
  computed here with ``contracts.protected_paths`` from the daemon's changed
  files and main's test map (land.sh's own logic and inputs). ``needs_owner``
  says the land needs ``LAND_OWNER=1``, hence the Owner's step-up.
- ``push_main``: after the daemon's fetch, the commits of ``origin/main..main``
  that will become public; blocked unless a fast-forward with a record.
- ``close_issue``: an open issue of the repository ``origin`` names; the
  fixing commit (a commit on ``main`` whose message names ``#<n>``, as the
  daemon found it) and whether ``origin/main`` has it; the comment, prefilled
  from those observations when the Owner gave none. Blocked when the fix is
  not on ``origin`` unless she ticks the override.

This module computes; ``owner_actions`` registers the actions and
``owner_git_runner`` runs them.
"""

from __future__ import annotations

import asyncio
from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from typing import Any

from pydantic import BaseModel, ConfigDict, Field

from services.contracts import protected_paths

LAND = "land_branch"
PUSH = "push_main"
CLOSE = "close_issue"
GIT = "workspace.self_host.git"
LAND_SUBJECT = "workspace.self_host.land"
PUSH_SUBJECT = "workspace.self_host.push"
VIEW_SECONDS = 60.0
FETCH_VIEW_SECONDS = 180.0
LAND_SECONDS = 6000.0  # the daemon stops land.sh at 5,400 s by default
PUSH_SECONDS = 420.0
SHA = r"^([0-9a-f]{40}|[0-9a-f]{64})$"
BRANCH = r"^[A-Za-z0-9][A-Za-z0-9._/-]{0,199}$"
COMMENT_MAX = 5000
SHOWN_COMMITS = 50
SUBJECTS = {LAND: (GIT, LAND_SUBJECT), PUSH: (GIT, PUSH_SUBJECT), CLOSE: (GIT,)}


class Refused(Exception):
    """A preview or start refused; ``status`` follows HTTP (``RunRefused`` upstream)."""

    def __init__(self, status: int, detail: Any):
        super().__init__(str(detail))
        self.status, self.detail = status, detail


class LandParams(BaseModel):
    model_config = ConfigDict(extra="forbid")

    branch: str = Field(pattern=BRANCH, max_length=200)


class PushParams(BaseModel):
    model_config = ConfigDict(extra="forbid")


class CloseParams(BaseModel):
    model_config = ConfigDict(extra="forbid")

    issue_number: int = Field(ge=1, le=10**9)
    fix_commit: str | None = Field(default=None, pattern=SHA)
    comment: str | None = Field(default=None, min_length=1, max_length=COMMENT_MAX)
    override_not_on_origin: bool = False


@dataclass
class Context:
    """What the facts are read through; the router builds it, tests fake it."""

    daemon: Callable[[str, dict[str, Any], float], Awaitable[dict[str, Any]]]
    github: Any  # producers.github_adapter, or a fake with the same functions
    token: str | None = None


# -- land_branch -------------------------------------------------------------------


async def land_facts(ctx: Context, p: LandParams) -> tuple[LandParams, dict[str, Any]]:
    listed = await ctx.daemon(GIT, {"view": "branches"}, VIEW_SECONDS)
    if p.branch not in {b.get("name") for b in listed.get("branches") or []}:
        raise Refused(422, f"{p.branch!r} is not a local branch the daemon lists")
    seen = await ctx.daemon(
        GIT, {"view": "land_preview", "branch": p.branch}, VIEW_SECONDS
    )
    test_map = seen.get("test_map")
    patterns = protected_paths.protected_patterns(test_map or None)
    touched = protected_paths.matches(seen.get("changed") or [], patterns)
    return p, {
        "branch": p.branch,
        "head": seen["head"],
        "main": seen["main"],
        "already_in_main": bool(seen.get("already_in_main")),
        "commits": (seen.get("commits") or [])[:SHOWN_COMMITS],
        "commit_count": len(seen.get("commits") or []),
        "commits_truncated": bool(seen.get("truncated"))
        or len(seen.get("commits") or []) > SHOWN_COMMITS,
        "diffstat": seen.get("diffstat") or [],
        "files_truncated": bool(seen.get("files_truncated")),
        "protected": [{"path": m.path, "pattern": m.pattern} for m in touched],
        # land.sh refuses a list it cannot read unless LAND_OWNER=1.
        "test_map_read": test_map is not None,
        "needs_owner": bool(touched) or test_map is None,
    }


def land_effect(p: LandParams, f: dict[str, Any]) -> list[dict[str, Any]]:
    effect = [
        {"key": "merge", "values": {"branch": p.branch, "count": f["commit_count"]}},
        {"key": "checks", "values": {}},
        {"key": "main", "values": {"main": f["main"][:12]}},
    ]
    if f["needs_owner"]:
        effect.append({"key": "protected", "values": {"count": len(f["protected"])}})
    effect.append({"key": "no_push", "values": {}})
    return effect


def land_cost(p: LandParams, f: dict[str, Any]) -> dict[str, Any]:
    return {"models": [], "token_budget": 0, "max_seconds": int(LAND_SECONDS)}


def land_blockers(p: LandParams, f: dict[str, Any]) -> list[str]:
    return ["nothing_to_land"] if f["already_in_main"] else []


# -- push_main ---------------------------------------------------------------------


async def push_facts(ctx: Context, p: PushParams) -> tuple[PushParams, dict[str, Any]]:
    seen = await ctx.daemon(GIT, {"view": "push_preview"}, FETCH_VIEW_SECONDS)
    commits = seen.get("commits") or []
    return p, {
        "main": seen["main"],
        "origin_main": seen.get("origin_main"),
        "fast_forward": bool(seen.get("fast_forward")),
        "up_to_date": bool(seen.get("up_to_date")),
        "has_record": bool(seen.get("has_record")),
        "notes": bool(seen.get("notes")),
        "commits": commits,
        "commit_count": len(commits),
        "commits_truncated": bool(seen.get("truncated")),
        "remote_url": seen.get("remote_url"),
        "public": True,
    }


def push_effect(p: PushParams, f: dict[str, Any]) -> list[dict[str, Any]]:
    return [
        {"key": "public", "values": {}},
        {
            "key": "push",
            "values": {
                "count": f["commit_count"],
                "main": f["main"][:12],
                "remote": f["remote_url"] or "origin",
            },
        },
        {"key": "notes", "values": {}}
        if f["notes"]
        else {"key": "no_notes", "values": {}},
        {"key": "never_force", "values": {}},
    ]


def push_cost(p: PushParams, f: dict[str, Any]) -> dict[str, Any]:
    return {"models": [], "token_budget": 0, "max_seconds": int(PUSH_SECONDS)}


def push_blockers(p: PushParams, f: dict[str, Any]) -> list[str]:
    if not f["fast_forward"]:
        return ["not_fast_forward"]
    if f["up_to_date"]:
        return ["nothing_to_push"]
    return [] if f["has_record"] else ["no_verification_record"]


# -- close_issue -------------------------------------------------------------------


def suggested_comment(fix: dict[str, Any] | None, on_origin: bool) -> str:
    """The comment prefilled from the observations; the Owner may edit it."""
    if fix is None:
        return "Closed from La Fabrique's owner actions."
    where = (
        "It is on origin/main."
        if on_origin
        else "It was not on origin/main yet when this was written."
    )
    return f"Fixed by {fix['sha']} ({fix['subject']}). {where}"


async def repository(ctx: Context) -> tuple[str, str]:
    seen = await ctx.daemon(GIT, {"view": "origin"}, VIEW_SECONDS)
    found = ctx.github.repository_of(seen.get("remote_url"))
    if found is None:
        raise Refused(503, "The daemon's origin is not a github.com repository")
    return found


async def open_issues(ctx: Context, owner: str, repo: str) -> list[dict[str, Any]]:
    try:
        return await asyncio.to_thread(
            ctx.github.list_open_issues, owner, repo, token=ctx.token
        )
    except (ctx.github.GitHubApiError, ctx.github.GitHubRateLimited) as exc:
        raise Refused(502, f"GitHub: {exc}") from exc


async def close_facts(
    ctx: Context, p: CloseParams
) -> tuple[CloseParams, dict[str, Any]]:
    owner, repo = await repository(ctx)
    issue = next(
        (
            i
            for i in await open_issues(ctx, owner, repo)
            if i["number"] == p.issue_number
        ),
        None,
    )
    if issue is None:
        raise Refused(422, f"#{p.issue_number} is not an open issue of {owner}/{repo}")
    seen = await ctx.daemon(
        GIT, {"view": "fix_commit", "issue": p.issue_number}, FETCH_VIEW_SECONDS
    )
    candidates = seen.get("commits") or []
    if p.fix_commit is not None:
        fix = next((c for c in candidates if c["sha"] == p.fix_commit), None)
        if fix is None:
            raise Refused(422, "That commit is not one on main that names this issue")
    else:
        fix = candidates[0] if candidates else None
    on_origin = bool(fix and fix.get("on_origin"))
    comment = p.comment if p.comment is not None else suggested_comment(fix, on_origin)
    params = p.model_copy(
        update={"fix_commit": fix["sha"] if fix else None, "comment": comment}
    )
    return params, {
        "repository": f"{owner}/{repo}",
        "issue": issue,
        "fix_commit": fix["sha"] if fix else None,
        "fix_subject": fix["subject"] if fix else None,
        "fix_on_origin": on_origin,
        "origin_main": seen.get("origin_main"),
        "fetched": bool(seen.get("fetched")),
        "candidates": candidates[:10],
    }


def close_effect(p: CloseParams, f: dict[str, Any]) -> list[dict[str, Any]]:
    effect = [
        {
            "key": "comment",
            "values": {"number": p.issue_number, "repository": f["repository"]},
        },
        {"key": "close", "values": {"number": p.issue_number}},
    ]
    if not f["fix_on_origin"] and p.override_not_on_origin:
        effect.append({"key": "override", "values": {}})
    return effect


def close_cost(p: CloseParams, f: dict[str, Any]) -> dict[str, Any]:
    return {"models": [], "token_budget": 0, "max_seconds": 60}


def close_blockers(p: CloseParams, f: dict[str, Any]) -> list[str]:
    if f["fix_on_origin"] or p.override_not_on_origin:
        return []
    return ["fix_not_on_origin"]


# -- what the Owner picks from -----------------------------------------------------


async def options(ctx: Context, name: str) -> dict[str, Any]:
    if name == LAND:
        listed = await ctx.daemon(GIT, {"view": "branches"}, VIEW_SECONDS)
        return {"main": listed.get("main"), "branches": listed.get("branches") or []}
    if name == CLOSE:
        owner, repo = await repository(ctx)
        return {
            "repository": f"{owner}/{repo}",
            "issues": await open_issues(ctx, owner, repo),
        }
    return {}
