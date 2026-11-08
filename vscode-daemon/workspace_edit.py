"""The Developer's edit tool: ``workspace.edit`` and ``workspace.edit.finish``.

Taken from SWE-agent's agent-computer interface (MIT): an edit replaces a
range of lines (or one SEARCH/REPLACE block, the format of ``file_edits``)
and is applied only when the file still parses for its language; otherwise
it is refused with the checker's error and the file is left as it was, so
the model never leaves the project broken halfway through a task.

- Checked in process, nothing runs: Python (``compile``) and JSON
  (``file_edits.syntax_error``). JavaScript (``.js``, ``.mjs``, ``.cjs``) with
  ``node --check`` on a copy outside the project, when ``node`` is on PATH.
  Any other file type is applied unchecked and the reply says so.
- Only an existing text file inside the project, never VCS metadata (the
  daemon's resolver), never a file that may hold secrets
  (``workspace_read.is_sensitive``), at most ``EDIT_MAX_BYTES``.
- Every applied edit is an observation: the sha256 before and after, read
  back from disk.

Edits are a working copy for one task's tool loop (a ``session``): the first
edit of a file keeps its original bytes under ``FABRIQUE_EDIT_STATE``, so
later reads and test runs see the edited file. ``workspace.edit.finish``
answers with each edited file's final text and puts the original back: the
backend then delivers those files through ``workspace.generate`` like any
other delivery, so the daemon's diff, the Git commit and the Verifier see the
change, and an edit that is never delivered never stays on disk.
"""

from __future__ import annotations

import hashlib
import json
import os
import re
import shutil
import subprocess
import tempfile
from pathlib import Path
from typing import Any, Dict, Optional, Tuple

import file_edits
import workspace_read
from workspace_read import ReadError

EDIT_MAX_BYTES = 128 * 1024
# Files one session may edit: their final texts travel back in one NATS reply.
EDIT_MAX_FILES = 5
CONTEXT_LINES = 3
WINDOW_MAX_LINES = 40
NODE_CHECK_TIMEOUT_SECONDS = 15
JS_SUFFIXES = {".js", ".mjs", ".cjs"}
SESSION_PATTERN = re.compile(r"^[a-f0-9]{16,64}$")


def state_dir() -> Path:
    """Where each session keeps the original bytes of the files it edited."""
    return Path(os.getenv("FABRIQUE_EDIT_STATE", "~/.fabrique-daemon/edits")).expanduser()


