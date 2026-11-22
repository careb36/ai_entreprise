"""The pack's own Markdown subset, parsed for export (issue #694, phase 4).

``docs/archive/design/PROJECT_DOCUMENTATION.md``, "Export to DOCX and PDF". The
builders write a known subset: ATX headings, paragraphs, bullet and numbered
lists (nested by indentation), pipe tables, block quotes (the claim marks, the
person sections and the person's statements) and fenced code (the Mermaid
diagrams). Inline, they write ``**strong**``, ``_emphasis_``, code spans,
backslash escapes (``sanitize.md_inline``) and the entities ``&amp;``,
``&lt;`` and ``&gt;``. This module turns that subset into plain blocks the
DOCX and PDF renderers walk; it is not a Markdown implementation and needs no
Markdown library.

Nothing here makes structure the Markdown did not have: there is no raw HTML,
no link and no image in the subset, so none comes out. Every piece of text is
returned as literal text, for the renderer to escape for its own format.

Pure functions; no I/O.
"""

from __future__ import annotations

import html
import re
from dataclasses import dataclass, field

# An inline run: (kind, text), kind one of "text", "strong", "em", "strong_em", "code".
Inline = tuple[str, str]

_HEADING = re.compile(r"^(#{1,6})\s+(.*?)\s*#*\s*$")
_FENCE = re.compile(r"^(```+|~~~+)\s*([\w+-]*)\s*$")
_ITEM = re.compile(r"^(\s*)([-*+]|\d{1,9}[.)])\s+(.*)$")
_SEPARATOR_CELL = re.compile(r"^\s*:?-{1,}:?\s*$")
_PUNCTUATION = set("!\"#$%&'()*+,-./:;<=>?@[\\]^_`{|}~")


@dataclass
class Heading:
    level: int
    inlines: list[Inline]


@dataclass
class Paragraph:
    inlines: list[Inline]


@dataclass
class ListItem:
    inlines: list[Inline]
    children: list[ListBlock] = field(default_factory=list)


@dataclass
class ListBlock:
    ordered: bool
    items: list[ListItem] = field(default_factory=list)


@dataclass
class Table:
    header: list[list[Inline]]
    rows: list[list[list[Inline]]]


@dataclass
class Quote:
    blocks: list[Block]


@dataclass
class Code:
    language: str
    text: str


Block = Heading | Paragraph | ListBlock | Table | Quote | Code


# --- inline ---------------------------------------------------------------------


def _literal(text: str) -> str:
    """Backslash escapes and entities resolved: the text as a reader sees it."""
    out, i = [], 0
    while i < len(text):
        ch = text[i]
        if ch == "\\" and i + 1 < len(text) and text[i + 1] in _PUNCTUATION:
            out.append(text[i + 1])
            i += 2
            continue
        if ch == "&":
            match = re.match(r"&(#\d{1,7}|#x[0-9a-fA-F]{1,6}|[a-zA-Z]{2,8});", text[i:])
            if match:
                out.append(html.unescape(match.group(0)))
                i += match.end()
                continue
        out.append(ch)
        i += 1
    return "".join(out)


def _closing(text: str, start: int, marker: str) -> int:
    """The index of the next unescaped *marker* at or after *start*, or -1."""
    i = start
    while i < len(text):
        if text[i] == "\\":
            i += 2
            continue
        if text.startswith(marker, i):
            return i
        i += 1
    return -1


def _merge(runs: list[Inline]) -> list[Inline]:
    merged: list[Inline] = []
    for kind, text in runs:
        if not text:
            continue
        if merged and merged[-1][0] == kind:
            merged[-1] = (kind, merged[-1][1] + text)
        else:
            merged.append((kind, text))
    return merged


def parse_inline(text: str, outer: str = "text") -> list[Inline]:
    """*text* as runs of literal text, each with its emphasis."""
    runs: list[Inline] = []
    plain_start = i = 0

    def flush(end: int) -> None:
        if end > plain_start:
            runs.append((outer, _literal(text[plain_start:end])))

    while i < len(text):
        ch = text[i]
        if ch == "\\":
            i += 2
            continue
        if ch == "`":
            end = _closing(text, i + 1, "`")
            if end > i + 1:
                flush(i)
                runs.append(("code", text[i + 1 : end]))
                i = plain_start = end + 1
                continue
        elif text.startswith("**", i):
            end = _closing(text, i + 2, "**")
            if end > i + 2:
                flush(i)
                inner = "strong_em" if outer in ("em", "strong_em") else "strong"
                runs.extend(parse_inline(text[i + 2 : end], inner))
                i = plain_start = end + 2
                continue
        elif ch in "_*" and _opens(text, i):
            end = _closing(text, i + 1, ch)
            if end > i + 1 and _closes(text, end):
                flush(i)
                inner = "strong_em" if outer in ("strong", "strong_em") else "em"
                runs.extend(parse_inline(text[i + 1 : end], inner))
                i = plain_start = end + 1
                continue
        i += 1
    flush(len(text))
    return _merge(runs)


