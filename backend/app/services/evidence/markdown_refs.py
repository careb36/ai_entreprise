"""What a Markdown file points at: its links, its headings' anchors, the paths it cites.

Pure parsing for the documentation checks (``doc_links``): no I/O. Fenced code
blocks are skipped (a link or a heading inside one is an example, not a
reference); inline code spans are not links but are where a document names a
path (```src/app.py```).

- ``links``: the target of every inline link and image (``[text](target)``,
  ``![alt](target "title")``) and every reference definition
  (``[name]: target``), in order, with the 1-based line.
- ``anchors``: the anchors a file's headings get on the forges (GitHub's
  rule: lower case, punctuation dropped, spaces to hyphens, a repeat numbered
  ``-1``, ``-2``), plus explicit ``id="…"`` and ``name="…"`` anchors.
- ``code_spans``: the text of every inline code span, with the line.
"""

from __future__ import annotations

import re

_FENCE = re.compile(r"^\s{0,3}(`{3,}|~{3,})")
_INLINE_CODE = re.compile(r"(`+)(.+?)\1")
_INLINE_LINK = re.compile(
    r"!?\[(?:[^\[\]]|\[[^\]]*\])*\]\(\s*(<[^>]*>|[^)\s]+)(?:\s+(?:\"[^\"]*\"|'[^']*'|\([^)]*\)))?\s*\)"
)
_REF_DEF = re.compile(r"^\s{0,3}\[[^\]]+\]:\s*(<[^>]*>|\S+)")
_ATX = re.compile(r"^\s{0,3}(#{1,6})\s+(.*?)\s*#*\s*$")
_SETEXT = re.compile(r"^\s{0,3}(=+|-+)\s*$")
_EXPLICIT = re.compile(r"""\b(?:id|name)\s*=\s*["']([^"']+)["']""")
_MD_LINK_TEXT = re.compile(r"!?\[([^\]]*)\]\([^)]*\)")


def _prose_lines(text: str) -> list[tuple[int, str]]:
    """``(line number, line)`` for every line outside a fenced code block."""
    out: list[tuple[int, str]] = []
    fence: str | None = None
    for number, line in enumerate(text.splitlines(), start=1):
        match = _FENCE.match(line)
        if fence is None and match:
            fence = match.group(1)[0] * len(match.group(1))
            continue
        if fence is not None:
            if match and match.group(1).startswith(fence):
                fence = None
            continue
        out.append((number, line))
    return out


def _without_code(line: str) -> str:
    return _INLINE_CODE.sub(lambda m: " " * len(m.group(0)), line)


def links(text: str) -> list[tuple[int, str]]:
    """``(line, target)`` of every link and reference definition outside code."""
    found: list[tuple[int, str]] = []
    for number, line in _prose_lines(text):
        prose = _without_code(line)
        targets = [m.group(1) for m in _INLINE_LINK.finditer(prose)]
        definition = _REF_DEF.match(prose)
        if definition:
            targets.append(definition.group(1))
        for target in targets:
            target = target.strip()
            if target.startswith("<") and target.endswith(">"):
                target = target[1:-1].strip()
            if target:
                found.append((number, target))
    return found


def slug(heading: str) -> str:
    """The anchor GitHub gives a heading's text."""
    text = _MD_LINK_TEXT.sub(r"\1", heading)
    text = re.sub(r"<[^>]+>", "", text).strip().lower()
    text = re.sub(r"[^\w\- ]", "", text)
    return text.replace(" ", "-")


def anchors(text: str) -> set[str]:
    """Every anchor *text*'s headings get, and its explicit ``id``/``name`` anchors."""
    seen: dict[str, int] = {}
    result: set[str] = set()
    previous = ""
    for _, line in _prose_lines(text):
        heading = None
        atx = _ATX.match(line)
        if atx:
            heading = atx.group(2)
        elif (
            _SETEXT.match(line)
            and previous.strip()
            and not previous.lstrip().startswith(("-", "*", ">", "|"))
        ):
            heading = previous.strip()
        if heading is not None:
            base = slug(heading)
            count = seen.get(base, 0)
            seen[base] = count + 1
            result.add(base if count == 0 else f"{base}-{count}")
        result.update(a.lower() for a in _EXPLICIT.findall(line))
        previous = line
    return result


def code_spans(text: str) -> list[tuple[int, str]]:
    """``(line, text)`` of every inline code span outside fenced blocks."""
    return [
        (number, match.group(2).strip())
        for number, line in _prose_lines(text)
        for match in _INLINE_CODE.finditer(line)
    ]
