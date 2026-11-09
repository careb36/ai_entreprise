"""A console producer's run: the task's worktree prepared, then what the CLI changed in it.

Taken from Aixle Flow (Apache 2.0, ``app/services/container_strategies/``):
a terminal coding agent runs headless in a workspace of its own, and what it
produced is read from that workspace afterwards, never from what it printed.
Flow gives each session a container with ``/workspace``; here the workspace
is the task's Git worktree (``git_workspace.worktree_for``, the one a
delivery commits in) and the run goes through ``workspace.execute`` with the
company's sandbox and command policy, so this module only prepares and reads.

- ``workspace.console.prepare`` ``{workspace_root, git}``: the task's worktree,
  made like a delivery's (the prerequisites' branches merged), put back at its
  ``HEAD``; the answer carries ``head`` and a ``guard``: the sha256 of the
  repository's metadata a hook or a config could hide in, and of the person's
  checkout status, as they were before the run.
- ``workspace.console.collect`` ``{workspace_root: <worktree>, head, guard}``:
  each file changed since ``head`` (committed by the CLI or not, tracked or
  new) with its ``change``, ``bytes``, the ``sha256`` read back and its text,
  or why it is not delivered (``refused``: outside the worktree, VCS metadata,
  a symlink, a file that may hold secrets; ``binary``, ``too_large``,
  ``deleted``). Then the worktree is put back at ``head``: the backend delivers
  the texts through ``workspace.generate``, which writes them, reads them back
  and commits them on the task's branch as any delivery's. A change to the
  repository's metadata or to the person's checkout while the CLI ran is
  reported (``refused``) and nothing is delivered.
"""

from __future__ import annotations

import asyncio
import hashlib
import os
import re
from pathlib import Path
from typing import Any, Callable, Dict, List, Optional

import git_workspace
import project_lock
import workspace_read

MAX_FILES = 200
MAX_FILE_BYTES = 512 * 1024
MAX_REPLY_BYTES = 700 * 1024  # A NATS message holds 1 MB by default.
_SHA = re.compile(r"^[0-9a-f]{40}([0-9a-f]{24})?$")
# Where a hook, an alias or a hooks path could be planted in the repository.
GUARDED = ("config", "config.worktree", "hooks", "info")