def _opens(text: str, i: int) -> bool:
    """A single ``_`` or ``*`` opens emphasis only at a word's start."""
    before = text[i - 1] if i else " "
    after = text[i + 1] if i + 1 < len(text) else " "
    return not before.isalnum() and not after.isspace()


def _closes(text: str, i: int) -> bool:
    after = text[i + 1] if i + 1 < len(text) else " "
    return not after.isalnum() and not text[i - 1].isspace()


def plain(inlines: list[Inline]) -> str:
    """The runs' text without emphasis."""
    return "".join(text for _, text in inlines)


# --- blocks ---------------------------------------------------------------------


def _cells(line: str) -> list[str]:
    """A pipe-table row split on its unescaped pipes."""
    body = line.strip()
    body = body.removeprefix("|")
    if body.endswith("|") and not body.endswith("\\|"):
        body = body[:-1]
    cells, start, i = [], 0, 0
    while i < len(body):
        if body[i] == "\\":
            i += 2
            continue
        if body[i] == "|":
            cells.append(body[start:i])
            start = i + 1
        i += 1
    cells.append(body[start:])
    return [c.strip() for c in cells]


def _is_separator(line: str) -> bool:
    cells = _cells(line)
    return bool(cells) and all(_SEPARATOR_CELL.match(c) for c in cells)


def _starts_block(line: str) -> bool:
    stripped = line.strip()
    return bool(
        _HEADING.match(stripped)
        or _FENCE.match(stripped)
        or stripped.startswith((">", "|"))
        or _ITEM.match(line)
    )


def _table(lines: list[str], i: int) -> tuple[Table, int]:
    header = [parse_inline(c) for c in _cells(lines[i])]
    i += 1
    if i < len(lines) and _is_separator(lines[i]):
        i += 1
    rows = []
    while i < len(lines) and lines[i].strip().startswith("|"):
        rows.append([parse_inline(c) for c in _cells(lines[i])])
        i += 1
    width = max([len(header), *(len(r) for r in rows)])
    header += [[] for _ in range(width - len(header))]
    rows = [r + [[] for _ in range(width - len(r))] for r in rows]
    return Table(header, rows), i


def _list(lines: list[str], i: int) -> tuple[ListBlock, int]:
    """A list and its nested lists; a deeper item nests under the item above it."""
    first = _ITEM.match(lines[i])
    assert first is not None
    root = ListBlock(ordered=first.group(2)[0].isdigit())
    # (indent, list) from the outermost in.
    stack: list[tuple[int, ListBlock]] = [(len(first.group(1)), root)]
    texts: dict[int, list[str]] = {}
    last: ListItem | None = None
    while i < len(lines) and lines[i].strip():
        match = _ITEM.match(lines[i])
        if match is None:
            if last is None or _starts_block(lines[i]):
                break
            texts[id(last)].append(lines[i].strip())
            i += 1
            continue
        indent = len(match.group(1))
        while len(stack) > 1 and indent < stack[-1][0]:
            stack.pop()
        if indent > stack[-1][0] and last is not None:
            child = ListBlock(ordered=match.group(2)[0].isdigit())
            last.children.append(child)
            stack.append((indent, child))
        last = ListItem(inlines=[])
        texts[id(last)] = [match.group(3)]
        stack[-1][1].items.append(last)
        i += 1
    _fill(root, texts)
    return root, i


def _fill(block: ListBlock, texts: dict[int, list[str]]) -> None:
    for item in block.items:
        item.inlines = parse_inline(" ".join(texts[id(item)]))
        for child in item.children:
            _fill(child, texts)


def parse(markdown: str) -> list[Block]:
    """*markdown* (the pack's subset) as a list of blocks."""
    lines = markdown.replace("\r\n", "\n").replace("\r", "\n").split("\n")
    blocks: list[Block] = []
    i = 0
    while i < len(lines):
        line = lines[i]
        stripped = line.strip()
        if not stripped:
            i += 1
            continue
        fence = _FENCE.match(stripped)
        if fence:
            body, i = [], i + 1
            while i < len(lines) and not lines[i].strip().startswith(fence.group(1)):
                body.append(lines[i])
                i += 1
            blocks.append(Code(fence.group(2).lower(), "\n".join(body)))
            i += 1
            continue
        heading = _HEADING.match(stripped)
        if heading:
            blocks.append(
                Heading(len(heading.group(1)), parse_inline(heading.group(2)))
            )
            i += 1
            continue
        if stripped.startswith(">"):
            inner = []
            while i < len(lines) and lines[i].strip().startswith(">"):
                inner.append(re.sub(r"^\s*> ?", "", lines[i]))
                i += 1
            blocks.append(Quote(parse("\n".join(inner))))
            continue
        if stripped.startswith("|"):
            table, i = _table(lines, i)
            blocks.append(table)
            continue
        if _ITEM.match(line):
            block, i = _list(lines, i)
            blocks.append(block)
            continue
        text = [stripped]
        i += 1
        while i < len(lines) and lines[i].strip() and not _starts_block(lines[i]):
            text.append(lines[i].strip())
            i += 1
        blocks.append(Paragraph(parse_inline(" ".join(text))))
    return blocks
