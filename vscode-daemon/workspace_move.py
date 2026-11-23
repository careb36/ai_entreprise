"""The edit session's ``move`` and ``delete`` operations (``workspace.edit`` with ``op``).

Design: ``docs/design/move-and-delete-files.md``. In sprint 6 (2026-11-23) a
Developer could split a 642-line test file only by retyping it: it wrote tests
from memory that called functions that do not exist, and the original could
not be removed. Here:

- ``move`` cuts a range of lines from one file and inserts the same bytes into
  another (created when missing) or elsewhere in the same file. Both files are
  changed or neither, and both must still parse. The daemon checks the moved
  bytes landed where it put them and reports their sha256 (``moved_sha256``),
  computed from disk, never taken from the request.
- ``delete`` removes a file only once nothing is left in it (empty or
  whitespace). Code cannot vanish through it: every line left the file by a
  move, with its bytes checked, or by an edit, each recorded.

Both work on the session's working copy like an edit (``workspace_edit.py``):
the first touch of a file keeps its original bytes, a file the session created
has none, and ``workspace.edit.finish`` reports a created file's text, a
deleted file as ``deleted``, and puts every original back.
"""

from __future__ import annotations

import os
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

import workspace_read
import workspace_edit as edit
from workspace_read import ReadError


def _text_of(root: Path, target: Path, path: Any) -> str:
    """A file the session may change: not a secret, text, within the size bound."""
    if workspace_read.is_sensitive(root, target):
        raise ReadError(403, f"A file that may hold secrets is never edited: {path}")
    data = target.read_bytes()
    if len(data) > edit.EDIT_MAX_BYTES or b"\0" in data[:8192]:
        raise ReadError(413, f"Only text files up to {edit.EDIT_MAX_BYTES} bytes are edited: {path}")
    try:
        return data.decode("utf-8")
    except UnicodeDecodeError as exc:
        raise ReadError(415, f"Not a UTF-8 text file: {path}") from exc


def _session(folder: Path, root: Path) -> Tuple[Dict[str, Any], Dict[str, Any]]:
    manifest = edit._load_manifest(folder)
    if manifest.get("workspace_root") not in (None, str(root)):
        raise ReadError(409, "This session edits another project.")
    return manifest, manifest.setdefault("files", {})


def _check_current(files: Dict[str, Any], relative: str, data: Optional[bytes], path: Any) -> None:
    """A file the session touched must hold what it last left there."""
    if relative not in files:
        return
    now = edit._sha256(data) if data is not None else None
    if now != files[relative].get("sha256_written"):
        raise ReadError(409, f"{path} changed outside the edit tool since your last change; read it again. Not applied.")


def _room(files: Dict[str, Any], relatives: List[str]) -> None:
    new = [r for r in dict.fromkeys(relatives) if r not in files]
    if len(files) + len(new) > edit.EDIT_MAX_FILES:
        raise ReadError(409, f"At most {edit.EDIT_MAX_FILES} files may be edited in one task; deliver the others whole.")


def _remember(folder: Path, manifest: Dict[str, Any], root: Path, relative: str,
              before: Optional[bytes], created_dirs: Optional[List[str]] = None) -> None:
    """The original bytes of a file the session touches first (``None``: it created it)."""
    files = manifest["files"]
    if relative in files:
        return
    folder.mkdir(parents=True, exist_ok=True)
    entry: Dict[str, Any] = {"original": None, "sha256": None, "sha256_written": None}
    if before is not None:
        entry["original"] = f"{len(files)}.orig"
        (folder / entry["original"]).write_bytes(before)
        entry["sha256"] = entry["sha256_written"] = edit._sha256(before)
    else:
        entry["created_dirs"] = created_dirs or []
    files[relative] = entry
    manifest["workspace_root"] = str(root)
    edit._save_manifest(folder, manifest)


def _write(target: Path, data: bytes) -> None:
    if target.exists():
        edit._write_atomically(target, data)
    else:
        with open(target, "xb") as out:
            out.write(data)


def _new_dirs(root: Path, target: Path) -> List[str]:
    """The folders above *target* that do not exist yet, deepest first."""
    missing, folder = [], target.parent
    while folder != root and not folder.exists():
        missing.append(folder.relative_to(root).as_posix())
        folder = folder.parent
    return missing


