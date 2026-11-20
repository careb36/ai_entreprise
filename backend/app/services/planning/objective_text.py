"""The objective a person wrote, read without a model and never cut.

``display_name`` shortens a text for a label (a sprint's or a task's name),
visibly, with an ellipsis; the text it was taken from is stored whole
elsewhere (``Sprint.objective``, the task's description). ``objective_items``
finds the explicit items of a structured objective (Markdown headings or a
numbered list) so a plan made without a model keeps them as tasks, each with
its text exactly as written.
"""

from __future__ import annotations

import re

ELLIPSIS = "…"

_HEADING = re.compile(r"^(#{1,6})[ \t]+(\S.*?)[ \t#]*$")
_NUMBERED = re.compile(r"^(\d{1,3})[.)][ \t]+(\S.*)$")
_FENCE = re.compile(r"^(```|~~~)")
_MARKUP = re.compile(r"[*_`]+")


def _plain(line: str) -> str:
    """*line* without heading marks, emphasis or extra spaces: for a label."""
    heading = _HEADING.match(line.strip())
    text = heading.group(2) if heading else line
    return " ".join(_MARKUP.sub("", text).split())


def display_name(text: str, limit: int) -> str:
    """A label of at most *limit* characters from the first line of *text*.

    For display only: a shortened label ends with an ellipsis, so it never
    passes for the whole text, which the caller keeps elsewhere.
    """
    first = next((line for line in text.splitlines() if line.strip()), "")
    plain = _plain(first)
    if len(plain) <= limit and len(text.strip().splitlines()) <= 1:
        return plain
    if len(plain) <= limit - 1:
        return plain + ELLIPSIS
    cut = plain[: limit - 1]
    if " " in cut:
        cut = cut.rsplit(" ", 1)[0]
    return cut.rstrip(" ,;:.-") + ELLIPSIS


def _unfenced(lines: list[str]) -> list[bool]:
    """For each line, whether it is outside a fenced code block."""
    outside, inside = [], False
    for line in lines:
        if _FENCE.match(line.strip()):
            outside.append(False)
            inside = not inside
            continue
        outside.append(not inside)
    return outside


def _starts(lines: list[str]) -> list[tuple[int, str]]:
    """The lines that start an item, with the item's title.

    The shallowest heading level that occurs at least twice, else numbered
    list items at the start of a line (at least two); nothing otherwise.
    """
    outside = _unfenced(lines)
    headings: dict[int, list[tuple[int, str]]] = {}
    numbered: list[tuple[int, str]] = []
    for index, line in enumerate(lines):
        if not outside[index]:
            continue
        heading = _HEADING.match(line)
        if heading:
            level = len(heading.group(1))
            headings.setdefault(level, []).append((index, _plain(line)))
            continue
        number = _NUMBERED.match(line)
        if number:
            numbered.append((index, _plain(number.group(2))))
    for level in sorted(headings):
        if len(headings[level]) >= 2:
            return headings[level]
    return numbered if len(numbered) >= 2 else []


def objective_items(objective: str) -> list[dict[str, str]]:
    """The explicit items of *objective*: ``title`` (a plain label, whole) and
    ``text`` (the item's lines exactly as written, its heading included).

    Empty when the objective lists fewer than two items. The text before the
    first item stays in the objective, which the plan keeps whole.
    """
    lines = objective.splitlines()
    starts = _starts(lines)
    items = []
    for position, (index, title) in enumerate(starts):
        end = starts[position + 1][0] if position + 1 < len(starts) else len(lines)
        text = "\n".join(lines[index:end]).rstrip()
        items.append({"title": title, "text": text})
    return items
