"""Bring the person's local base branch up to date with its remote (issue #745).

``workspace.git.sync_base``: when a company is created from a folder whose
clone is weeks behind its remote, or later from the company's settings, the
Owner may ask that the local base branch catch up, so that the files the
agents read are the current ones. Only a **fast-forward**, the way
``local_merge._advance`` moves a base:

- the base's own branch is fetched from its remote (``git fetch`` of that one
  branch into a temporary ref, with the person's credentials on this machine,
  no shell, no prompt, bounded in time);
- local base behind and an ancestor of the remote tip: it moves to that tip,
  with ``git update-ref`` and the old value expected when no worktree has it
  checked out, else ``git merge --ff-only`` in the checkout that has it. The
  remote-tracking branch is updated to the same tip, as ``git pull`` would;
- already equal, or only ahead (unpushed work of the person's): nothing moves.

There is no merge commit, no rebase and no reset, so the local copy never
loses a commit. It refuses, changing nothing and saying why, when:

- the forge policy does not allow a fetch (``forge_disabled``; nothing is
  contacted, ``forge_gate``, issue #727);
- the base has no remote (``no_remote``) or the remote has no such branch
  (``base_not_on_remote``);
- a rebase, merge, cherry-pick or revert is unfinished in the folder or in
  the checkout that has the base (``operation_in_progress``), or that
  checkout has uncommitted changes to tracked files (``dirty``); both are
  read before the remote is contacted;
- the remote cannot be read (``remote_unreachable``, with git's reason);
- both sides moved (``diverged``, naming both counts).

A refusal leaves the refs and the index as they were: the fetch writes only
the temporary ref, deleted at once, and the objects it brought. What was
observed is the answer (``outcome``, ``before``, ``after``, ``behind``,
``ahead``, the remote and its URL without credentials, ``forge_policy`` and
``remote_contacted``): an observation of this daemon, which the backend
records on the company.
"""

from __future__ import annotations

import asyncio
import uuid
from pathlib import Path
from typing import Any, Dict, Optional

import forge_gate
import git_base
import git_local
from git_workspace import GitError, run_git, valid_branch
from local_merge import checked_out_in

# One branch fetched while the person waits on the form: bounded.
SYNC_FETCH_TIMEOUT_SECONDS = 20
_TEMP_PREFIX = "refs/fabrique-sync/"

UPDATED = "updated"
UP_TO_DATE = "up_to_date"
AHEAD = "ahead"
# The outcomes that are not refusals.
DONE = (UPDATED, UP_TO_DATE, AHEAD)


def _refused(answer: Dict[str, Any], outcome: str, reason: str) -> Dict[str, Any]:
    return {**answer, "ok": False, "outcome": outcome, "reason": reason}


def _base_name(root: Path, base: str) -> Optional[str]:
    """The local branch to bring up to date: *base*, else the one checked out."""
    name = base or git_local.current_branch(root) or ""
    if not name or not valid_branch(name):
        return None
    found = run_git(["show-ref", "--verify", "--quiet", f"refs/heads/{name}"], root, check=False)
    return name if found.returncode == 0 else None


def _local_refusal(root: Path, checkout: Optional[Path], name: str) -> Optional[tuple]:
    """``(outcome, reason)`` when the folder or the base's checkout is not safe to move."""
    for place in dict.fromkeys(p for p in (root, checkout) if p is not None):
        operation = git_local.operation_in_progress(place)
        if operation:
            return ("operation_in_progress",
                    f"a {operation} is in progress in {place}: finish or abort it first")
    if checkout is not None and not git_local.clean(checkout):
        return ("dirty",
                f"{name} is checked out in {checkout} with uncommitted changes: commit or stash them first")
    return None


def _fetch(root: Path, remote: str, there: str) -> tuple:
    """``(remote tip, None)`` or ``(None, (outcome, reason))``; only a temporary ref is written, and removed."""
    temp = f"{_TEMP_PREFIX}{uuid.uuid4().hex[:12]}"
    try:
        try:
            # ``--refmap=``: no opportunistic update of ``refs/remotes/<remote>/*``.
            fetched = run_git(["fetch", "--no-tags", "--quiet", "--refmap=", remote, f"+refs/heads/{there}:{temp}"],
                              root, check=False, timeout=SYNC_FETCH_TIMEOUT_SECONDS)
            reason = None if fetched.returncode == 0 else (
                (fetched.stderr or fetched.stdout).strip() or "git fetch failed")
        except GitError as e:
            reason = str(e)
        if reason and "couldn't find remote ref" in reason.lower():
            return None, ("base_not_on_remote", f"{remote} has no branch {there}")
        if reason:
            return None, ("remote_unreachable", reason[-500:])
        tip = git_base._commit(root, temp)
        if tip is None:
            return None, ("remote_unreachable", f"{remote} has no branch {there}")
        return tip, None
    finally:
        run_git(["update-ref", "-d", temp], root, check=False)


