"""The Developer's edit tool: ``workspace.edit`` and ``workspace.edit.finish``.

Taken from SWE-agent's agent-computer interface (MIT): an edit replaces a
range of lines (or one SEARCH/REPLACE block, the format of ``file_edits``)
and is applied only when the file still parses for its language; otherwise
it is refused with the checker's error and the file is left as it was, so
the model never leaves the project broken halfway through a task.

- Checked in process, nothing runs: Python (``compile``) and JSON
  (``file_edits.syntax_error``). JavaScript (``.js``, ``.mjs``, ``.cjs``) with
  ``node --check`` on a copy outside the project, when ``node`` is on PATH
  (a check that cannot complete refuses the edit).
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
import project_lock
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
        except (OSError, subprocess.TimeoutExpired) as exc:
            # node is there but the check could not complete: never unchecked.
            return "node", f"node --check could not complete ({type(exc).__name__})"
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
    if new_text and start > len(lines) and lines and not lines[-1].endswith(("\n", "\r")):
        new_text = "\n" + new_text  # Appended after a last line without its newline.
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


def _saved_target(root: Path, relative: str, resolve) -> Optional[Path]:
    """The file a session edited, when *relative* still leads to it: inside
    the project, not VCS metadata, no symlink along the way, not a file that
    may hold secrets. ``None`` otherwise: nothing is read or written there."""
    try:
        target = workspace_read._target(root, relative, resolve)
    except ReadError:
        return None
    if target != root.joinpath(*Path(relative).parts) or workspace_read.is_sensitive(root, target):
        return None
    return target


def _save_manifest(folder: Path, manifest: Dict[str, Any]) -> None:
    folder.mkdir(parents=True, exist_ok=True)
    (folder / "manifest.json").write_text(json.dumps(manifest), encoding="utf-8")


def _window(text: str, first: int, last: int) -> Tuple[str, int, int]:
    lines = text.splitlines()
    low = max(1, first - CONTEXT_LINES)
    high = min(len(lines), last + CONTEXT_LINES, low + WINDOW_MAX_LINES - 1)
    rows = [f"{n:5d}| {lines[n - 1]}" for n in range(low, high + 1)]
    return "\n".join(rows), low, high


def edit_file(workspace_path: str, data: Dict[str, Any], resolve) -> Dict[str, Any]:
    """Apply one edit to an existing file of the project, when it still parses.

    Under the project's lock (``project_lock``), like a delivery and an undo.
    ``op`` ``move`` and ``delete`` are ``workspace_move.py``'s."""
    import workspace_move

    op = data.get("op") or "edit"
    with project_lock.lock_for(workspace_path):
        if op == "move":
            return workspace_move.move_lines(workspace_path, data, resolve)
        if op == "delete":
            return workspace_move.delete_file(workspace_path, data, resolve)
        if op != "edit":
            raise ReadError(400, f"No such edit operation: {op}")
        return _edit_file(workspace_path, data, resolve)


def _edit_file(workspace_path: str, data: Dict[str, Any], resolve) -> Dict[str, Any]:
    root = workspace_read._root(workspace_path)
    folder = _session_folder(data.get("session"))
    path = data.get("path")
    target = workspace_read._target(root, path, resolve)
    if not target.is_file():
        raise ReadError(
            404,
            f"No such file: {path}. The edit tool changes existing files only: write a new "
            f"file whole in your answer, as a fenced block whose opening line carries path={path}.",
        )
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
    if relative in files and _sha256(before) != files[relative].get("sha256_written"):
        raise ReadError(
            409, f"{path} changed outside the edit tool since your last edit; read it again. Not applied."
        )
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
        # ``sha256_written``: the bytes this session last left in the file.
        files[relative] = {"original": original, "sha256": _sha256(before), "sha256_written": _sha256(before)}
        manifest["workspace_root"] = str(root)
        _save_manifest(folder, manifest)
    _write_atomically(target, result.encode("utf-8"))
    after = target.read_bytes()
    files[relative]["sha256_written"] = _sha256(after)
    _save_manifest(folder, manifest)
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


