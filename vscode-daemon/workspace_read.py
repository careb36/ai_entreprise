"""The Developer's eyes on the project: ``workspace.list``, ``workspace.read``
and ``workspace.search``.

Read-only requests the backend relays as tools the model may call before it
delivers (``backend/app/services/developer_tools.py``). Each reply is an
observation of the disk this daemon made: the sha256 of the bytes a read
returned, how many entries a listing holds, how many matches a search found,
and where a bound cut the answer. The model's reading of what came back stays
a claim. Nothing here writes, and a path outside the workspace is rejected.

Bounds that hold before anything is loaded: a read hashes the file in chunks
and indexes only its first ``READ_SCAN_BYTES``; a search opens only files
inside the project (a symlink that leads out is skipped), never a file that
may hold secrets (``SENSITIVE_NAMES``), and runs in a child process it kills
at ``SEARCH_TIMEOUT_SECONDS``, so a pattern that backtracks forever costs
that much and no more.
"""

from __future__ import annotations

import asyncio
import fnmatch
import hashlib
import multiprocessing
import os
import re
from pathlib import Path
from typing import Any, Callable, Dict, List, Optional

# Folders a listing or a search never enters: dependencies, build output, VCS
# data, caches. The same set the project snapshot skips.
SKIP_DIRS = {
    ".git", ".svn", ".hg", "node_modules", "__pycache__", ".venv", "venv", "env",
    "dist", "build", ".next", ".nuxt", "target", ".gradle", ".idea", ".vscode",
    "coverage", ".pytest_cache", ".mypy_cache", ".ruff_cache", "vendor",
}
# Files that may hold secrets: never read, never searched, whatever the
# request names. Folders whose every file is such.
SENSITIVE_NAMES = (
    ".env", ".env.*", "*.pem", "*.key", "*.p12", "*.pfx", "*.jks", "*.keystore",
    "id_rsa", "id_dsa", "id_ecdsa", "id_ed25519", ".netrc", ".npmrc", ".pypirc",
    ".git-credentials", "credentials", "credentials.json", "secrets.*", "*.secret",
    "secrets.yaml", "secrets.yml", "service-account*.json",
)
SENSITIVE_DIRS = {".ssh", ".aws", ".gnupg", ".git", ".docker", ".kube"}
LIST_MAX_ENTRIES = 400
LIST_MAX_DEPTH = 6
READ_MAX_BYTES = 64 * 1024
READ_MAX_LINES = 400
# How much of a file is read into memory to index its lines; the rest is only hashed.
READ_SCAN_BYTES = 1024 * 1024
SEARCH_MAX_MATCHES = 60
SEARCH_MAX_FILE_BYTES = 512 * 1024
SEARCH_MAX_FILES = 5000
SEARCH_LINE_CHARS = 240
SEARCH_TIMEOUT_SECONDS = 20.0


class ReadError(Exception):
    """A request that cannot be served; ``status`` follows HTTP."""

    def __init__(self, status: int, message: str):
        super().__init__(message)
        self.status = status
        self.message = message


def _root(workspace_path: str) -> Path:
    root = Path(workspace_path).expanduser()
    if not root.is_absolute() or not root.is_dir():
        raise ReadError(404, "The project folder is not on this machine.")
    return root.resolve()


def _inside(root: Path, candidate: Path) -> Optional[Path]:
    """*candidate* resolved, when it stays under *root* (symlinks followed); else ``None``."""
    try:
        resolved = candidate.resolve()
        resolved.relative_to(root)
    except (OSError, ValueError):
        return None
    return resolved


def _target(root: Path, relative: Any, resolve: Callable[[str, str], Optional[Path]]) -> Path:
    if not isinstance(relative, str):
        raise ReadError(400, "A path is required.")
    target = resolve(str(root), relative)
    if target is None or _inside(root, target) is None:
        raise ReadError(400, f"Not a path inside the project: {relative}")
    return target


def is_sensitive(root: Path, path: Path) -> bool:
    """Whether *path* (under *root*) may hold secrets, by its name or folder."""
    try:
        parts = path.relative_to(root).parts
    except ValueError:
        return True
    if any(part in SENSITIVE_DIRS for part in parts[:-1]):
        return True
    name = parts[-1] if parts else ""
    return any(fnmatch.fnmatch(name, pattern) for pattern in SENSITIVE_NAMES)