def _advance(root: Path, name: str, before: str, after: str) -> None:
    """Move *name* from *before* to *after*, a fast-forward, where it lives."""
    checkout = checked_out_in(root, name)
    if checkout is None:
        run_git(["update-ref", f"refs/heads/{name}", after, before], root)
        return
    refusal = _local_refusal(root, checkout, name)
    if refusal:
        raise GitError(refusal[1])
    if run_git(["rev-parse", "HEAD"], checkout).stdout.strip() != before:
        raise GitError(f"{name} moved while it was being brought up to date; try again")
    run_git(["merge", "--ff-only", "--quiet", after], checkout)


def sync(workspace_root: str, base: str, policy: str = forge_gate.NONE) -> Dict[str, Any]:
    """Fast-forward the local *base* (default: the checked-out branch) to its remote, or say why not."""
    folder = Path(workspace_root).expanduser()
    if not folder.is_dir():
        raise GitError(f"the project folder does not exist on this machine: {workspace_root}")
    if base and not valid_branch(base):
        raise GitError(f"not a branch name: {base!r}")
    answer: Dict[str, Any] = {"ok": False, "forge_policy": policy, "remote_contacted": False}
    root = git_base.toplevel(folder)
    if root is None:
        return _refused(answer, "not_a_repository", f"{workspace_root} is not a git repository")
    answer["repository"] = str(root)
    name = _base_name(root, base)
    if name is None:
        return _refused(answer, "not_a_branch", f"not a local branch in this repository: {base or 'HEAD'!r}")
    before = git_base._commit(root, f"refs/heads/{name}")
    answer.update(base=name, before=before, after=before)
    upstream = git_base.upstream_of(root, name)
    if upstream is None:
        return _refused(answer, "no_remote", f"{name} has no remote to bring it up to date from")
    remote, there = upstream
    answer.update(remote=f"{remote}/{there}", remote_url=git_local.remote_url(root, remote))
    if not forge_gate.allows_fetch(policy):
        return _refused(answer, forge_gate.FORGE_DISABLED,
                        forge_gate.refusal_message(policy, f"fetch {remote}/{there}"))
    checkout = checked_out_in(root, name)
    refusal = _local_refusal(root, checkout, name)
    if refusal:
        return _refused(answer, *refusal)
    answer["remote_contacted"] = True
    tip, refusal = _fetch(root, remote, there)
    if refusal:
        return _refused(answer, *refusal)
    behind = git_base._count(root, before, tip)
    ahead = git_base._count(root, tip, before)
    answer.update(remote_commit=tip, behind=behind, ahead=ahead)
    if behind and ahead:
        return _refused(answer, "diverged",
                        f"the local {name} and {remote}/{there} have diverged ({ahead} commit(s) only here, "
                        f"{behind} only there): neither can be chosen without dropping commits")
    if behind:
        try:
            _advance(root, name, before, tip)
        except GitError as e:
            return _refused(answer, "not_moved", str(e)[-500:])
        outcome, answer["after"] = UPDATED, tip
    else:
        outcome = AHEAD if ahead else UP_TO_DATE
    # As ``git pull`` does: the remote-tracking branch shows what was read.
    run_git(["update-ref", f"refs/remotes/{remote}/{there}", tip], root, check=False)
    return {**answer, "ok": True, "outcome": outcome}


async def handle_sync(nc, msg, reply, request_body) -> None:
    """``workspace.git.sync_base`` → the observation, or ``{"error": {status, message}}``."""
    data = request_body(msg)
    try:
        body = await asyncio.to_thread(sync, str(data.get("workspace_root") or ""), str(data.get("base") or ""),
                                       forge_gate.of(data))
    except GitError as e:
        body = {"error": {"status": 409, "message": str(e)}}
    await reply(nc, msg, body)


HANDLERS = (("workspace.git.sync_base", handle_sync),)
