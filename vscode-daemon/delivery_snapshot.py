"""A snapshot before each delivery, so a person can undo it: ``workspace.undo``.

Taken from opencode's snapshots (https://opencode.ai/v2/docs/snapshots/): the
state of the files a step is about to change is kept in a separate Git object
store in the tool's own data directory, never in the project's ``.git``, so it
works for a folder that is not a repository too, and undoing a step restores
only the paths that step changed.

For a company without the Git workflow a delivery lands in the person's
folder (with the workflow it is a commit on the task's branch, and discarding
the branch undoes it). Before writing, the daemon keeps, for each target:
whether it existed and, if so, its bytes (a blob) and their sha256; after
writing, the sha256 of the bytes read back. The manifest and the blobs form
one tree, pointed at by ``refs/fabrique/snapshots/<id>`` in a bare store per
project under ``FABRIQUE_SNAPSHOT_STATE`` (default
``~/.fabrique-daemon/snapshots``), named by the sha256 of the project's path.

Undo is all or nothing: every file must still lead to where it was written
(inside the project, no VCS metadata, no symlink along the way) and hold the
bytes the delivery left; a file changed or deleted since is a conflict and
nothing is touched. Otherwise each file gets its bytes back, or is removed
when the delivery created it (with the folders it created, once empty), and
the ref is deleted, so a second undo finds no snapshot.

The bytes of a file that may hold secrets (``workspace_read.is_sensitive``)
are never copied into the store: a delivery that overwrites one is made
without a snapshot, and the reply says why (``snapshot_error``). A failure to
take the snapshot never blocks the delivery either.
"""

from __future__ import annotations

import hashlib
import json
import logging
import os
import re
import secrets
import subprocess
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Callable, Dict, List, Optional

import workspace_edit
import workspace_read
from workspace_read import ReadError

logger = logging.getLogger("fabrique-daemon")

GIT_TIMEOUT_SECONDS = 60
REF_PREFIX = "refs/fabrique/snapshots/"
SNAPSHOT_ID = re.compile(r"^[a-f0-9]{32}$")


class SnapshotError(Exception):
    """The store could not be read or written, with git's own words."""


def state_dir() -> Path:
    return Path(os.getenv("FABRIQUE_SNAPSHOT_STATE", "~/.fabrique-daemon/snapshots")).expanduser()