def _sha256(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def check_syntax(path: str, text: str) -> Tuple[Optional[str], Optional[str]]:
    """``(checker, problem)``: which check ran (``None`` when none applies to
    this file type) and why *text* would not parse, or ``None``."""
    suffix = Path(path).suffix.lower()
    if suffix in {".py", ".json"}:
        return ("python" if suffix == ".py" else "json"), file_edits.syntax_error(path, text)
    node = shutil.which("node") if suffix in JS_SUFFIXES else None
    if not node:
        return None, None
    with tempfile.TemporaryDirectory(prefix="fabrique-check-") as folder:
        copy = Path(folder) / f"check{suffix}"
        copy.write_text(text, encoding="utf-8")
        try:
            done = subprocess.run(
                [node, "--check", str(copy)],
                capture_output=True, text=True, timeout=NODE_CHECK_TIMEOUT_SECONDS,
            )
        except (OSError, subprocess.TimeoutExpired):
            return None, None
    if done.returncode == 0:
        return "node", None
    detail = (done.stderr or done.stdout or "").replace(str(copy), path).strip()
    return "node", "\n".join(detail.splitlines()[:6]) or f"node --check exited {done.returncode}"


def _session_folder(session: Any) -> Path:
    if not isinstance(session, str) or not SESSION_PATTERN.match(session):
        raise ReadError(400, "A session id (hex) is required.")
    return state_dir() / session


def _load_manifest(folder: Path) -> Dict[str, Any]:
    try:
        return json.loads((folder / "manifest.json").read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {"workspace_root": None, "files": {}}


def _write_atomically(target: Path, data: bytes) -> None:
    handle, temporary = tempfile.mkstemp(prefix=".fabrique-edit-", dir=str(target.parent))
    try:
        with os.fdopen(handle, "wb") as out:
            out.write(data)
        shutil.copymode(str(target), temporary)
        os.replace(temporary, target)
    except BaseException:
        if os.path.exists(temporary):
            os.unlink(temporary)
        raise


def _int(value: Any, name: str) -> int:
    try:
        return int(value)
    except (TypeError, ValueError) as exc:
        raise ReadError(400, f"{name} must be a line number.") from exc


def _replace_lines(text: str, data: Dict[str, Any]) -> Tuple[str, int, int]:
    """*text* with lines ``start_line``..``end_line`` (inclusive, 1-based)
    replaced by ``new_text``; ``end_line = start_line - 1`` inserts before
    ``start_line``. Returns the new text and the new text's line range."""
    lines = text.splitlines(keepends=True)
    start = _int(data.get("start_line"), "start_line")
    end = _int(data.get("end_line", start), "end_line")
    if not 1 <= start <= len(lines) + 1 or not start - 1 <= end <= len(lines):
        raise ReadError(
            400, f"Lines {start}–{end} are not in the file, which has {len(lines)} lines."
        )
    new_text = data.get("new_text")
    if not isinstance(new_text, str):
        raise ReadError(400, "new_text is required (empty deletes the lines).")
    if new_text and not new_text.endswith("\n"):
        new_text += "\n"
    result = "".join(lines[: start - 1]) + new_text + "".join(lines[end:])
    return result, start, start + max(new_text.count("\n"), 1) - 1


def _search_replace(text: str, data: Dict[str, Any]) -> Tuple[str, int, int]:
    search, replace = data.get("search"), data.get("replace", "")
    if not isinstance(search, str) or not isinstance(replace, str):
        raise ReadError(400, "search and replace must be text.")
    try:
        result = file_edits.apply_edits(text, [{"search": search, "replace": replace}])
    except file_edits.EditError as exc:
        raise ReadError(422, f"The edit was not applied: {exc}.") from exc
    first = text[: text.find(search)].count("\n") + 1
    return result, first, first + max(replace.count("\n"), 1) - 1


def _window(text: str, first: int, last: int) -> Tuple[str, int, int]:
    lines = text.splitlines()
    low = max(1, first - CONTEXT_LINES)
    high = min(len(lines), last + CONTEXT_LINES, low + WINDOW_MAX_LINES - 1)
    rows = [f"{n:5d}| {lines[n - 1]}" for n in range(low, high + 1)]
    return "\n".join(rows), low, high


def edit_file(workspace_path: str, data: Dict[str, Any], resolve) -> Dict[str, Any]:
    """Apply one edit to an existing file of the project, when it still parses."""
    root = workspace_read._root(workspace_path)
    folder = _session_folder(data.get("session"))
    path = data.get("path")
    target = workspace_read._target(root, path, resolve)
    if not target.is_file():
        raise ReadError(404, f"No such file: {path} (the edit tool changes existing files only).")
    if workspace_read.is_sensitive(root, target):
        raise ReadError(403, f"A file that may hold secrets is never edited: {path}")
    relative = target.relative_to(root).as_posix()
    manifest = _load_manifest(folder)
    if manifest.get("workspace_root") not in (None, str(root)):
        raise ReadError(409, "This session edits another project.")
    files = manifest.setdefault("files", {})
    if relative not in files and len(files) >= EDIT_MAX_FILES:
        raise ReadError(
            409, f"At most {EDIT_MAX_FILES} files may be edited in one task; deliver the others whole."
        )
    before = target.read_bytes()
    if len(before) > EDIT_MAX_BYTES or b"\0" in before[:8192]:
        raise ReadError(413, f"Only text files up to {EDIT_MAX_BYTES} bytes are edited: {path}")
    try:
        text = before.decode("utf-8")
    except UnicodeDecodeError as exc:
        raise ReadError(415, f"Not a UTF-8 text file: {path}") from exc
    if data.get("search") is not None:
        result, first, last = _search_replace(text, data)
    else:
        result, first, last = _replace_lines(text, data)
    if result == text:
        raise ReadError(400, "The edit changes nothing.")
    if len(result.encode("utf-8")) > EDIT_MAX_BYTES:
        raise ReadError(413, f"The edited file would exceed {EDIT_MAX_BYTES} bytes.")
    checker, problem = check_syntax(relative, result)
    if problem:
        raise ReadError(
            422, f"The edit was not applied, the file would not parse ({checker}): {problem}. The file is unchanged."
        )
    if relative not in files:
        folder.mkdir(parents=True, exist_ok=True)
        original = f"{len(files)}.orig"
        (folder / original).write_bytes(before)
        files[relative] = {"original": original, "sha256": _sha256(before)}
        manifest["workspace_root"] = str(root)
        (folder / "manifest.json").write_text(json.dumps(manifest), encoding="utf-8")
    _write_atomically(target, result.encode("utf-8"))
    after = target.read_bytes()
    window, low, high = _window(after.decode("utf-8", errors="replace"), first, last)
    return {
        "path": relative,
        "sha256_before": _sha256(before),
        "sha256_after": _sha256(after),
        "bytes": len(after),
        "lines": len(after.decode("utf-8", errors="replace").splitlines()),
        "checked": checker,
        "first_line": low,
        "last_line": high,
        "text": window,
    }


def finish(workspace_path: str, session: Any) -> Dict[str, Any]:
    """Each file the session edited, with its final text, and the original
    bytes put back. ``changed`` is false for a file edited back to what it was."""
    root = workspace_read._root(workspace_path)
    folder = _session_folder(session)
    manifest = _load_manifest(folder)
    if manifest.get("workspace_root") not in (None, str(root)):
        raise ReadError(409, "This session edits another project.")
    files = []
    for relative, entry in sorted((manifest.get("files") or {}).items()):
        target = root / relative
        original = (folder / entry["original"]).read_bytes()
        try:
            final: Optional[bytes] = target.read_bytes()
        except OSError:
            final = None
        if target.parent.is_dir():
            if target.exists():
                _write_atomically(target, original)
            else:
                target.write_bytes(original)
        files.append({
            "path": relative,
            "content": final.decode("utf-8", errors="replace") if final is not None else None,
            "sha256": _sha256(final) if final is not None else None,
            "sha256_original": entry.get("sha256"),
            "changed": final is not None and final != original,
            "restored": target.is_file() and _sha256(target.read_bytes()) == entry.get("sha256"),
        })
    shutil.rmtree(folder, ignore_errors=True)
    return {"session": session, "files": files}


async def handle_edit(nc, msg, reply, request_body, resolve) -> None:
    """``workspace.edit`` → the hashes before and after, and the edited lines."""
    await workspace_read._serve(nc, msg, reply, request_body, resolve, lambda r, d, s: edit_file(r, d, s))


async def handle_finish(nc, msg, reply, request_body, resolve) -> None:
    """``workspace.edit.finish`` → the final texts; the originals are back on disk."""
    await workspace_read._serve(nc, msg, reply, request_body, resolve, lambda r, d, s: finish(r, d.get("session")))


HANDLERS = (
    ("workspace.edit", handle_edit),
    ("workspace.edit.finish", handle_finish),
)
