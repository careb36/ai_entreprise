"""Merge La Fabrique's branch into a base branch of the local repository.

Nothing is pushed and no remote is needed: a company that keeps its code in a
local repository accepts a task's or a sprint's work here, and decides later,
if ever, to publish it (``git_workspace.publish``).

The merge is computed in a worktree of its own, detached at the base's tip,
so a conflict never reaches the person's files: it is aborted and reported
with the conflicted paths. The base then moves to the result:
``git update-ref`` with the old tip as the expected value when no worktree
has the base checked out, else ``git merge --ff-only`` in the checkout that
has it, and only while that checkout has no uncommitted changes to tracked
files (a person who asked to merge into the branch they are on gets their
files updated the way ``git pull`` would; one with work in progress is told
to commit or stash it first).
"""

from __future__ import annotations

import asyncio
import uuid
from pathlib import Path
from typing import Any, Dict, Optional

from git_workspace import (
    _SHA,
    TASK_BRANCH_PREFIX,
    WORKTREES_DIR,
    GitError,
    _git_dir,
    _out,
    merge_one,
    run_git,
    valid_branch,
)


def _ancestor(root: Path, older: str, newer: str) -> bool:
    return run_git(["merge-base", "--is-ancestor", older, newer], root, check=False).returncode == 0


def checked_out_in(root: Path, branch: str) -> Optional[Path]:
    """The worktree (the person's checkout included) that has *branch* checked out."""
    current: Optional[Path] = None
    for line in _out(["worktree", "list", "--porcelain"], root).splitlines():
        if line.startswith("worktree "):
            current = Path(line[len("worktree "):])
        elif line == f"branch refs/heads/{branch}" and current is not None:
            return current
    return None


def _conflicted(worktree: Path) -> list:
    out = run_git(["diff", "--name-only", "--diff-filter=U"], worktree, check=False).stdout
    return [p for p in out.splitlines() if p][:50]


def _merge_commit(root: Path, base_tip: str, tip: str, message: str) -> str:
    """The commit *base* moves to: *tip* when it is a fast-forward, else a merge."""
    if _ancestor(root, base_tip, tip):
        return tip
    git_dir = _git_dir(root)
    if git_dir is None:
        raise GitError("not a git repository")
    scratch = git_dir / WORKTREES_DIR / f"merge-{uuid.uuid4().hex[:12]}"
    run_git(["worktree", "add", "--detach", str(scratch), base_tip], root)
    try:
        result = merge_one(scratch, tip, message)
        if result.returncode != 0:
            files = _conflicted(scratch)
            run_git(["merge", "--abort"], scratch, check=False)
            named = ", ".join(files) if files else (result.stderr or result.stdout).strip()
            raise GitError(f"the merge conflicts in {named}; nothing was changed")
        return _out(["rev-parse", "HEAD"], scratch)
    finally:
        run_git(["worktree", "remove", "--force", str(scratch)], root, check=False)


def _advance(root: Path, base: str, before: str, after: str) -> None:
    checkout = checked_out_in(root, base)
    if checkout is None:
        run_git(["update-ref", f"refs/heads/{base}", after, before], root)
        return
    dirty = _out(["status", "--porcelain", "--untracked-files=no"], checkout)
    if dirty:
        raise GitError(
            f"{base} is checked out in {checkout} with uncommitted changes: commit or stash them first"
        )
    if _out(["rev-parse", "HEAD"], checkout) != before:
        raise GitError(f"{base} moved while merging; try again")
    run_git(["merge", "--ff-only", after], checkout)


def merge_local(workspace_root: str, spec: Dict[str, Any]) -> Dict[str, Any]:
    """Merge *spec*'s ``branch`` at its pinned ``commit`` into ``base`` (default: the checked-out branch)."""
    branch = str(spec.get("branch") or "")
    if not (valid_branch(branch) and branch.startswith(TASK_BRANCH_PREFIX)):
        raise GitError(f"not a branch name La Fabrique writes: {branch!r}")
    root = Path(_out(["rev-parse", "--show-toplevel"], Path(workspace_root).expanduser()))
    tip = _out(["rev-parse", f"refs/heads/{branch}"], root)
    pinned = str(spec.get("commit") or "")
    if pinned and not _SHA.match(pinned):
        raise GitError(f"not a commit: {pinned!r}")
    if pinned and pinned != tip:
        raise GitError(f"{branch} has moved on to {tip[:7]} since {pinned[:7]}: merge the latest one")
    base = str(spec.get("base") or "")
    if not base:
        base = run_git(["symbolic-ref", "--quiet", "--short", "HEAD"], root, check=False).stdout.strip()
    if not valid_branch(base) or base.startswith(TASK_BRANCH_PREFIX):
        raise GitError(f"not a branch to merge into: {base!r}")
    before = _out(["rev-parse", f"refs/heads/{base}"], root)
    answer = {"ok": True, "branch": branch, "base": base, "commit": tip, "before": before}
    if _ancestor(root, tip, before):
        return {**answer, "after": before, "already": True, "fast_forward": False}
    message = str(spec.get("message") or f"Merge {branch} into {base}")[:2000]
    after = _merge_commit(root, before, tip, message)
    _advance(root, base, before, after)
    return {**answer, "after": after, "already": False, "fast_forward": after == tip}


async def handle_merge_local(nc, msg, reply, request_body) -> None:
    """``workspace.git.merge`` → the base before and after, or ``{"error": {status, message}}``."""
    data = request_body(msg)
    try:
        body = await asyncio.to_thread(merge_local, str(data.get("workspace_root") or ""), data)
    except GitError as e:
        body = {"error": {"status": 409, "message": str(e)}}
    await reply(nc, msg, body)