def _lines(data: Dict[str, Any], count: int) -> Tuple[int, int]:
    start = edit._int(data.get("start_line"), "start_line")
    end = edit._int(data.get("end_line", start), "end_line")
    if not 1 <= start <= end <= count:
        raise ReadError(400, f"Lines {start}–{end} are not in the file, which has {count} lines.")
    return start, end


def _insert_at(data: Dict[str, Any], count: int) -> int:
    """The 0-based index ``to_line`` inserts before; the end without one."""
    if data.get("to_line") is None:
        return count
    to_line = edit._int(data.get("to_line"), "to_line")
    if not 1 <= to_line <= count + 1:
        raise ReadError(400, f"to_line {to_line} is not in the target, which has {count} lines (use {count + 1} for the end).")
    return to_line - 1


def move_lines(workspace_path: str, data: Dict[str, Any], resolve) -> Dict[str, Any]:
    """Cut lines ``start_line``..``end_line`` of ``path`` and insert the same bytes
    before ``to_line`` of ``to_path`` (the end without it; a new file when missing)."""
    root = workspace_read._root(workspace_path)
    folder = edit._session_folder(data.get("session"))
    path, to_path = data.get("path"), data.get("to_path")
    if not isinstance(to_path, str) or not to_path.strip():
        raise ReadError(400, "to_path is required: the file the lines go to.")
    source = workspace_read._target(root, path, resolve)
    target = workspace_read._target(root, to_path, resolve)
    if not source.is_file():
        raise ReadError(404, f"No such file: {path}")
    if target.exists() and not target.is_file():
        raise ReadError(400, f"{to_path} is not a file.")
    src_rel = source.relative_to(root).as_posix()
    dst_rel = target.relative_to(root).as_posix()
    manifest, files = _session(folder, root)
    _room(files, [src_rel, dst_rel])
    src_before = source.read_bytes()
    _check_current(files, src_rel, src_before, path)
    lines = _text_of(root, source, path).splitlines(keepends=True)
    start, end = _lines(data, len(lines))
    block = lines[start - 1 : end]
    if not block[-1].endswith(("\n", "\r")):
        block[-1] += "\n"  # The file's last line, without its newline.
    moved = "".join(block)
    rest = lines[: start - 1] + lines[end:]
    same = src_rel == dst_rel
    created = not same and not target.exists()
    dst_before: Optional[bytes] = None
    if same:
        index = _insert_at(data, len(lines))
        if start - 1 < index <= end:
            raise ReadError(400, "to_line falls inside the lines being moved: the move changes nothing.")
        index = index - len(block) if index > end else index
        dst_lines = rest
    elif created:
        if workspace_read.is_sensitive(root, target):
            raise ReadError(403, f"A file that may hold secrets is never written: {to_path}")
        if data.get("to_line") not in (None, 1):
            raise ReadError(400, f"{to_path} is a new file: leave to_line out.")
        index, dst_lines = 0, []
    else:
        dst_before = target.read_bytes()
        _check_current(files, dst_rel, dst_before, to_path)
        dst_lines = _text_of(root, target, to_path).splitlines(keepends=True)
        index = _insert_at(data, len(dst_lines))
        if index == len(dst_lines) and dst_lines and not dst_lines[-1].endswith(("\n", "\r")):
            dst_lines[-1] += "\n"
    if same and index == start - 1:
        raise ReadError(400, "The move changes nothing.")
    dst_result = "".join(dst_lines[:index]) + moved + "".join(dst_lines[index:])
    src_result = dst_result if same else "".join(rest)
    for relative, text in {src_rel: src_result, dst_rel: dst_result}.items():
        if len(text.encode("utf-8")) > edit.EDIT_MAX_BYTES:
            raise ReadError(413, f"{relative} would exceed {edit.EDIT_MAX_BYTES} bytes.")
        checker, problem = edit.check_syntax(relative, text)
        if problem:
            raise ReadError(422, f"The move was not applied, {relative} would not parse ({checker}): {problem}. Both files are unchanged.")
    offset = len("".join(dst_lines[:index]).encode("utf-8"))
    if dst_result.encode("utf-8")[offset : offset + len(moved.encode("utf-8"))] != moved.encode("utf-8"):
        raise ReadError(500, "The moved lines would not land intact; nothing was applied.")
    dirs = _new_dirs(root, target) if created else []
    _remember(folder, manifest, root, src_rel, src_before)
    _remember(folder, manifest, root, dst_rel, dst_before, dirs)
    if created:
        target.parent.mkdir(parents=True, exist_ok=True)
    if not same:
        _write(source, src_result.encode("utf-8"))
    _write(target, dst_result.encode("utf-8"))
    src_after, dst_after = source.read_bytes(), target.read_bytes()
    files[src_rel]["sha256_written"] = edit._sha256(src_after)
    files[dst_rel]["sha256_written"] = edit._sha256(dst_after)
    edit._save_manifest(folder, manifest)
    window, low, high = edit._window(dst_after.decode("utf-8", errors="replace"), index + 1, index + len(block))
    return {
        "op": "move",
        "path": src_rel,
        "to_path": dst_rel,
        "moved_lines": len(block),
        "moved_sha256": edit._sha256(moved.encode("utf-8")),
        "source": {"path": src_rel, "sha256_before": edit._sha256(src_before),
                   "sha256_after": edit._sha256(src_after), "lines": len(src_after.decode("utf-8", errors="replace").splitlines())},
        "target": {"path": dst_rel, "sha256_before": edit._sha256(dst_before) if dst_before is not None else (edit._sha256(src_before) if same else None),
                   "sha256_after": edit._sha256(dst_after), "lines": len(dst_after.decode("utf-8", errors="replace").splitlines()),
                   "created": created},
        "first_line": low,
        "last_line": high,
        "text": window,
    }


