"""
This machine's folders: the folder picker and the project check.

- ``workspace.browse``:  the sub-folders of a folder on this machine, for the
  folder picker of "New company" (never hidden, VCS or dependency folders).
- ``workspace.inspect``: whether a path is a folder here, its git remote and
  branch, and, when asked, a snapshot of its file paths and build files for the
  backend's stack detection (``.env`` is never sent).

A request that cannot be served answers ``{"error": {"status", "message"}}``
with an HTTP status (``vscode-daemon/protocol/PROTOCOL.md``, refusals). Moved
out of ``daemon.py`` unchanged (issue #511); ``daemon.py`` re-exports the names
its callers used.
"""

import asyncio
import os
import stat
import string
from pathlib import Path
from typing import Any, Dict, List, Optional

from daemon_status import host_info
from git_workspace import git_info

IS_WINDOWS = os.name == "nt"


class WorkspaceError(Exception):
    """A folder request that cannot be served; ``status`` follows HTTP."""

    def __init__(self, status: int, message: str):
        super().__init__(message)
        self.status = status
        self.message = message


# Folders never offered in the picker (dependencies, build output, VCS data).
BROWSE_SKIP_DIRS = {
    "node_modules", "__pycache__", "venv", "env", "dist", "build", "coverage",
    "target", "vendor", "$RECYCLE.BIN", "System Volume Information",
}
BROWSE_MAX_ENTRIES = 500
# Common places people clone repositories, offered as shortcuts when present.
CLONE_FOLDERS = (
    "code", "Code", "projects", "Projects", "src", "dev", "Developer", "repos",
    "git", "GitHub", "workspace", "workspaces", "Documents/GitHub",
    "Documents/GitLab", "source/repos", "IdeaProjects", "PycharmProjects",
    "WebstormProjects",
)


def _host_path(path: str) -> Path:
    """An absolute path on this machine, with ``~`` expanded."""
    candidate = Path(os.path.expanduser(path.strip()))
    if not candidate.is_absolute():
        raise WorkspaceError(400, f"Not an absolute path on this machine: {path}")
    return candidate


def _is_hidden(entry: os.DirEntry) -> bool:
    if entry.name.startswith("."):
        return True
    if IS_WINDOWS:
        try:
            attributes = entry.stat().st_file_attributes
        except OSError:
            return True
        return bool(attributes & (stat.FILE_ATTRIBUTE_HIDDEN | stat.FILE_ATTRIBUTE_SYSTEM))
    return False


def filesystem_roots() -> List[Dict[str, str]]:
    if IS_WINDOWS:
        drives = [f"{letter}:\\" for letter in string.ascii_uppercase]
        return [{"name": d, "path": d} for d in drives if os.path.isdir(d)]
    return [{"name": "/", "path": "/"}]


def clone_shortcuts() -> List[Dict[str, str]]:
    home = Path.home()
    shortcuts = [{"name": "Home", "path": str(home)}]
    seen = {str(home).lower() if IS_WINDOWS else str(home)}
    for relative in CLONE_FOLDERS:
        folder = home / relative
        key = str(folder).lower() if IS_WINDOWS else str(folder)
        if key not in seen and folder.is_dir():
            seen.add(key)
            shortcuts.append({"name": relative, "path": str(folder)})
    return shortcuts


def browse(path: Optional[str] = None) -> Dict[str, Any]:
    """The sub-folders of *path* (default: the home folder) on this machine."""
    target = _host_path(path).resolve() if path else Path.home()
    if not target.is_dir():
        raise WorkspaceError(404, f"Folder not found on {host_info()['hostname']}: {target}")
    entries = []
    truncated = False
    try:
        with os.scandir(target) as it:
            for entry in it:
                if entry.name in BROWSE_SKIP_DIRS or _is_hidden(entry):
                    continue
                try:
                    if not entry.is_dir():
                        continue
                except OSError:
                    continue
                if len(entries) >= BROWSE_MAX_ENTRIES:
                    truncated = True
                    break
                entries.append({
                    "name": entry.name,
                    "path": entry.path,
                    "is_git": os.path.exists(os.path.join(entry.path, ".git")),
                })
    except PermissionError:
        raise WorkspaceError(403, f"Permission denied: {target}")
    entries.sort(key=lambda e: e["name"].lower())
    parent = target.parent
    return {
        "host": host_info(),
        "path": str(target),
        "parent": str(parent) if parent != target else None,
        "entries": entries,
        "shortcuts": clone_shortcuts(),
        "roots": filesystem_roots(),
        "truncated": truncated,
    }