def _sha256(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def store_for(root: Path) -> Path:
    """The bare store of the project at *root* (a canonical path)."""
    return state_dir() / f"{_sha256(str(root).encode('utf-8'))}.git"


def _git(store: Path, args: List[str], data: Optional[bytes] = None, check: bool = True) -> subprocess.CompletedProcess:
    """``git`` on the store alone: no shell, no hooks, no inherited repository."""
    env = {k: v for k, v in os.environ.items() if not k.startswith("GIT_")}
    env.update({"GIT_DIR": str(store), "GIT_TERMINAL_PROMPT": "0", "GIT_CONFIG_NOSYSTEM": "1", "LC_ALL": "C"})
    try:
        result = subprocess.run(  # noqa: S603 - fixed argv, no shell.
            ["git", "-c", "core.hooksPath=/dev/null", *args],
            cwd=str(store), env=env, input=data, capture_output=True,
            timeout=GIT_TIMEOUT_SECONDS, check=False,
        )
    except FileNotFoundError as exc:
        raise SnapshotError("git is not installed on this machine") from exc
    except subprocess.TimeoutExpired as exc:
        raise SnapshotError(f"git {args[0]} took more than {GIT_TIMEOUT_SECONDS} s") from exc
    if check and result.returncode != 0:
        raise SnapshotError(result.stderr.decode("utf-8", "replace").strip() or f"git {args[0]} failed")
    return result


def _open_store(root: Path) -> Path:
    store = store_for(root)
    if not (store / "HEAD").is_file():
        store.mkdir(parents=True, exist_ok=True)
        _git(store, ["init", "--bare", "--quiet", str(store)])
        # The blobs are referenced from a tree; never collect them on our own.
        _git(store, ["config", "gc.auto", "0"])
    return store


def _blob(store: Path, data: bytes) -> str:
    return _git(store, ["hash-object", "-w", "--no-filters", "--stdin"], data).stdout.decode().strip()


def _safe_target(root: Path, relative: Any, resolve) -> Optional[Path]:
    """The file *relative* names, when it leads there: inside the project, no
    VCS metadata, no symlink along the way. ``None`` otherwise."""
    try:
        target = workspace_read._target(root, relative, resolve)
    except ReadError:
        return None
    return target if target == root.joinpath(*Path(relative).parts) and target != root else None


def _created_dirs(root: Path, target: Path) -> List[str]:
    """The folders above *target* that do not exist yet, deepest first."""
    missing, folder = [], target.parent
    while folder != root and not folder.exists():
        missing.append(folder.relative_to(root).as_posix())
        folder = folder.parent
    return missing


def _before(root: Path, store: Path, files: list, resolve) -> Dict[str, Dict[str, Any]]:
    """What each target holds before the delivery, by requested path; raises when one cannot be kept."""
    entries: Dict[str, Dict[str, Any]] = {}
    for file_info in files:
        requested = file_info.get("path") if isinstance(file_info, dict) else None
        if not isinstance(requested, str) or requested in entries:
            continue
        if resolve(str(root), requested) in (None, root):
            continue  # write_files refuses it: nothing changes there.
        target = _safe_target(root, requested, resolve)
        if target is None:
            raise SnapshotError(f"{requested} is reached through a symlink or an unusual path")
        entry: Dict[str, Any] = {"path": requested, "existed": target.exists()}
        if entry["existed"]:
            if not target.is_file():
                raise SnapshotError(f"{requested} is not a regular file")
            if workspace_read.is_sensitive(root, target):
                raise SnapshotError(f"{requested} may hold secrets; its bytes are never copied")
            data = target.read_bytes()
            entry.update({"before_blob": _blob(store, data), "before_sha256": _sha256(data)})
        else:
            entry.update({"before_blob": None, "before_sha256": None, "created_dirs": _created_dirs(root, target)})
        entries[requested] = entry
    return entries


def _save(root: Path, store: Path, entries: List[Dict[str, Any]]) -> str:
    snapshot_id = secrets.token_hex(16)
    manifest = {
        "id": snapshot_id,
        "created_at": datetime.now(timezone.utc).isoformat(),
        "project_root": str(root),
        "files": entries,
    }
    rows = [f"100644 blob {_blob(store, json.dumps(manifest).encode('utf-8'))}\tmanifest.json"]
    rows += [f"100644 blob {e['before_blob']}\t{n}" for n, e in enumerate(entries) if e["before_blob"]]
    tree = _git(store, ["mktree"], ("\n".join(rows) + "\n").encode()).stdout.decode().strip()
    _git(store, ["update-ref", REF_PREFIX + snapshot_id, tree])
    return snapshot_id


def deliver(workspace_path: str, files: list, write_files: Callable[[str, list], list], resolve) -> Dict[str, Any]:
    """``write_files`` in the reply shape the backend reads, with the snapshot that undoes it."""
    root_path = Path(workspace_path).expanduser()
    store = entries = None
    snapshot_error = None
    if root_path.is_absolute() and root_path.is_dir():
        root = root_path.resolve()
        try:
            store = _open_store(root)
            entries = _before(root, store, files, resolve)
        except (OSError, SnapshotError) as exc:
            snapshot_error = f"no snapshot was taken, this delivery cannot be undone: {exc}"
    results = write_files(workspace_path, files)
    reply: Dict[str, Any] = {
        "ok": all(r["status"] == "written" for r in results),
        "workspace_root": workspace_path,
        "files": results,
    }
    if entries is not None:
        after: Dict[str, Any] = {}  # A path delivered twice: the last bytes written.
        for result in results:
            if result.get("status") == "written" and result.get("path") in entries:
                after[result["path"]] = result.get("sha256")
        written = [{**entries[path], "after_sha256": sha} for path, sha in after.items()]
        try:
            if written:
                snapshot_id = _save(root, store, written)
                reply["snapshot"] = {"id": snapshot_id, "files": [
                    {k: e[k] for k in ("path", "existed", "before_sha256", "after_sha256")} for e in written
                ]}
        except (OSError, SnapshotError) as exc:
            snapshot_error = f"no snapshot was kept, this delivery cannot be undone: {exc}"
    if snapshot_error:
        logger.warning("Delivery under %s: %s", workspace_path, snapshot_error)
        reply["snapshot_error"] = snapshot_error
    return reply


def _manifest(store: Path, snapshot_id: Any) -> Dict[str, Any]:
    if not isinstance(snapshot_id, str) or not SNAPSHOT_ID.match(snapshot_id):
        raise ReadError(400, "A snapshot id (32 hex characters) is required.")
    ref = REF_PREFIX + snapshot_id
    if not (store / "HEAD").is_file() or _git(store, ["rev-parse", "--verify", "--quiet", ref], check=False).returncode:
        raise ReadError(409, f"No snapshot {snapshot_id} for this project: it was undone already, or never taken here.")
    return json.loads(_git(store, ["cat-file", "blob", f"{ref}:manifest.json"]).stdout.decode("utf-8"))


def undo(workspace_path: str, snapshot_id: Any, resolve) -> Dict[str, Any]:
    """Put back what the snapshot kept, only when every file still holds what the delivery wrote."""
    root = workspace_read._root(workspace_path)
    store = store_for(root)
    try:
        manifest = _manifest(store, snapshot_id)
    except SnapshotError as exc:
        raise ReadError(500, f"The snapshot store could not be read: {exc}") from exc
    entries = manifest.get("files") or []
    targets, conflicts = [], []
    for entry in entries:
        target = _safe_target(root, entry.get("path"), resolve)
        if target is None:
            conflicts.append(f"{entry.get('path')} (no longer leads to the file written)")
        elif not target.is_file():
            conflicts.append(f"{entry['path']} (deleted since the delivery)")
        elif _sha256(target.read_bytes()) != entry.get("after_sha256"):
            conflicts.append(f"{entry['path']} (changed since the delivery)")
        targets.append(target)
    if conflicts:
        raise ReadError(409, "Nothing was undone; these files are not as the delivery left them: " + ", ".join(conflicts))
    ref = REF_PREFIX + manifest["id"]
    restored, removed = [], []
    for n, (entry, target) in enumerate(zip(entries, targets)):
        if entry.get("existed"):
            data = _git(store, ["cat-file", "blob", f"{ref}:{n}"]).stdout
            workspace_edit._write_atomically(target, data)
            restored.append({"path": entry["path"], "sha256": _sha256(target.read_bytes())})
        else:
            target.unlink()
            for folder in entry.get("created_dirs") or []:
                try:
                    (root / folder).rmdir()  # Only once empty.
                except OSError:
                    break
            removed.append({"path": entry["path"]})
    _git(store, ["update-ref", "-d", ref])
    return {"ok": True, "snapshot": manifest["id"], "restored": restored, "removed": removed}


def _undo(root: str, data: Dict[str, Any], resolve) -> Dict[str, Any]:
    try:
        return undo(root, data.get("snapshot_id"), resolve)
    except SnapshotError as exc:
        raise ReadError(500, f"The snapshot store could not be written: {exc}") from exc


async def handle_undo(nc, msg, reply, request_body, resolve) -> None:
    """``workspace.undo`` → what was restored and removed, or ``{"error": {status, message}}``."""
    await workspace_read._serve(nc, msg, reply, request_body, resolve, _undo)