def _sha(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def _common_dir(worktree: Path) -> Path:
    common = Path(git_workspace._out(["rev-parse", "--git-common-dir"], worktree))
    return common if common.is_absolute() else (worktree / common).resolve()


def _tree_digest(base: Path) -> str:
    """The sha256 of every file under the guarded parts of *base* (names and bytes)."""
    digest = hashlib.sha256()
    for name in GUARDED:
        top = base / name
        if top.is_dir():
            paths = sorted(p for p in top.rglob("*") if p.is_file())
        else:
            paths = [top] if top.is_file() else []
        for path in paths:
            digest.update(str(path.relative_to(base)).encode() + b"\0")
            try:
                digest.update(path.read_bytes())
            except OSError:
                digest.update(b"<unreadable>")
    return digest.hexdigest()


def guard(worktree: Path) -> Dict[str, str]:
    """What the CLI must leave alone: the repository's metadata, the worktree's own git dir, the person's checkout."""
    common = _common_dir(worktree)
    own = Path(git_workspace._out(["rev-parse", "--absolute-git-dir"], worktree))
    status = b""
    root = common.parent if common.name == ".git" else None
    if root is not None:
        status = git_workspace.run_git(["status", "--porcelain=v1", "-z", "--untracked-files=all"], root, check=False).stdout.encode()
    return {"metadata": _tree_digest(common), "worktree_git": _tree_digest(own), "checkout": _sha(status)}


def _put_back(worktree: Path, head: str) -> None:
    """The worktree at *head*, its untracked files gone (ignored ones stay): it is La Fabrique's, never the person's."""
    git_workspace.run_git(["reset", "--hard", "-q", head], worktree)
    git_workspace.run_git(["clean", "-fdq"], worktree)


def _task_worktree(workspace_root: str, spec: Dict[str, Any]) -> tuple:
    branch = str(spec.get("branch") or "")
    if not git_workspace.valid_branch(branch) or not branch.startswith(git_workspace.TASK_BRANCH_PREFIX):
        raise git_workspace.GitError(f"not a task branch La Fabrique writes: {branch!r}")
    folder = Path(workspace_root).expanduser()
    if not folder.is_dir():
        raise git_workspace.GitError(f"the project is not a folder on this machine: {workspace_root}")
    root = Path(git_workspace._out(["rev-parse", "--show-toplevel"], folder))
    worktree = git_workspace.worktree_for(root, branch, str(spec.get("base") or "HEAD"))
    return root, worktree, branch


def prepare(workspace_root: str, data: Dict[str, Any], resolve=None) -> Dict[str, Any]:
    """The task's worktree, clean at its ``HEAD``, and the guard taken before the run."""
    spec = data.get("git") if isinstance(data.get("git"), dict) else {}
    try:
        root, worktree, branch = _task_worktree(workspace_root, spec)
        with project_lock.lock_for(str(worktree)):
            merged = git_workspace.merge_branches(root, worktree, [str(b) for b in spec.get("merge") or []])
            head = git_workspace._out(["rev-parse", "HEAD"], worktree)
            _put_back(worktree, head)
            return {
                "ok": True, "repository": str(root), "worktree": str(worktree), "branch": branch,
                "head": head, "merged": merged, "guard": guard(worktree),
            }
    except (OSError, git_workspace.GitError) as exc:
        return {"ok": False, "error": str(exc)}


def _changed_paths(worktree: Path, head: str) -> Dict[str, str]:
    """``{path: "added"|"modified"|"deleted"}`` for every file that differs from *head*."""
    out = git_workspace.run_git(["diff", "--name-status", "-z", "--no-renames", head, "--"], worktree).stdout
    fields = out.split("\0")
    changes: Dict[str, str] = {}
    for status, path in zip(fields[0::2], fields[1::2]):
        if path:
            changes[path] = {"A": "added", "D": "deleted"}.get(status[:1], "modified")
    untracked = git_workspace.run_git(["ls-files", "--others", "--exclude-standard", "-z"], worktree).stdout
    for path in untracked.split("\0"):
        if path:
            changes[path] = "added"
    return changes


def _describe(worktree: Path, path: str, change: str, resolve: Callable[[str, str], Optional[Path]]) -> Dict[str, Any]:
    entry: Dict[str, Any] = {"path": path, "change": change}
    lexical = worktree / path
    target = resolve(str(worktree), path)
    if target is None or workspace_read._inside(worktree.resolve(), lexical.parent) is None:
        return {**entry, "status": "refused", "reason": "outside the worktree or into VCS metadata"}
    if change == "deleted":
        return {**entry, "status": "deleted", "reason": "a deletion is not delivered"}
    if lexical.is_symlink():
        return {**entry, "status": "refused", "reason": "a symlink"}
    if workspace_read.is_sensitive(worktree.resolve(), target):
        return {**entry, "status": "refused", "reason": "a file that may hold secrets is never read"}
    try:
        size = lexical.stat().st_size
        if size > MAX_FILE_BYTES:
            return {**entry, "status": "too_large", "bytes": size}
        data = lexical.read_bytes()
    except OSError as exc:
        return {**entry, "status": "unreadable", "reason": str(exc)}
    entry.update({"bytes": len(data), "sha256": _sha(data)})
    try:
        return {**entry, "status": "changed", "content": data.decode("utf-8")}
    except UnicodeDecodeError:
        return {**entry, "status": "binary"}


def collect(workspace_root: str, data: Dict[str, Any], resolve: Callable[[str, str], Optional[Path]]) -> Dict[str, Any]:
    """What the CLI changed in the worktree since ``head``, then the worktree put back at ``head``."""
    head = str(data.get("head") or "")
    if not _SHA.match(head):
        return {"ok": False, "error": "a commit id is required as head"}
    worktree = Path(workspace_root).expanduser()
    if not (worktree / ".git").is_file() or git_workspace.WORKTREES_DIR not in worktree.parts:
        return {"ok": False, "error": "not a task worktree La Fabrique made"}
    try:
        with project_lock.lock_for(str(worktree)):
            head_after = git_workspace._out(["rev-parse", "HEAD"], worktree)
            before = data.get("guard") if isinstance(data.get("guard"), dict) else {}
            after = guard(worktree)
            refused: List[str] = []
            if before.get("metadata") != after["metadata"] or before.get("worktree_git") != after["worktree_git"]:
                refused.append("the repository's metadata (config, hooks or info) changed while the CLI ran; check it by hand")
            if before.get("checkout") != after["checkout"]:
                refused.append("the person's checkout changed while the CLI ran")
            changes = _changed_paths(worktree, head)
            files: List[Dict[str, Any]] = []
            total = 0
            for path, change in sorted(changes.items())[:MAX_FILES]:
                entry = _describe(worktree, path, change, resolve)
                total += len(entry.get("content") or "")
                if total > MAX_REPLY_BYTES and "content" in entry:
                    entry = {**{k: v for k, v in entry.items() if k != "content"}, "status": "too_large", "reason": "past the reply's bound"}
                if refused:
                    entry.pop("content", None)
                files.append(entry)
            _put_back(worktree, head)
            return {
                "ok": not refused, "worktree": str(worktree), "head": head, "head_after": head_after,
                "committed_by_cli": head_after != head, "files": files, "truncated": len(changes) > MAX_FILES,
                "refused": refused, "restored": True,
            }
    except (OSError, git_workspace.GitError) as exc:
        return {"ok": False, "error": str(exc)}


async def _serve(nc, msg, reply, request_body, resolve, handler) -> None:
    body = request_body(msg)
    root = body.get("workspace_root")
    if not isinstance(root, str) or not root.strip():
        result: Dict[str, Any] = {"ok": False, "error": "workspace_root is required"}
    else:
        result = await asyncio.to_thread(handler, os.path.expanduser(root), body, resolve)
    await reply(nc, msg, result)


async def handle_prepare(nc, msg, reply, request_body, resolve) -> None:
    await _serve(nc, msg, reply, request_body, resolve, prepare)


async def handle_collect(nc, msg, reply, request_body, resolve) -> None:
    await _serve(nc, msg, reply, request_body, resolve, collect)


HANDLERS = (
    ("workspace.console.prepare", handle_prepare),
    ("workspace.console.collect", handle_collect),
)