# What the backend's stack detection reads.  Paths are listed for every file;
# contents only for these build/config files.  ``.env`` is never sent.
SNAPSHOT_SKIP_DIRS = {
    ".git", ".svn", "node_modules", "__pycache__", ".venv", "venv", "env", "dist",
    "build", ".next", ".nuxt", "target", ".gradle", ".idea", ".vscode", "coverage",
    ".pytest_cache", ".mypy_cache", "vendor",
}
SNAPSHOT_KEY_FILES = {
    "package.json", "pyproject.toml", "requirements.txt", "setup.cfg", "pytest.ini",
    "pom.xml", "build.gradle", "build.gradle.kts", "Gemfile", "go.mod",
    "docker-compose.yml", "docker-compose.yaml", ".env.example", "application.yml",
    "application.properties", "database.yml", "Chart.yaml",
    "README.md", "README.rst", "README.txt", "README",
}
SNAPSHOT_MAX_FILES = 5000
SNAPSHOT_MAX_DEPTH_FOR_CONTENT = 3
SNAPSHOT_MAX_FILE_BYTES = 64 * 1024
SNAPSHOT_MAX_TOTAL_BYTES = 400 * 1024


def _wants_content(relative: str, name: str, depth: int) -> bool:
    if name in SNAPSHOT_KEY_FILES:
        return depth <= SNAPSHOT_MAX_DEPTH_FOR_CONTENT
    lowered = relative.lower()
    return name.endswith((".yaml", ".yml")) and ("k8s" in lowered or "kubernetes" in lowered)


def project_snapshot(root: Path) -> Dict[str, Any]:
    """Relative paths of the project's files plus the text of its config files."""
    files: List[str] = []
    contents: Dict[str, str] = {}
    total = 0
    truncated = False
    for dirpath, dirnames, filenames in os.walk(root):
        dirnames[:] = sorted(d for d in dirnames if d not in SNAPSHOT_SKIP_DIRS)
        rel_dir = Path(dirpath).relative_to(root)
        depth = len(rel_dir.parts)
        for name in sorted(filenames):
            relative = (rel_dir / name).as_posix()
            files.append(relative)
            if _wants_content(relative, name, depth) and total < SNAPSHOT_MAX_TOTAL_BYTES:
                try:
                    with open(os.path.join(dirpath, name), "rb") as fh:
                        raw = fh.read(SNAPSHOT_MAX_FILE_BYTES)
                except OSError:
                    raw = b""
                text = raw.decode("utf-8", errors="ignore")
                if name.startswith("README"):
                    text = text[:2000]
                contents[relative] = text
                total += len(text)
            if len(files) >= SNAPSHOT_MAX_FILES:
                truncated = True
                break
        if truncated:
            break
    return {"files": files, "contents": contents, "truncated": truncated}


def inspect_project(path: str, with_snapshot: bool = False) -> Dict[str, Any]:
    """Whether *path* is a folder on this machine, and what repository it holds."""
    candidate = _host_path(path)
    try:
        resolved = candidate.resolve()
    except OSError:
        resolved = candidate
    is_dir = resolved.is_dir()
    result: Dict[str, Any] = {
        "host": host_info(),
        "path": str(resolved),
        "name": resolved.name or str(resolved),
        "exists": resolved.exists(),
        "is_dir": is_dir,
        "git": git_info(resolved) if is_dir else None,
    }
    if with_snapshot and is_dir:
        result["snapshot"] = project_snapshot(resolved)
    return result


async def handle_browse(nc, msg, reply, request_body) -> None:
    """``workspace.browse`` → the folder listing, or ``{"error": {status, message}}``."""
    data = request_body(msg)
    try:
        body = await asyncio.to_thread(browse, data.get("path") or None)
    except WorkspaceError as e:
        body = {"error": {"status": e.status, "message": e.message}}
    await reply(nc, msg, body)


async def handle_inspect(nc, msg, reply, request_body) -> None:
    """``workspace.inspect`` → folder, git and (when asked) file snapshot."""
    data = request_body(msg)
    path = data.get("path")
    if not isinstance(path, str) or not path.strip():
        body = {"error": {"status": 400, "message": "A folder path is required."}}
    else:
        try:
            body = await asyncio.to_thread(inspect_project, path, bool(data.get("snapshot")))
        except WorkspaceError as e:
            body = {"error": {"status": e.status, "message": e.message}}
    await reply(nc, msg, body)


# (subject, handler) for the daemon's subscription loop.
HANDLERS = (
    ("workspace.browse", handle_browse),
    ("workspace.inspect", handle_inspect),
)
