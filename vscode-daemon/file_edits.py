"""Edits instead of whole files, and a syntax check before anything is written.

Two ideas taken from other coding agents:

- **SEARCH/REPLACE edits** (Aider, Apache-2.0): a delivered file may carry
  ``edits`` instead of ``content``, each a ``search`` text that must appear
  **exactly once** in the current file and the ``replace`` text that takes
  its place. Fewer tokens than a whole file, and no truncated files. An edit
  whose search text is missing or ambiguous is refused, never guessed.
- **Check before writing** (SWE-agent's linted edit, MIT): the resulting text
  of a ``.py`` or ``.json`` file must still parse (``compile`` and
  ``json.loads``, in process: nothing runs). A file that does not parse is
  not written, so the project is never left broken halfway through a task.

The functions are pure (text in, text out) so ``write_files`` decides what
reaches the disk.
"""

from __future__ import annotations

import difflib
import json
from pathlib import Path
from typing import Any


class EditError(ValueError):
    """An edit that cannot be applied as given; the message says why."""


def occurrences(text: str, search: str) -> int:
    """How many positions *search* starts at in *text*, overlaps included
    (``str.count`` skips them, so ``"a\na\n"`` in ``"a\na\na\n"`` would read as once)."""
    count, start = 0, text.find(search)
    while start != -1:
        count += 1
        start = text.find(search, start + 1)
    return count


def closest_line(text: str, search: str) -> str:
    """A pointer to where the missing *search* text was likely meant: the file's
    line closest to the search's first non-blank line, with its number. A hint
    for the next attempt, never applied; empty when nothing is close."""
    first = next((line.strip() for line in search.splitlines() if line.strip()), "")
    lines = text.splitlines()
    stripped = [line.strip() for line in lines]
    near = difflib.get_close_matches(first, stripped, n=1, cutoff=0.6) if first else []
    if not near:
        return ""
    number = stripped.index(near[0]) + 1
    return f"; the closest line is {number}: {lines[number - 1].strip()[:160]!r}"


def apply_edits(original: str, edits: Any) -> str:
    """Apply SEARCH/REPLACE *edits* to *original*, in order."""
    if not isinstance(edits, list) or not edits:
        raise EditError("no edits given")
    text = original
    for number, edit in enumerate(edits, 1):
        if not isinstance(edit, dict):
            raise EditError(f"edit {number} is not a search/replace pair")
        search = str(edit.get("search") or "")
        replace = str(edit.get("replace") or "")
        if not search:
            raise EditError(f"edit {number} has an empty search text")
        count = occurrences(text, search)
        if count == 0:
            raise EditError(
                f"edit {number}: the search text is not in the file{closest_line(text, search)}"
            )
        if count > 1:
            raise EditError(
                f"edit {number}: the search text appears {count} times; "
                "include more surrounding lines so it matches once"
            )
        text = text.replace(search, replace, 1)
    return text


def syntax_error(path: str, text: str) -> str | None:
    """Why *text* would not parse as the file *path* is, or ``None``.

    Only languages checkable in process without running anything: Python
    (``compile``) and JSON. Every other file passes."""
    suffix = Path(path).suffix.lower()
    try:
        if suffix == ".py":
            compile(text, path, "exec", dont_inherit=True)
        elif suffix == ".json":
            json.loads(text)
    except SyntaxError as exc:
        return f"{type(exc).__name__} at line {exc.lineno}: {exc.msg}"
    except ValueError as exc:  # json.JSONDecodeError
        return f"invalid JSON: {exc}"
    return None


def resolve_content(target: Path, file_info: dict[str, Any]) -> tuple[str | None, dict[str, Any]]:
    """The text to write for *file_info*, or ``None`` with a refusal.

    Returns ``(text, extra)``: *extra* goes into the daemon's report for the
    file (``edits_applied``; or ``status`` and ``error`` when refused)."""
    extra: dict[str, Any] = {}
    if file_info.get("edits") is not None:
        if not target.is_file():
            return None, {"status": "rejected_edit", "error": "edits need an existing file"}
        try:
            text = apply_edits(target.read_text(encoding="utf-8"), file_info["edits"])
        except (EditError, UnicodeDecodeError) as exc:
            return None, {"status": "rejected_edit", "error": str(exc)}
        extra["edits_applied"] = len(file_info["edits"])
    else:
        text = str(file_info.get("content", ""))
    problem = syntax_error(str(file_info.get("path", "")), text)
    if problem:
        return None, {"status": "rejected_syntax", "error": problem}
    return text, extra
