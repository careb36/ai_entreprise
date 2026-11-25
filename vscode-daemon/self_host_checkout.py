"""The self-hosting snapshot, made on this computer (issue #701, PROTOCOL.md §5.9).

An owner action started from the app (``docs/design/owner-actions.md``) runs
the self-hosting harness's ``checkout`` step here, because this daemon is the
only component that touches the user's files. ``workspace.self_host.checkout``
takes one field, ``name``: a folder name, never a path. Where it goes and
what is copied are this machine's own settings, never the request's:

- ``FABRIQUE_SELF_HOST_SOURCE``: the repository copied. Default: the La
  Fabrique repository this daemon runs from (the parent of ``vscode-daemon/``)
  when it is a git repository.
- ``FABRIQUE_SELF_HOST_ROOT``: the folder the snapshots go under. Default
  ``~/fabrique-self-host``.

The copy is the harness's (``scripts/self_host/self_host.py``,
``step_checkout``): the files git tracks or would track
(``ls-files --cached --others --exclude-standard``), so ignored files such as
``.env`` stay out, committed as ``main`` of a fresh repository. Never the live
checkout and never a ``git worktree`` of it. A symlink is not followed: a
link is skipped. Refused (400): a name that is not one plain folder name; a
target outside the root once resolved (a symlinked folder); the source or a
folder inside it, or a root inside the source; a non-empty folder that is not
an earlier snapshot. An earlier snapshot is answered as it is
(``created: false``), as the harness does.

The reply is what this daemon observed: the target's ``path``, ``created``,
``head`` (the snapshot's commit), ``source_head`` and ``files`` copied.
"""

from __future__ import annotations

import asyncio
import os
import re
import shutil
from pathlib import Path
from typing import Any, Dict

from git_workspace import GitError, run_git

SUBJECT = "workspace.self_host.checkout"
SOURCE_ENV = "FABRIQUE_SELF_HOST_SOURCE"
ROOT_ENV = "FABRIQUE_SELF_HOST_ROOT"
NAME = re.compile(r"^[a-z0-9][a-z0-9._-]{0,62}$")
IDENTITY = ["-c", "user.name=La Fabrique self-hosting", "-c", "user.email=self-host@localhost"]
COPY_TIMEOUT_SECONDS = 300
MESSAGE = "La Fabrique, as the self-hosting company's project"


TRAILER = "Source-Commit"


class Refused(ValueError):
    """A request this daemon will not carry out, and why (a 400)."""


class Conflict(RuntimeError):
    """An earlier snapshot that cannot be refreshed without losing work (a 409)."""


def source_repository() -> Path:
    configured = os.getenv(SOURCE_ENV, "").strip()
    source = Path(configured).expanduser() if configured else Path(__file__).resolve().parents[1]
    source = source.resolve()
    if not (source / ".git").exists():
        raise Refused(f"the self-hosting source {source} is not a git repository ({SOURCE_ENV})")
    return source


def snapshots_root() -> Path:
    configured = os.getenv(ROOT_ENV, "").strip()
    return Path(configured or "~/fabrique-self-host").expanduser().resolve()


def _inside(path: Path, folder: Path) -> bool:
    return path == folder or folder in path.parents


def target_for(name: Any, root: Path, source: Path) -> Path:
    """``<root>/<name>``, or ``Refused``: the request names a folder, never a path."""
    if not isinstance(name, str) or not NAME.match(name) or name in (".", ".."):
        raise Refused("name must be one folder name: lowercase letters, digits, '.', '_' or '-'")
    if _inside(root, source):
        raise Refused(f"the snapshots' root {root} is inside the source repository")
    target = (root / name).resolve()
    if target.parent != root:
        raise Refused(f"{name} resolves outside {root}")
    if _inside(target, source) or _inside(source, target):
        raise Refused("the snapshot must be outside the source repository")
    return target


def _head(root: Path) -> str:
    return run_git([*IDENTITY, "rev-parse", "HEAD"], root).stdout.strip()


