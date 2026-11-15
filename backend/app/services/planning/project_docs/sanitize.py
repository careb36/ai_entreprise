"""Making record text safe to put in a Markdown document or a diagram's source.

Every string a pack shows came from a record a model or a person wrote: a task
title, a module name, a block reason, the TechWriter's prose. It is untrusted
input (``docs/design/PROJECT_DOCUMENTATION.md``, threat model T1–T3), rendered
in the browser and written into the user's repository, so nothing here lets it
become structure:

- ``md_inline`` turns a value into one line of literal text: no heading, list,
  table cell break, link, image, autolink, raw HTML or code span can come out of
  it. Markdown's punctuation is backslash-escaped and ``<``, ``>`` and ``&``
  become entities, so the text reads the same on GitHub and in the app.
- ``md_paragraphs`` does the same per paragraph, for longer prose.
- ``mermaid_label`` keeps a short label made only of letters, digits, spaces
  and a few harmless marks; quotes, brackets, pipes, semicolons, ``%`` (the
  ``%%{init}%%`` directive), backticks and line breaks are dropped, so a label
  cannot close its node, start a new statement (``click``, ``style``) or carry
  markup. Node ids are never built from record text (``c4`` numbers them).

Pure functions; no I/O.
"""

from __future__ import annotations

import re
import unicodedata
from typing import Any

INLINE_LIMIT = 300
LABEL_LIMIT = 60
PROSE_LIMIT = 2000

# Markdown punctuation that can make structure inside a line: emphasis, code
# spans, links and images (no ``[``, so no ``](``), table cells, strikethrough.
_MD_ESCAPE = re.compile(r"([\\`*_\[\]|~])")
# What GFM would turn into a link by itself (an autolink literal).
_AUTOLINK = re.compile(r"(?i)\b(https?|ftp|mailto)(:)|\b(www)(\.)")
# What makes structure at the start of a line: a heading, a list item.
_LINE_START = re.compile(r"^(#|[-+]\s|\d+)([.)]?)")
_SAFE_ID = re.compile(r"[^A-Za-z0-9.\-]")
# Characters a Mermaid label keeps: word characters, spaces and a few marks.
_LABEL_DROP = re.compile(r"[^\w .,/+\-'()@]", re.UNICODE)
_SPACES = re.compile(r"\s+")


def _plain(value: Any) -> str:
    """*value* as text without control or invisible format characters."""
    text = "" if value is None else str(value)
    return "".join(
        ch for ch in text if ch in "\n\t" or unicodedata.category(ch)[0] != "C"
    )


def _cut(text: str, limit: int) -> str:
    return text if len(text) <= limit else text[: limit - 1].rstrip() + "…"


def md_inline(value: Any, limit: int = INLINE_LIMIT) -> str:
    """*value* as one line of literal Markdown text, at most *limit* characters."""
    text = _cut(_SPACES.sub(" ", _plain(value)).strip(), limit)
    text = text.replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")
    text = _MD_ESCAPE.sub(r"\\\1", text)
    return _AUTOLINK.sub(
        lambda m: f"{m.group(1)}\\:" if m.group(1) else f"{m.group(3)}\\.", text
    )


def _line_start(text: str) -> str:
    """*text* with a leading heading or list marker escaped."""
    match = _LINE_START.match(text)
    if match is None:
        return text
    if match.group(1).isdigit():
        if not match.group(2):
            return text
        end = match.end(1)
        return text[:end] + "\\" + text[end:]
    return "\\" + text


def md_paragraphs(value: Any, limit: int = PROSE_LIMIT) -> list[str]:
    """*value* as literal paragraphs (split on blank lines), *limit* characters in
    all; each may start a line."""
    text = _cut(_plain(value).strip(), limit)
    paragraphs = [p for p in re.split(r"\n\s*\n", text) if p.strip()]
    return [_line_start(md_inline(p, limit)) for p in paragraphs]


def md_id(value: Any) -> str:
    """A record id (``REQ-1.2``, ``MOD-3``, ``T-42``) with nothing but its id characters."""
    return _SAFE_ID.sub("", _plain(value))[:40] or "?"


def mermaid_label(value: Any, limit: int = LABEL_LIMIT) -> str:
    """*value* as a short Mermaid label that cannot leave its quotes."""
    text = _LABEL_DROP.sub(" ", _plain(value))
    text = _cut(_SPACES.sub(" ", text).strip(), limit)
    return text or "unnamed"


def plain_label(value: Any, limit: int = LABEL_LIMIT) -> str:
    """*value* as one line of plain text for the JSON the frontend draws (React escapes it)."""
    return _cut(_SPACES.sub(" ", _plain(value)).strip(), limit) or "unnamed"
