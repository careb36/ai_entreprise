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
  repository's metadata a hook or a config could hide in (``config``,
  ``hooks``, ``info``, of the common git dir and of the worktree's own), as
  they were before the run.
- ``workspace.console.collect`` ``{workspace_root: <worktree>, head, guard}``:
  each file changed since ``head`` (committed by the CLI or not, tracked or
  new) with its ``change``, ``bytes``, the ``sha256`` read back and its text,
  or why it is not delivered (``refused``: outside the worktree, VCS metadata,
  a symlink, a file that may hold secrets; ``binary``, ``too_large``,
  ``deleted``, ``past_bound``). Then the worktree is put back at ``head``:
  the backend delivers the texts through ``workspace.generate``, which writes
  them, reads them back and commits them on the task's branch as any
  delivery's. A change to the repository's metadata while the CLI ran is
  reported (``refused``) and nothing is delivered.

The CLI runs in the sandbox with the worktree as its only folder. A linked
worktree's ``.git`` is a pointer into the host repository's git dir, which no
sandbox mounts (a git run there finds no repository), and mounting it would
hand the CLI the person's config and hooks. So ``prepare`` replaces the
pointer with a private repository inside the worktree (``private_repository``:
a shallow bare clone of the task's branch at ``head``, no remote, nothing
naming the host), which the CLI may diff, stage or commit in. ``collect``
discards it with file operations (never following a symlink) and writes the
pointer back before any host git runs in the worktree; a ``.git`` that is no
longer a folder refuses the run. ``git_workspace.worktree_for`` does the same
for a private repository a run never collected, before any git there. The
CLI's commits are read from the private repository's files as text
(``private_head``, informative only); what it changed is the worktree
against ``head`` in the real git dir. Every host git command here names
``--git-dir`` and ``--work-tree`` from known paths (``.git`` is never read),
without the inherited ``GIT_*`` environment, with ``core.fsmonitor`` off, no
hooks, no submodule recursion, and ``--no-ext-diff --no-textconv`` on diff.
The person's checkout is not guarded: no sandbox mounts it.
"""

from __future__ import annotations

import asyncio
import hashlib
import json
import os
import re
import stat
import subprocess
from pathlib import Path
from typing import Any, Callable, Dict, List, Optional

import git_workspace
import project_lock
import workspace_read
from git_workspace import admin_dir, pointer_problem, restore_pointer

MAX_FILES = 200
MAX_FILE_BYTES = 512 * 1024
# The reply's bound, as encoded (``json.dumps`` escapes non-ASCII text, up to
# six bytes a character): a NATS message holds 1 MB by default.
MAX_REPLY_BYTES = 700 * 1024
_SHA = re.compile(r"^[0-9a-f]{40}([0-9a-f]{24})?$")
# Where a hook, an alias or a hooks path could be planted in the repository.
GUARDED = ("config", "config.worktree", "hooks", "info")


def _sha(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


# Every host git run in a worktree the CLI had: nothing it could plant runs.
SAFE_CONFIG = (
    "-c", "core.fsmonitor=false", "-c", "core.hooksPath=/dev/null",
    "-c", "submodule.recurse=false", "-c", "core.untrackedCache=false",
)


def git(worktree: Path, admin: Path, args: List[str], check: bool = True) -> subprocess.CompletedProcess:
    """``git`` on *admin* and *worktree* named explicitly, hardened (``SAFE_CONFIG``)."""
    return _hardened([f"--git-dir={admin}", f"--work-tree={worktree}"], worktree, args, check)


def _hardened(prefix: List[str], cwd: Path, args: List[str], check: bool = True) -> subprocess.CompletedProcess:
    env = {k: v for k, v in os.environ.items() if not k.startswith("GIT_")}
    env.update({"GIT_TERMINAL_PROMPT": "0", "LC_ALL": "C"})
    try:
        result = subprocess.run(  # noqa: S603 - fixed argv, no shell.
            ["git", *prefix, *SAFE_CONFIG, *args],
            cwd=str(cwd), env=env, capture_output=True, text=True,
            timeout=git_workspace.GIT_TIMEOUT_SECONDS, check=False,
        )
    except FileNotFoundError as e:
        raise git_workspace.GitError("git is not installed on this machine") from e
    except subprocess.TimeoutExpired as e:
        raise git_workspace.GitError(f"git {args[0]} took too long") from e
    if check and result.returncode != 0:
        raise git_workspace.GitError((result.stderr or result.stdout).strip() or f"git {args[0]} failed")
    return result


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


def guard(worktree: Path, admin: Path) -> Dict[str, str]:
    """What the CLI must leave alone: the repository's metadata and the worktree's own git dir.

    Defence in depth: the sandbox mounts only the worktree, so neither is
    reachable from it. The person's checkout is not guarded for the same
    reason, and they may keep working in it while the CLI runs."""
    return {"metadata": _tree_digest(worktree.parent.parent), "worktree_git": _tree_digest(admin)}


def _put_back(worktree: Path, admin: Path, head: str) -> None:
    """The worktree at *head*, its untracked files and nested repositories gone
    (ignored ones stay): it is La Fabrique's, never the person's."""
    git(worktree, admin, ["reset", "--hard", "-q", head])
    git(worktree, admin, ["clean", "-ffdq"])


def private_repository(worktree: Path, branch: str, head: str) -> None:
    """Give the worktree a Git of its own for the run, inside the folder the sandbox mounts.

    A linked worktree's ``.git`` names the host repository's git dir, which no
    sandbox mounts, so a git the CLI runs there finds no repository; mounting
    the person's git dir would hand the CLI their config and hooks. Instead
    the pointer is replaced by a shallow bare clone of *branch* at *head*
    (``core.bare`` off, no remote, its index built from ``HEAD``): the CLI sees
    the task's commit and may diff, stage or commit against it, and nothing in
    it names or reaches the host repository. ``collect`` discards it with
    plain file operations, before any host git runs, and writes the pointer
    back; no host git ever runs in it after the CLI had it."""
    dotgit = worktree / ".git"
    common = worktree.parent.parent.resolve()
    dotgit.unlink()
    try:
        _hardened([], worktree.parent, [
            "-c", "protocol.file.allow=always", "clone", "-q", "--bare", "--depth", "1",
            "--single-branch", "--no-tags", "--branch", branch, common.as_uri(), str(dotgit),
        ])
        git(worktree, dotgit, ["config", "core.bare", "false"])
        git(worktree, dotgit, ["remote", "remove", "origin"])
        if git(worktree, dotgit, ["rev-parse", "HEAD"]).stdout.strip() != head:
            raise git_workspace.GitError("the private repository is not at the task's head")
        git(worktree, dotgit, ["reset", "-q"])  # The index from HEAD; no file changes.
    except BaseException:
        restore_pointer(worktree, git_workspace.admin_dir(worktree))
        raise


_REF = re.compile(r"^refs/heads/[A-Za-z0-9][A-Za-z0-9/_.-]{0,199}$")


def _small_file(dotgit: Path, relative: str) -> Optional[str]:
    """A file of the private repository read as text, no symlink, no git; else ``None``."""
    path = dotgit / relative
    try:
        if os.path.realpath(path) != os.path.join(os.path.realpath(dotgit), *relative.split("/")):
            return None
        if not stat.S_ISREG(os.lstat(path).st_mode) or os.lstat(path).st_size > 64 * 1024:
            return None
        return path.read_text(encoding="utf-8")
    except (OSError, UnicodeDecodeError):
        return None


def private_head(dotgit: Path) -> Optional[str]:
    """The commit the private repository's ``HEAD`` names, read from its files
    (never by running git in it): informative only, the CLI wrote them."""
    text = (_small_file(dotgit, "HEAD") or "").strip()
    if _SHA.match(text):
        return text
    ref = text[len("ref: "):] if text.startswith("ref: ") else ""
    if not _REF.match(ref) or ".." in ref:
        return None
    loose = (_small_file(dotgit, ref) or "").strip()
    if _SHA.match(loose):
        return loose
    for line in (_small_file(dotgit, "packed-refs") or "").splitlines():
        sha, _, name = line.partition(" ")
        if name == ref and _SHA.match(sha):
            return sha
    return None


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
            admin = admin_dir(worktree)
            # A pointer a run left behind (one never collected) is put back first.
            restored = pointer_problem(worktree, admin)
            if restored:
                restore_pointer(worktree, admin)
            _put_back(worktree, admin, "HEAD")
            merged = git_workspace.merge_branches(root, worktree, [str(b) for b in spec.get("merge") or []])
            head = git(worktree, admin, ["rev-parse", "HEAD"]).stdout.strip()
            _put_back(worktree, admin, head)
            taken = guard(worktree, admin)
            private_repository(worktree, branch, head)
            return {
                "ok": True, "repository": str(root), "worktree": str(worktree), "branch": branch,
                "head": head, "merged": merged, "guard": taken,
                "pointer_restored": restored, "private_git": True,
            }
    except (OSError, git_workspace.GitError) as exc:
        return {"ok": False, "error": str(exc)}


def _changed_paths(worktree: Path, admin: Path, head: str) -> Dict[str, str]:
    """``{path: "added"|"modified"|"deleted"}`` for every file that differs from *head*."""
    out = git(worktree, admin, [
        "diff", "--name-status", "-z", "--no-renames", "--no-ext-diff", "--no-textconv",
        "--ignore-submodules=all", head, "--",
    ]).stdout
    fields = out.split("\0")
    changes: Dict[str, str] = {}
    for status, path in zip(fields[0::2], fields[1::2]):
        if path:
            changes[path] = {"A": "added", "D": "deleted"}.get(status[:1], "modified")
    untracked = git(worktree, admin, ["ls-files", "--others", "--exclude-standard", "-z"]).stdout
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


def _encoded(value: Any) -> int:
    """Its size in the reply, as the daemon encodes it (``json.dumps``, ASCII)."""
    return len(json.dumps(value))


def _bounded(files: List[Dict[str, Any]], envelope: Dict[str, Any]) -> tuple:
    """*files* within ``MAX_REPLY_BYTES`` once encoded: texts past it dropped
    (``past_bound``), then entries if the listing alone is too long."""
    budget = MAX_REPLY_BYTES - _encoded(envelope) - 1024
    kept: List[Dict[str, Any]] = []
    used, truncated = 0, False
    for entry in files:
        size = _encoded(entry) + 2
        if used + size > budget and "content" in entry:
            entry = {**{k: v for k, v in entry.items() if k != "content"}, "status": "past_bound",
                     "reason": "past the reply's bound"}
            size = _encoded(entry) + 2
        if used + size > budget:
            truncated = True
            break
        kept.append(entry)
        used += size
    return kept, truncated


def collect(workspace_root: str, data: Dict[str, Any], resolve: Callable[[str, str], Optional[Path]]) -> Dict[str, Any]:
    """What the CLI changed in the worktree since ``head``, then the worktree put back at ``head``."""
    head = str(data.get("head") or "")
    if not _SHA.match(head):
        return {"ok": False, "error": "a commit id is required as head"}
    worktree = Path(workspace_root).expanduser()
    try:
        admin = admin_dir(worktree)
    except (OSError, git_workspace.GitError):
        return {"ok": False, "error": "not a task worktree La Fabrique made"}
    try:
        with project_lock.lock_for(str(worktree)):
            dotgit = worktree / ".git"
            problem = None
            if dotgit.is_symlink() or not dotgit.is_dir():
                problem = "the worktree's private .git was replaced"
            head_after = None if problem else private_head(dotgit)
            # The private repository is discarded and the pointer written back
            # (file operations) before any host git runs in this worktree.
            restore_pointer(worktree, admin)
            if problem:
                # The next prepare puts the worktree back.
                return {
                    "ok": False, "worktree": str(worktree), "head": head, "files": [], "truncated": False,
                    "refused": [f"{problem} while the CLI ran; nothing is read from the worktree"],
                    "restored": False,
                }
            before = data.get("guard") if isinstance(data.get("guard"), dict) else {}
            after = guard(worktree, admin)
            refused: List[str] = []
            if before.get("metadata") != after["metadata"] or before.get("worktree_git") != after["worktree_git"]:
                refused.append("the repository's metadata (config, hooks or info) changed while the CLI ran; check it by hand")
            changes = _changed_paths(worktree, admin, head)
            files: List[Dict[str, Any]] = []
            for path, change in sorted(changes.items())[:MAX_FILES]:
                entry = _describe(worktree, path, change, resolve)
                if refused:
                    entry.pop("content", None)
                files.append(entry)
            reply = {
                "ok": not refused, "worktree": str(worktree), "head": head, "head_after": head_after,
                "committed_by_cli": bool(head_after) and head_after != head, "files": [], "truncated": len(changes) > MAX_FILES,
                "refused": refused, "restored": True,
            }
            files, cut = _bounded(files, reply)
            reply.update(files=files, truncated=reply["truncated"] or cut)
            # Only once the reply is known to fit is the worktree put back.
            _put_back(worktree, admin, head)
            return reply
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