def _copy_source(source: Path, target: Path) -> int:
    """Copy what git tracks or would track in *source* into *target*; never a link."""
    listed = run_git(["ls-files", "-z", "--cached", "--others", "--exclude-standard"], source)
    copied = 0
    for relative in filter(None, listed.stdout.split("\0")):
        origin = source / relative
        if origin.is_symlink() or not origin.is_file():
            continue  # a link is never followed; a deleted file or a submodule is skipped
        destination = (target / relative).resolve()
        if not _inside(destination, target):
            continue
        destination.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(origin, destination, follow_symlinks=False)
        copied += 1
    return copied


def _commit(target: Path, message: str, source_head: str) -> None:
    run_git([*IDENTITY, "add", "-A"], target, timeout=COPY_TIMEOUT_SECONDS)
    run_git([*IDENTITY, "commit", "-q", "-m", message, "-m", f"{TRAILER}: {source_head}"],
            target, timeout=COPY_TIMEOUT_SECONDS)


def _refreshable(target: Path) -> str | None:
    """The source commit the snapshot's ``main`` holds (``None``: not recorded), or ``Conflict``."""
    if run_git(["rev-parse", "--abbrev-ref", "HEAD"], target).stdout.strip() != "main":
        raise Conflict(f"{target} is not on main: the snapshot is not refreshed")
    if run_git(["status", "--porcelain"], target).stdout.strip():
        raise Conflict(f"{target} has changes or untracked files: the snapshot is not refreshed")
    author = run_git(["log", "-1", "--format=%ae"], target).stdout.strip()
    if author != IDENTITY[-1].split("=", 1)[1]:
        raise Conflict(f"main in {target} has commits the source does not: land them or use a new name")
    recorded = run_git(["log", "-1", f"--format=%(trailers:key={TRAILER},valueonly)"], target)
    return recorded.stdout.strip() or None


def _refresh(target: Path, source: Path, source_head: str) -> Dict[str, Any]:
    previous = _head(target)
    reply = {"ok": True, "path": str(target), "created": False, "refreshed": False,
             "previous_head": previous, "head": previous, "source_head": source_head, "files": None}
    if _refreshable(target) == source_head:
        return reply
    tracked = run_git(["ls-files", "-z"], target).stdout.split("\0")
    for relative in filter(None, tracked):
        path = target / relative
        if path.is_symlink() or path.is_file():
            path.unlink()
    copied = _copy_source(source, target)
    run_git([*IDENTITY, "add", "-A"], target, timeout=COPY_TIMEOUT_SECONDS)
    if not run_git(["status", "--porcelain"], target).stdout.strip():
        return {**reply, "files": copied}  # the same files: nothing to commit
    _commit(target, f"{MESSAGE}, refreshed from the source", source_head)
    return {**reply, "refreshed": True, "head": _head(target), "files": copied}


def checkout(name: Any) -> Dict[str, Any]:
    source = source_repository()
    root = snapshots_root()
    target = target_for(name, root, source)
    source_head = _head(source)
    if (target / ".git").is_dir() and not (target / ".git").is_symlink():
        return _refresh(target, source, source_head)
    if target.exists() and (target.is_symlink() or not target.is_dir() or any(target.iterdir())):
        raise Refused(f"{target} exists and is not an earlier snapshot")
    root.mkdir(parents=True, exist_ok=True)
    target.mkdir(exist_ok=True)
    copied = _copy_source(source, target)
    run_git([*IDENTITY, "init", "-q", "-b", "main"], target)
    _commit(target, MESSAGE, source_head)
    return {"ok": True, "path": str(target), "created": True, "refreshed": False,
            "previous_head": None, "head": _head(target), "source_head": source_head,
            "files": copied}


async def handle_checkout(nc, msg, reply, request_body) -> None:
    """``workspace.self_host.checkout`` → the snapshot, or ``{"error": {status, message}}``."""
    data = request_body(msg)
    try:
        body = await asyncio.to_thread(checkout, data.get("name"))
    except Refused as e:
        body = {"error": {"status": 400, "message": str(e)}}
    except (Conflict, GitError) as e:
        body = {"error": {"status": 409, "message": str(e)}}
    except OSError as e:
        body = {"error": {"status": 500, "message": f"{type(e).__name__}: {e}"}}
    await reply(nc, msg, body)


HANDLERS = ((SUBJECT, handle_checkout),)
