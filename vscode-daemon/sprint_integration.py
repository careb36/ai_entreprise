"""A sprint's task branches joined into one branch, the way a release is cut.

With the company's Git workflow on, each task is a branch
(``fabrique/task-<id>``, ``git_workspace.deliver``). When the sprint closes
the backend asks for ``workspace.git.integrate``: the branch
``fabrique/sprint-<id>`` is started from the company's base (or the commit
the repository has checked out), as its remote has it when the local copy is
behind (``git_base``; a branch started earlier gets that base merged in
first), in a worktree under the repository's git
directory like a task's, and the task branches are merged into it in the
order the backend names (their dependencies first). The person's checkout,
index and branch are never touched; nothing is pushed.

The answer is what git observed: the merges made, the branches already in
or missing, and, at the first merge git could not make on its own, the
conflicted files and the conflict hunks as git printed them. That merge is
aborted, the branches after it are not attempted (one may build on the
conflicted one), and the branch stays at the last clean merge. Running the
same request again picks up where it stopped: a branch already merged is
skipped, so a published integration branch only ever moves forward.

``sources`` names the commit each task branch pointed at when it was read
(once: that commit is the one checked and merged),
and ``test_worktree`` is a detached worktree at the integrated commit, of
this attempt only (the backend's ``attempt`` token): the suite runs there, so
neither a later integration nor a retry of this one changes what it tested.
"""

from __future__ import annotations

import asyncio
import re
import secrets
import threading
import time
from pathlib import Path
from typing import Any, Dict

import git_base
from git_workspace import GitError, merge_one, run_git, valid_branch, worktree_for

SPRINT_BRANCH_PREFIX = "fabrique/sprint-"
TASK_BRANCH_PREFIX = "fabrique/task-"
# The conflict hunks a person reads to decide; the files are always listed in full.
MAX_HUNKS_CHARS = 20_000
MAX_BRANCHES = 200
# Detached worktrees kept per sprint branch, the newest first.
KEEP_SNAPSHOTS = 3
# A snapshot this recent is never removed: its suite may be about to start.
SNAPSHOT_GRACE_SECONDS = 600
# Snapshot paths a command is running in now (``while_active``), with a count.
_ACTIVE: Dict[str, int] = {}
_ACTIVE_LOCK = threading.Lock()


def _resolve(root: Path, worktree: Path, ref: str):
    """``(state, commit)``: the branch's tip read once, and whether HEAD holds it.

    The same commit is checked, merged and recorded, so a delivery that moves
    the task branch meanwhile is neither recorded unmerged nor merged unrecorded.
    """
    if not (valid_branch(ref) and ref.startswith(TASK_BRANCH_PREFIX)):
        return "skip", None
    tip = run_git(["rev-parse", "--verify", "--quiet", f"refs/heads/{ref}^{{commit}}"], root, check=False)
    commit = tip.stdout.strip()
    if tip.returncode != 0 or not commit:
        return "missing", None
    if run_git(["merge-base", "--is-ancestor", commit, "HEAD"], worktree, check=False).returncode == 0:
        return "already", commit
    return "todo", commit


def _snapshot_key(path) -> str:
    return str(Path(path).expanduser().resolve())


def while_active(path, fn, *args):
    """``fn(*args)`` with *path* marked in use, so no integration prunes it meanwhile.

    The daemon runs every ``workspace.execute`` through this: a suite whose
    backend went away keeps its snapshot until it ends.
    """
    key = _snapshot_key(path) if path else None
    if key:
        with _ACTIVE_LOCK:
            _ACTIVE[key] = _ACTIVE.get(key, 0) + 1
    try:
        return fn(*args)
    finally:
        if key:
            with _ACTIVE_LOCK:
                _ACTIVE[key] -= 1
                if not _ACTIVE[key]:
                    del _ACTIVE[key]


def _prunable(path: Path) -> bool:
    with _ACTIVE_LOCK:
        if _snapshot_key(path) in _ACTIVE:
            return False
    try:
        return time.time() - path.stat().st_mtime > SNAPSHOT_GRACE_SECONDS
    except OSError:
        return True


def snapshot_worktree(worktree: Path, root: Path, commit: str, attempt: str = "") -> Path:
    """A detached worktree at *commit* beside the branch's, of this attempt only.

    Each attempt gets its own (``<branch worktree>@<commit>-<attempt>``), so
    no other attempt resets, cleans or removes the files its suite runs on.
    Beyond the newest ``KEEP_SNAPSHOTS``, older ones are removed, never one a
    command is running in (``while_active``) or one younger than
    ``SNAPSHOT_GRACE_SECONDS`` (its suite may not have started yet).
    """
    if not re.fullmatch(r"[0-9a-z]{1,32}", attempt or ""):
        attempt = secrets.token_hex(4)
    path = worktree.parent / f"{worktree.name}@{commit[:12]}-{attempt}"
    run_git(["worktree", "prune"], root, check=False)
    if (path / ".git").exists():
        raise GitError(f"the snapshot of attempt {attempt} already exists")
    run_git(["worktree", "add", "--detach", str(path), commit], root)
    older = sorted(
        (p for p in worktree.parent.glob(f"{worktree.name}@*") if p != path),
        key=lambda p: p.stat().st_mtime,
        reverse=True,
    )
    for old in older[KEEP_SNAPSHOTS - 1 :]:
        if _prunable(old):
            run_git(["worktree", "remove", "--force", str(old)], root, check=False)
    return path