def list_tree(workspace_path: str, path: Any, depth: Any, resolve) -> Dict[str, Any]:
    """The files and folders under *path* (default: the project root), as
    posix paths relative to the root, folders ending in ``/``; sorted, at most
    ``LIST_MAX_ENTRIES`` entries and ``depth`` levels (default 2)."""
    root = _root(workspace_path)
    start = _target(root, path or ".", resolve)
    if not start.is_dir():
        raise ReadError(404, f"Not a folder: {path}")
    try:
        levels = max(1, min(int(depth or 2), LIST_MAX_DEPTH))
    except (TypeError, ValueError):
        levels = 2
    entries: List[str] = []
    truncated = False
    base_depth = len(start.relative_to(root).parts)
    for dirpath, dirnames, filenames in os.walk(start):
        current = Path(dirpath)
        rel_dir = current.relative_to(root)
        dirnames[:] = sorted(d for d in dirnames if d not in SKIP_DIRS and not d.startswith("."))
        here = len(rel_dir.parts) - base_depth
        names = [f"{d}/" for d in dirnames] + sorted(n for n in filenames if not n.startswith("."))
        for name in names:
            if len(entries) >= LIST_MAX_ENTRIES:
                truncated = True
                break
            entries.append((rel_dir / name).as_posix() if rel_dir.parts else name)
        if truncated or here + 1 >= levels:
            dirnames[:] = []
        if truncated:
            break
    return {"path": start.relative_to(root).as_posix() if start != root else ".", "entries": entries, "truncated": truncated}


def _hash_and_head(target: Path) -> tuple:
    """The sha256 of the whole file, streamed, and its first ``READ_SCAN_BYTES``."""
    digest = hashlib.sha256()
    head = b""
    size = 0
    with target.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1 << 20), b""):
            digest.update(chunk)
            size += len(chunk)
            if len(head) < READ_SCAN_BYTES:
                head += chunk[: READ_SCAN_BYTES - len(head)]
    return digest.hexdigest(), head, size


def read_file(workspace_path: str, path: Any, start_line: Any, resolve) -> Dict[str, Any]:
    """A text file as it is on disk: its lines numbered from ``start_line``
    (default 1), at most ``READ_MAX_LINES`` lines and ``READ_MAX_BYTES`` bytes,
    with the sha256 and size of the whole file (hashed in chunks; only the
    first ``READ_SCAN_BYTES`` are read into memory)."""
    root = _root(workspace_path)
    target = _target(root, path, resolve)
    if not target.is_file():
        raise ReadError(404, f"No such file: {path}")
    if is_sensitive(root, target):
        raise ReadError(403, f"A file that may hold secrets is never shown: {path}")
    digest, head, size = _hash_and_head(target)
    if b"\0" in head[:8192]:
        return {"path": path, "sha256": digest, "bytes": size, "binary": True, "text": "", "lines": 0, "truncated": False}
    lines = head.decode("utf-8", errors="replace").splitlines()
    beyond_scan = size > len(head)
    try:
        first = max(1, int(start_line or 1))
    except (TypeError, ValueError):
        first = 1
    chosen = lines[first - 1 : first - 1 + READ_MAX_LINES]
    numbered: List[str] = []
    out = 0
    truncated = beyond_scan or len(lines) > first - 1 + len(chosen)
    for offset, line in enumerate(chosen):
        row = f"{first + offset:5d}| {line}"
        out += len(row) + 1
        if out > READ_MAX_BYTES:
            truncated = True
            break
        numbered.append(row)
    return {
        "path": path,
        "sha256": digest,
        "bytes": size,
        "binary": False,
        "text": "\n".join(numbered),
        "lines": len(lines),
        "lines_indexed_to_byte": len(head) if beyond_scan else None,
        "first_line": first,
        "truncated": truncated,
    }


def search(workspace_path: str, pattern: Any, path: Any, resolve) -> Dict[str, Any]:
    """The lines matching *pattern* (a regular expression, case-insensitive)
    under *path* (default: the root): ``file:line: text``, at most
    ``SEARCH_MAX_MATCHES`` of them. Files that may hold secrets and files
    that resolve outside the project are never opened."""
    root = _root(workspace_path)
    if not isinstance(pattern, str) or not pattern.strip():
        raise ReadError(400, "A pattern is required.")
    try:
        regex = re.compile(pattern, re.IGNORECASE)
    except re.error as exc:
        raise ReadError(400, f"Not a valid pattern: {exc}") from exc
    start = _target(root, path or ".", resolve)
    if not start.exists():
        raise ReadError(404, f"No such path: {path}")
    files = [start] if start.is_file() else []
    matches: List[str] = []
    scanned = 0
    files_truncated = False
    matches_truncated = False
    if start.is_dir():
        for dirpath, dirnames, filenames in os.walk(start):
            dirnames[:] = sorted(d for d in dirnames if d not in SKIP_DIRS and not d.startswith("."))
            files.extend(Path(dirpath) / n for n in sorted(filenames) if not n.startswith("."))
            if len(files) > SEARCH_MAX_FILES:
                files_truncated = True
                break
    for file in files[:SEARCH_MAX_FILES]:
        contained = _inside(root, file)
        if contained is None or is_sensitive(root, contained):
            continue
        try:
            if contained.stat().st_size > SEARCH_MAX_FILE_BYTES:
                continue
            raw = contained.read_bytes()
        except OSError:
            continue
        if b"\0" in raw[:8192]:
            continue
        scanned += 1
        relative = file.relative_to(root).as_posix()
        for number, line in enumerate(raw.decode("utf-8", errors="replace").splitlines(), 1):
            if regex.search(line):
                if len(matches) >= SEARCH_MAX_MATCHES:
                    matches_truncated = True
                    break
                matches.append(f"{relative}:{number}: {line.strip()[:SEARCH_LINE_CHARS]}")
        if matches_truncated:
            break
    return {
        "pattern": pattern,
        "matches": matches,
        "files_scanned": scanned,
        "truncated": files_truncated or matches_truncated,
    }


