"""A sprint's task branches joined into one branch, the way a release is cut.

With the company's Git workflow on, each task is a branch
(``fabrique/task-<id>``, ``git_workspace.deliver``). When the sprint closes
the backend asks for ``workspace.git.integrate``: the branch
``fabrique/sprint-<id>`` is started from the company's base (or the commit
the repository has checked out), in a worktree under the repository's git
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
"""

from __future__ import annotations

import asyncio
from pathlib import Path
from typing import Any, Dict

from git_workspace import GitError, merge_one, merge_state, run_git, valid_branch, worktree_for

SPRINT_BRANCH_PREFIX = "fabrique/sprint-"
TASK_BRANCH_PREFIX = "fabrique/task-"
# The conflict hunks a person reads to decide; the files are always listed in full.
MAX_HUNKS_CHARS = 20_000
MAX_BRANCHES = 200


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
    worktree = worktree_for(root, branch, base)
    start = _head(worktree)
    merged, already, missing, refused = [], [], [], []
    conflict = None
    not_attempted: list = []
    for index, ref in enumerate(refs):
        state = merge_state(root, worktree, ref) if ref.startswith(TASK_BRANCH_PREFIX) else "skip"
        if state == "skip":
            refused.append(ref)
            continue
        if state == "missing":
            missing.append(ref)
            continue
        if state == "already":
            already.append(ref)
            continue
        result = merge_one(worktree, ref, f"Merge {ref} into {branch}")
        if result.returncode != 0:
            conflict = _conflict(worktree, ref, result)
            run_git(["merge", "--abort"], worktree, check=False)
            not_attempted = refs[index + 1 :]
            break
        merged.append({"branch": ref, "commit": _head(worktree)})
    return {
        "ok": conflict is None,
        "repository": str(root),
        "branch": branch,
        "base": base,
        "worktree": str(worktree),
        # Where the branch stood before this request, and where it stands now.
        "start": start,
        "commit": _head(worktree),
        "merged": merged,
        "already_merged": already,
        "missing": missing,
        "refused": refused,
        "conflict": conflict,
        "not_attempted": not_attempted,
    }


async def handle_integrate(nc, msg, reply, request_body) -> None:
    """``workspace.git.integrate`` → the integration's observation, or ``{"error": {status, message}}``."""
    data = request_body(msg)
    try:
        body = await asyncio.to_thread(integrate, str(data.get("workspace_root") or ""), data)
    except GitError as e:
        body = {"error": {"status": 409, "message": str(e)}}
    await reply(nc, msg, body)
