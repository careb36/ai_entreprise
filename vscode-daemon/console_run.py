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

The CLI runs in the sandbox with the worktree as its only writable folder,
so the one piece of Git it can reach is the worktree's ``.git`` file, the
pointer to its git dir. Rewritten (to an attacker's git dir, or replaced by a
``.git`` folder), any host git run in the worktree would read that config:
``core.fsmonitor``, hooks, ``diff.external`` or a textconv would run on the
person's computer. So ``collect`` checks the pointer before any git command,
against the git dir La Fabrique made (found from the common dir, which the
sandbox never mounts); a pointer that differs refuses the run with no git
run in the worktree (the pointer is rewritten, a plain file write, and the
next ``prepare`` puts the worktree back). And every git command here names
``--git-dir`` and ``--work-tree`` from those known paths (``.git`` is never
read), without the inherited ``GIT_*`` environment, with ``core.fsmonitor``
off, no hooks, no submodule recursion, and ``--no-ext-diff --no-textconv`` on
diff. The one remote call, the fetch of the base before a new task branch
starts (``git_base``), runs in the person's checkout before the CLI runs,
never in a worktree a CLI had. The person's checkout is not guarded: no
sandbox mounts it.
"""

from __future__ import annotations

import asyncio
import hashlib
import json
import os
import re
import shutil
import stat
import subprocess
from pathlib import Path
from typing import Any, Callable, Dict, List, Optional

import git_workspace
import project_lock
import workspace_read

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


def admin_dir(worktree: Path) -> Path:
    """The worktree's own git dir, found from the common dir (never from ``.git``).

    A task worktree is ``<common>/fabrique-worktrees/<name>`` and git records
    it in ``<common>/worktrees/<id>/gitdir``: both outside what the sandbox
    mounts."""
    if worktree.parent.name != git_workspace.WORKTREES_DIR:
        raise git_workspace.GitError("not a task worktree La Fabrique made")
    common = worktree.parent.parent
    real = worktree.resolve()
    admins = common / "worktrees"
    for admin in sorted(admins.iterdir()) if admins.is_dir() else []:
        try:
            recorded = Path((admin / "gitdir").read_text(encoding="utf-8").strip())
        except (OSError, UnicodeDecodeError):
            continue
        if recorded.name == ".git" and recorded.parent.resolve() == real:
            return admin
    raise git_workspace.GitError("not a task worktree La Fabrique made")


def pointer_problem(worktree: Path, admin: Path) -> Optional[str]:
    """Why the worktree's ``.git`` is not the pointer to *admin*, else ``None``."""
    dotgit = worktree / ".git"
    try:
        mode = os.lstat(dotgit).st_mode
        if not stat.S_ISREG(mode):
            return "the worktree's .git is no longer the file La Fabrique wrote"
        text = dotgit.read_text(encoding="utf-8")
    except (OSError, UnicodeDecodeError):
        return "the worktree's .git cannot be read"
    lines = text.splitlines()
    if len(lines) != 1 or not lines[0].startswith("gitdir: "):
        return "the worktree's .git was rewritten"
    named = Path(lines[0][len("gitdir: "):])
    named = named if named.is_absolute() else worktree / named
    if os.path.realpath(named) != os.path.realpath(admin):
        return "the worktree's .git points at another git dir"
    return None


def restore_pointer(worktree: Path, admin: Path) -> None:
    """The worktree's ``.git`` written back as git writes it: a plain file write, no git."""
    dotgit = worktree / ".git"
    if dotgit.is_dir() and not dotgit.is_symlink():
        shutil.rmtree(dotgit)
    elif os.path.lexists(dotgit):
        dotgit.unlink()
    dotgit.write_text(f"gitdir: {admin}\n", encoding="utf-8")


def git(worktree: Path, admin: Path, args: List[str], check: bool = True) -> subprocess.CompletedProcess:
    """``git`` on *admin* and *worktree* named explicitly, hardened (``SAFE_CONFIG``)."""
    env = {k: v for k, v in os.environ.items() if not k.startswith("GIT_")}
    env.update({"GIT_TERMINAL_PROMPT": "0", "LC_ALL": "C"})
    try:
        result = subprocess.run(  # noqa: S603 - fixed argv, no shell.
            ["git", f"--git-dir={admin}", f"--work-tree={worktree}", *SAFE_CONFIG, *args],
            cwd=str(worktree), env=env, capture_output=True, text=True,
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


def _task_worktree(workspace_root: str, spec: Dict[str, Any]) -> tuple:
    branch = str(spec.get("branch") or "")
    if not git_workspace.valid_branch(branch) or not branch.startswith(git_workspace.TASK_BRANCH_PREFIX):
        raise git_workspace.GitError(f"not a task branch La Fabrique writes: {branch!r}")
    folder = Path(workspace_root).expanduser()
    if not folder.is_dir():
        raise git_workspace.GitError(f"the project is not a folder on this machine: {workspace_root}")
    root = Path(git_workspace._out(["rev-parse", "--show-toplevel"], folder))
    # A new branch starts from the base as its remote has it (git_base).
    start, started = git_workspace.fresh_start(root, branch, str(spec.get("base") or "HEAD"))
    return root, git_workspace.worktree_for(root, branch, start), branch, started


def prepare(workspace_root: str, data: Dict[str, Any], resolve=None) -> Dict[str, Any]:
    """The task's worktree, clean at its ``HEAD``, and the guard taken before the run."""
    spec = data.get("git") if isinstance(data.get("git"), dict) else {}
    try:
        root, worktree, branch, started = _task_worktree(workspace_root, spec)
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
            return {
                "ok": True, "repository": str(root), "worktree": str(worktree), "branch": branch,
                "head": head, "merged": merged, "guard": guard(worktree, admin),
                "pointer_restored": restored, **started,
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
            problem = pointer_problem(worktree, admin)
            if problem:
                # No git runs in this worktree: its pointer is written back
                # (a file write) and the next prepare puts the worktree back.
                restore_pointer(worktree, admin)
                return {
                    "ok": False, "worktree": str(worktree), "head": head, "files": [], "truncated": False,
                    "refused": [f"{problem} while the CLI ran; nothing is read from the worktree"],
                    "restored": False,
                }
            head_after = git(worktree, admin, ["rev-parse", "HEAD"]).stdout.strip()
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
                "committed_by_cli": head_after != head, "files": [], "truncated": len(changes) > MAX_FILES,
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