def finish(workspace_path: str, session: Any, resolve) -> Dict[str, Any]:
    """``_finish`` under the project's lock (``project_lock``)."""
    with project_lock.lock_for(workspace_path):
        return _finish(workspace_path, session, resolve)


def _finish(workspace_path: str, session: Any, resolve) -> Dict[str, Any]:
    """Each file the session edited, with its final text, and the original
    bytes put back. ``changed`` is false for a file edited back to what it was.

    A file is read and restored only while its path still leads to it
    (``_saved_target``) and it holds the bytes the session last wrote. A file
    someone saved meanwhile (``conflict``), or whose path now leads elsewhere,
    is left as it is, never delivered, and its original stays in the journal.

    A file the session created (``created``) is removed, with the folders it
    made when they are empty; one it deleted (``deleted``, no ``content``) is
    written back. A file created and deleted in the same session is neither."""
    root = workspace_read._root(workspace_path)
    folder = _session_folder(session)
    manifest = _load_manifest(folder)
    if manifest.get("workspace_root") not in (None, str(root)):
        raise ReadError(409, "This session edits another project.")
    files, kept = [], {}
    for relative, entry in sorted((manifest.get("files") or {}).items()):
        report: Dict[str, Any] = {"path": relative, "content": None, "sha256": None,
                                  "sha256_original": entry.get("sha256"), "changed": False, "restored": False}
        target = _saved_target(root, relative, resolve)
        current = target.read_bytes() if target is not None and target.is_file() else None
        expected = entry.get("sha256_written")
        if target is None:
            report["error"] = "the path no longer leads to the file the edit tool changed; nothing was read or restored"
        elif (current is None) != (expected is None) or (current is not None and _sha256(current) != expected):
            report["conflict"] = True
            report["error"] = "the file changed outside the edit tool after its last edit; it was left as it is"
        if "error" in report:
            kept[relative] = entry
            files.append(report)
            continue
        original = (folder / entry["original"]).read_bytes() if entry.get("original") else None
        _restore(root, target, original, entry.get("created_dirs") or [])
        if current is None:
            report.update({"deleted": original is not None, "changed": original is not None,
                           "restored": target.is_file() and _sha256(target.read_bytes()) == entry.get("sha256")})
        else:
            report.update({
                "content": current.decode("utf-8", errors="replace"),
                "sha256": _sha256(current),
                "changed": current != original,
                "created": original is None,
                "restored": (not target.exists()) if original is None
                else _sha256(target.read_bytes()) == entry.get("sha256"),
            })
        files.append(report)
    if kept:
        # The originals of the files left alone stay, for a person to compare.
        for relative, entry in (manifest.get("files") or {}).items():
            if relative not in kept and entry.get("original"):  # None: one it created.
                (folder / entry["original"]).unlink(missing_ok=True)
        _save_manifest(folder, {**manifest, "files": kept})
    else:
        shutil.rmtree(folder, ignore_errors=True)
    return {"session": session, "files": files}


def _restore(root: Path, target: Path, original: Optional[bytes], created_dirs: list) -> None:
    """Put *original* back at *target*; ``None``: the session created it, so remove
    it and the folders it made, deepest first, while they are empty."""
    if original is None:
        target.unlink(missing_ok=True)
        for relative in created_dirs:
            try:
                (root / relative).rmdir()
            except OSError:
                break  # Not empty, or already gone: left as it is.
    elif target.exists():
        _write_atomically(target, original)
    else:
        target.write_bytes(original)


async def handle_edit(nc, msg, reply, request_body, resolve) -> None:
    """``workspace.edit`` → the hashes before and after, and the edited lines."""
    await workspace_read._serve(nc, msg, reply, request_body, resolve, lambda r, d, s: edit_file(r, d, s))


async def handle_finish(nc, msg, reply, request_body, resolve) -> None:
    """``workspace.edit.finish`` → the final texts; the originals are back on disk."""
    await workspace_read._serve(nc, msg, reply, request_body, resolve, lambda r, d, s: finish(r, d.get("session"), s))


HANDLERS = (
    ("workspace.edit", handle_edit),
    ("workspace.edit.finish", handle_finish),
)
