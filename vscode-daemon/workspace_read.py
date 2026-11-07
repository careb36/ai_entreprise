"""The Developer's eyes on the project: ``workspace.list``, ``workspace.read``
and ``workspace.search``.

Read-only requests the backend relays as tools the model may call before it
delivers (``backend/app/services/developer_tools.py``). Each reply is an
observation of the disk this daemon made: the sha256 of the bytes a read
returned, how many entries a listing holds, how many matches a search found,
and where a bound cut the answer. The model's reading of what came back stays
a claim. Nothing here writes, and a path outside the workspace is rejected.
"""

from __future__ import annotations

import asyncio
import hashlib
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
LIST_MAX_ENTRIES = 400
LIST_MAX_DEPTH = 6
READ_MAX_BYTES = 64 * 1024
READ_MAX_LINES = 400
SEARCH_MAX_MATCHES = 60
SEARCH_MAX_FILE_BYTES = 512 * 1024
SEARCH_MAX_FILES = 5000
SEARCH_LINE_CHARS = 240


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


def _target(root: Path, relative: Any, resolve: Callable[[str, str], Optional[Path]]) -> Path:
    if not isinstance(relative, str):
        raise ReadError(400, "A path is required.")
    target = resolve(str(root), relative)
    if target is None:
        raise ReadError(400, f"Not a path inside the project: {relative}")
    return target


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


def read_file(workspace_path: str, path: Any, start_line: Any, resolve) -> Dict[str, Any]:
    """A text file as it is on disk: its lines numbered from ``start_line``
    (default 1), at most ``READ_MAX_LINES`` lines and ``READ_MAX_BYTES`` bytes,
    with the sha256 and size of the whole file."""
    root = _root(workspace_path)
    target = _target(root, path, resolve)
    if not target.is_file():
        raise ReadError(404, f"No such file: {path}")
    raw = target.read_bytes()
    digest = hashlib.sha256(raw).hexdigest()
    if b"\0" in raw[:8192]:
        return {"path": path, "sha256": digest, "bytes": len(raw), "binary": True, "text": "", "lines": 0, "truncated": False}
    text = raw.decode("utf-8", errors="replace")
    lines = text.splitlines()
    try:
        first = max(1, int(start_line or 1))
    except (TypeError, ValueError):
        first = 1
    chosen = lines[first - 1 : first - 1 + READ_MAX_LINES]
    numbered: List[str] = []
    size = 0
    truncated = len(lines) > first - 1 + len(chosen)
    for offset, line in enumerate(chosen):
        row = f"{first + offset:5d}| {line}"
        size += len(row) + 1
        if size > READ_MAX_BYTES:
            truncated = True
            break
        numbered.append(row)
    return {
        "path": path,
        "sha256": digest,
        "bytes": len(raw),
        "binary": False,
        "text": "\n".join(numbered),
        "lines": len(lines),
        "first_line": first,
        "truncated": truncated,
    }


def search(workspace_path: str, pattern: Any, path: Any, resolve) -> Dict[str, Any]:
    """The lines matching *pattern* (a regular expression, case-insensitive)
    under *path* (default: the root): ``file:line: text``, at most
    ``SEARCH_MAX_MATCHES`` of them."""
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
    truncated = False
    if start.is_dir():
        for dirpath, dirnames, filenames in os.walk(start):
            dirnames[:] = sorted(d for d in dirnames if d not in SKIP_DIRS and not d.startswith("."))
            files.extend(Path(dirpath) / n for n in sorted(filenames) if not n.startswith("."))
            if len(files) > SEARCH_MAX_FILES:
                truncated = True
                break
    for file in files[:SEARCH_MAX_FILES]:
        try:
            if file.stat().st_size > SEARCH_MAX_FILE_BYTES:
                continue
            raw = file.read_bytes()
        except OSError:
            continue
        if b"\0" in raw[:8192]:
            continue
        scanned += 1
        relative = file.relative_to(root).as_posix()
        for number, line in enumerate(raw.decode("utf-8", errors="replace").splitlines(), 1):
            if regex.search(line):
                if len(matches) >= SEARCH_MAX_MATCHES:
                    truncated = True
                    break
                matches.append(f"{relative}:{number}: {line.strip()[:SEARCH_LINE_CHARS]}")
        if truncated:
            break
    return {"pattern": pattern, "matches": matches, "files_scanned": scanned, "truncated": truncated}


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
    """``workspace.search`` → the matching lines."""
    await _serve(nc, msg, reply, request_body, resolve, lambda r, d, s: search(r, d.get("pattern"), d.get("path"), s))


# (subject, handler) for the daemon's subscription loop.
HANDLERS = (
    ("workspace.list", handle_list),
    ("workspace.read", handle_read),
    ("workspace.search", handle_search),
)