def _resolve_inside(workspace_path: str, relative: str) -> Optional[Path]:
    """The daemon's path resolution, for the search child process."""
    root = Path(workspace_path).resolve()
    candidate = (root / (relative or "")).resolve()
    try:
        candidate.relative_to(root)
    except ValueError:
        return None
    return candidate


def _search_job(workspace_path: str, pattern: str, path: Any, queue) -> None:
    try:
        queue.put(("ok", search(workspace_path, pattern, path, _resolve_inside)))
    except ReadError as exc:
        queue.put(("error", exc.status, exc.message))
    except Exception as exc:  # noqa: BLE001 - The parent reports it as a 500.
        queue.put(("error", 500, f"{type(exc).__name__}: {exc}"))


def search_bounded(
    workspace_path: str, pattern: Any, path: Any, resolve, timeout: float = SEARCH_TIMEOUT_SECONDS
) -> Dict[str, Any]:
    """``search`` in a child process killed at *timeout*: a pattern that
    backtracks without end (``(a+)+$`` on a line of ``a``) costs that much
    and no more. The pattern and the path are checked here first, so a bad
    request is refused without a process."""
    root = _root(workspace_path)
    if not isinstance(pattern, str) or not pattern.strip():
        raise ReadError(400, "A pattern is required.")
    try:
        re.compile(pattern)
    except re.error as exc:
        raise ReadError(400, f"Not a valid pattern: {exc}") from exc
    _target(root, path or ".", resolve)
    context = multiprocessing.get_context("spawn")
    queue = context.Queue()
    worker = context.Process(target=_search_job, args=(str(root), pattern, path, queue), daemon=True)
    worker.start()
    try:
        outcome = queue.get(timeout=timeout)
    except Exception:  # noqa: BLE001 - queue.Empty, or a dead child: the deadline is the result.
        outcome = None
    finally:
        worker.join(timeout=1)
        if worker.is_alive():
            worker.kill()
            worker.join(timeout=5)
    if outcome is None:
        raise ReadError(408, f"The search ran past {timeout:g} s and was stopped; narrow the pattern or the path.")
    if outcome[0] == "error":
        raise ReadError(outcome[1], outcome[2])
    return outcome[1]


async def _serve(nc, msg, reply, request_body, resolve, handler) -> None:
    data = request_body(msg)
    root = data.get("workspace_root")
    if not isinstance(root, str) or not root.strip():
        body: Dict[str, Any] = {"error": {"status": 400, "message": "workspace_root is required."}}
    else:
        try:
            body = await asyncio.to_thread(handler, root, data, resolve)
        except ReadError as exc:
            body = {"error": {"status": exc.status, "message": exc.message}}
        except OSError as exc:
            body = {"error": {"status": 500, "message": f"Could not read the project: {exc}"}}
    await reply(nc, msg, body)


async def handle_list(nc, msg, reply, request_body, resolve) -> None:
    """``workspace.list`` → ``{"path", "entries", "truncated"}``."""
    await _serve(nc, msg, reply, request_body, resolve, lambda r, d, s: list_tree(r, d.get("path"), d.get("depth"), s))


async def handle_read(nc, msg, reply, request_body, resolve) -> None:
    """``workspace.read`` → the numbered text, its sha256 and size."""
    await _serve(nc, msg, reply, request_body, resolve, lambda r, d, s: read_file(r, d.get("path"), d.get("start_line"), s))


async def handle_search(nc, msg, reply, request_body, resolve) -> None:
    """``workspace.search`` → the matching lines, within the deadline."""
    await _serve(nc, msg, reply, request_body, resolve, lambda r, d, s: search_bounded(r, d.get("pattern"), d.get("path"), s))


# (subject, handler) for the daemon's subscription loop.
HANDLERS = (
    ("workspace.list", handle_list),
    ("workspace.read", handle_read),
    ("workspace.search", handle_search),
)
