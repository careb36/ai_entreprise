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

Only the files the delivery changed are in the snapshot: created, or with
other bytes than before (a file written with the same bytes is left out), once
per file however its path was spelled (``app.py`` and ``./app.py``), with the
hash of the last write that succeeded on it. Everything is staged before an
undo changes a file (the bytes to put back, the delivery's bytes now on disk,
whether each folder can be written); a write that fails midway puts the files
already undone back as the delivery left them, so the snapshot can still undo
it. A delivery, an edit of the Developer's tool and an undo of the same project
hold its lock (``project_lock``) from their first read to their last write.

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
import tempfile
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Callable, Dict, List, Optional, Tuple

import project_lock
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


def _before(root: Path, store: Path, files: list, resolve) -> Tuple[Dict[str, Dict[str, Any]], Dict[str, str]]:
    """What each target holds before the delivery, once per file however its path is spelled
    (``app.py`` and ``./app.py``): the entries by the target's path relative to the root,
    and the key of each requested path. Raises when one cannot be kept."""
    entries: Dict[str, Dict[str, Any]] = {}
    keys: Dict[str, str] = {}
    for file_info in files:
        requested = file_info.get("path") if isinstance(file_info, dict) else None
        if not isinstance(requested, str) or requested in keys:
            continue
        if resolve(str(root), requested) in (None, root):
            continue  # write_files refuses it: nothing changes there.
        target = _safe_target(root, requested, resolve)
        if target is None:
            raise SnapshotError(f"{requested} is reached through a symlink or an unusual path")
        key = keys[requested] = target.relative_to(root).as_posix()
        if key in entries:
            continue  # An alias of a file already kept: its original bytes once.
        entry: Dict[str, Any] = {"path": key, "existed": target.exists()}
        if entry["existed"]:
            if not target.is_file():
                raise SnapshotError(f"{requested} is not a regular file")
            if workspace_read.is_sensitive(root, target):
                raise SnapshotError(f"{requested} may hold secrets; its bytes are never copied")
            data = target.read_bytes()
            entry.update({"before_blob": _blob(store, data), "before_sha256": _sha256(data)})
        else:
            entry.update({"before_blob": None, "before_sha256": None, "created_dirs": _created_dirs(root, target)})
        entries[key] = entry
    return entries, keys


def _changed(entries: Dict[str, Dict[str, Any]], keys: Dict[str, str], results: list) -> List[Dict[str, Any]]:
    """The files the delivery changed: created, or with other bytes than before; each with
    the hash of the last write that succeeded on it, whichever alias it came through."""
    after: Dict[str, Any] = {}
    for result in results:
        key = keys.get(result.get("path"))
        if result.get("status") == "written" and key is not None:
            after[key] = result.get("sha256")
    return [
        {**entries[key], "after_sha256": sha}
        for key, sha in after.items()
        if not entries[key]["existed"] or entries[key]["before_sha256"] != sha
    ]


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
    """``write_files`` in the reply shape the backend reads, with the snapshot that undoes it.

    Under the project's lock from the snapshot to the manifest (``project_lock``)."""
    with project_lock.lock_for(workspace_path):
        return _deliver(workspace_path, files, write_files, resolve)


def _deliver(workspace_path: str, files: list, write_files: Callable[[str, list], list], resolve) -> Dict[str, Any]:
    root_path = Path(workspace_path).expanduser()
    store = entries = keys = None
    snapshot_error = None
    if root_path.is_absolute() and root_path.is_dir():
        root = root_path.resolve()
        try:
            store = _open_store(root)
            entries, keys = _before(root, store, files, resolve)
        except (OSError, SnapshotError) as exc:
            snapshot_error = f"no snapshot was taken, this delivery cannot be undone: {exc}"
    results = write_files(workspace_path, files)
    reply: Dict[str, Any] = {
        "ok": all(r["status"] == "written" for r in results),
        "workspace_root": workspace_path,
        "files": results,
    }
    if entries is not None:
        written = _changed(entries, keys, results)
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
    """Put back what the snapshot kept, only when every file still holds what the delivery wrote.

    Under the project's lock from the first check to the ref's deletion (``project_lock``)."""
    root = workspace_read._root(workspace_path)
    with project_lock.lock_for(str(root)):
        return _undo_locked(root, snapshot_id, resolve)


def _writable(folder: Path) -> bool:
    """Whether a file can be created, replaced or removed in *folder*: tried, not guessed."""
    try:
        handle, probe = tempfile.mkstemp(prefix=".fabrique-undo-", dir=str(folder))
    except OSError:
        return False
    os.close(handle)
    os.unlink(probe)
    return True


def _put_back(target: Path, data: bytes) -> None:
    """*data* in *target*, whether the file is there or was removed."""
    if target.exists():
        workspace_edit._write_atomically(target, data)
    else:
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_bytes(data)


def _undo_locked(root: Path, snapshot_id: Any, resolve) -> Dict[str, Any]:
    store = store_for(root)
    try:
        manifest = _manifest(store, snapshot_id)
    except SnapshotError as exc:
        raise ReadError(500, f"The snapshot store could not be read: {exc}") from exc
    entries = manifest.get("files") or []
    ref = REF_PREFIX + manifest["id"]
    # Staged before anything changes: each target, the delivery's bytes now on
    # disk (to roll back to) and the bytes to put back.
    staged, conflicts = [], []
    for n, entry in enumerate(entries):
        target = _safe_target(root, entry.get("path"), resolve)
        current = target.read_bytes() if target is not None and target.is_file() else None
        if target is None:
            conflicts.append(f"{entry.get('path')} (no longer leads to the file written)")
        elif current is None:
            conflicts.append(f"{entry['path']} (deleted since the delivery)")
        elif _sha256(current) != entry.get("after_sha256"):
            conflicts.append(f"{entry['path']} (changed since the delivery)")
        elif not _writable(target.parent):
            conflicts.append(f"{entry['path']} (its folder cannot be written)")
        staged.append((entry, target, current, n))
    if conflicts:
        raise ReadError(409, "Nothing was undone; these files are not as the delivery left them: " + ", ".join(conflicts))
    originals = {
        n: _git(store, ["cat-file", "blob", f"{ref}:{n}"]).stdout
        for entry, _, _, n in staged if entry.get("existed")
    }
    done: List[Tuple[Path, bytes]] = []
    restored, removed = [], []
    try:
        for entry, target, current, n in staged:
            if entry.get("existed"):
                workspace_edit._write_atomically(target, originals[n])
                done.append((target, current))
                restored.append({"path": entry["path"], "sha256": _sha256(target.read_bytes())})
            else:
                target.unlink()
                done.append((target, current))
                removed.append({"path": entry["path"]})
    except OSError as exc:
        # Back to the delivery's bytes, so the snapshot can still undo it.
        unrecovered = []
        for target, data in reversed(done):
            try:
                _put_back(target, data)
            except OSError:
                unrecovered.append(target.relative_to(root).as_posix())
        if unrecovered:
            raise ReadError(500, f"Undoing failed ({exc}) and these files could not be put back as the delivery left them: {', '.join(unrecovered)}") from exc
        raise ReadError(500, f"Nothing was undone: {exc}. The files are as the delivery left them; the snapshot can still undo it.") from exc
    for entry, _, _, _ in staged:
        for folder in entry.get("created_dirs") or []:
            try:
                (root / folder).rmdir()  # Only once empty.
            except OSError:
                break
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