def _head(worktree: Path) -> str:
    return run_git(["rev-parse", "HEAD"], worktree).stdout.strip()


def _conflict(worktree: Path, ref: str, result) -> Dict[str, Any]:
    files = [f for f in run_git(["diff", "--name-only", "--diff-filter=U"], worktree, check=False).stdout.splitlines() if f]
    hunks = run_git(["diff"], worktree, check=False).stdout if files else ""
    return {
        "branch": ref,
        "files": files,
        "hunks": hunks[:MAX_HUNKS_CHARS],
        "hunks_truncated": len(hunks) > MAX_HUNKS_CHARS,
        # git's own words: a conflict, or another reason it refused (an untracked file in the way).
        "message": ((result.stderr or "") + (result.stdout or "")).strip()[-2000:],
    }


def integrate(workspace_root: str, spec: Dict[str, Any]) -> Dict[str, Any]:
    """Merge *spec*'s ``merge`` branches, in order, into *spec*'s ``branch``.

    *spec*: ``branch`` (``fabrique/sprint-<id>``), ``base`` (where it starts
    when it does not exist yet: a branch or a commit; empty for what the
    repository has checked out) and ``merge`` (the task branches in the order
    to merge them).
    """
    branch = str(spec.get("branch") or "")
    if not (valid_branch(branch) and branch.startswith(SPRINT_BRANCH_PREFIX)):
        raise GitError(f"not a sprint branch La Fabrique writes: {branch!r}")
    refs = [str(r) for r in (spec.get("merge") or [])][:MAX_BRANCHES]
    folder = Path(workspace_root).expanduser()
    if not folder.is_dir():
        raise GitError(f"the project folder does not exist on this machine: {workspace_root}")
    root = Path(run_git(["rev-parse", "--show-toplevel"], folder).stdout.strip())
    base = str(spec.get("base") or "HEAD")
    # The base as its remote has it when the local copy is behind (git_base).
    base_commit, base_status = git_base.start_point(root, base)
    worktree = worktree_for(root, branch, base_commit)
    start = _head(worktree)
    merged, already, missing, refused = [], [], [], []
    sources: Dict[str, str] = {}
    conflict, base_merge = None, None
    not_attempted: list = []
    if run_git(["merge-base", "--is-ancestor", base_commit, "HEAD"], worktree, check=False).returncode != 0:
        # A branch started earlier lacks what the base gained since: merged in
        # first, as a pull request's "update branch" does, so it only moves forward.
        ref = str(base_status.get("started_from") or base)
        result = merge_one(worktree, base_commit, f"Merge {ref} into {branch}")
        if result.returncode != 0:
            conflict = _conflict(worktree, ref, result)
            run_git(["merge", "--abort"], worktree, check=False)
            not_attempted, refs = list(refs), []
        else:
            base_merge = {"branch": ref, "source": base_commit, "commit": _head(worktree)}
    for index, ref in enumerate(refs):
        state, tip = _resolve(root, worktree, ref)
        if state == "skip":
            refused.append(ref)
            continue
        if state == "missing":
            missing.append(ref)
            continue
        sources[ref] = tip
        if state == "already":
            already.append(ref)
            continue
        result = merge_one(worktree, tip, f"Merge {ref} into {branch}")
        if result.returncode != 0:
            conflict = _conflict(worktree, ref, result)
            run_git(["merge", "--abort"], worktree, check=False)
            not_attempted = refs[index + 1 :]
            break
        merged.append({"branch": ref, "source": sources[ref], "commit": _head(worktree)})
    commit = _head(worktree)
    return {
        "ok": conflict is None,
        "repository": str(root),
        "branch": branch,
        "base": base,
        "worktree": str(worktree),
        # Where the branch stood before this request, and where it stands now.
        "start": start,
        "commit": commit,
        # Every requested branch merged or already in: nothing missing or refused.
        "complete": conflict is None and not missing and not refused,
        "test_worktree": str(snapshot_worktree(worktree, root, commit, str(spec.get("attempt") or ""))) if conflict is None else None,
        "sources": sources,
        "merged": merged,
        "already_merged": already,
        "missing": missing,
        "refused": refused,
        "conflict": conflict,
        "not_attempted": not_attempted,
        # Where the base stood here and at its remote, and its merge into an older branch.
        "base_status": base_status,
        "base_merge": base_merge,
    }


async def handle_integrate(nc, msg, reply, request_body) -> None:
    """``workspace.git.integrate`` → the integration's observation, or ``{"error": {status, message}}``."""
    data = request_body(msg)
    try:
        body = await asyncio.to_thread(integrate, str(data.get("workspace_root") or ""), data)
    except GitError as e:
        body = {"error": {"status": 409, "message": str(e)}}
    await reply(nc, msg, body)