def delete_file(workspace_path: str, data: Dict[str, Any], resolve) -> Dict[str, Any]:
    """Remove ``path`` from the working copy, only once nothing is left in it."""
    root = workspace_read._root(workspace_path)
    folder = edit._session_folder(data.get("session"))
    path = data.get("path")
    target = workspace_read._target(root, path, resolve)
    if not target.is_file():
        raise ReadError(404, f"No such file: {path}")
    relative = target.relative_to(root).as_posix()
    manifest, files = _session(folder, root)
    _room(files, [relative])
    before = target.read_bytes()
    _check_current(files, relative, before, path)
    text = _text_of(root, target, path)
    if text.strip():
        raise ReadError(
            409,
            f"{path} still has {len(text.splitlines())} lines. Move them out first (workspace__move); "
            "a file is deleted only once it is empty. A person deletes a file that still has content.",
        )
    _remember(folder, manifest, root, relative, before)
    os.unlink(target)
    files[relative]["sha256_written"] = None
    files[relative]["deleted"] = True
    edit._save_manifest(folder, manifest)
    entry = files[relative]
    return {"op": "delete", "path": relative, "deleted": True,
            "sha256_before": entry.get("sha256"), "created_by_session": entry.get("original") is None}


def deliver_delete(root: Path, target: Path, file_info: Dict[str, Any], requested: str) -> Dict[str, Any]:
    """A delivery's ``delete`` entry (``write_files``): the file is removed only
    when it still holds the bytes the edit session saw (``sha256_expected``, the
    daemon's own ``finish`` report), is a regular file and may not hold secrets.
    ``deleted`` with ``sha256_before``, or ``rejected_delete`` with the reason."""
    report: Dict[str, Any] = {"path": requested, "absolute_path": str(target)}
    expected = file_info.get("sha256_expected")
    if not target.is_file() or target.is_symlink():
        return {**report, "status": "rejected_delete", "error": "no such regular file"}
    if workspace_read.is_sensitive(root, target):
        return {**report, "status": "rejected_delete", "error": "a file that may hold secrets is never deleted"}
    data = target.read_bytes()
    if not isinstance(expected, str) or edit._sha256(data) != expected:
        return {**report, "status": "rejected_delete",
                "error": "the file changed since the edit session saw it; it was left as it is"}
    os.unlink(target)
    return {**report, "status": "deleted", "sha256_before": expected, "bytes": 0}
