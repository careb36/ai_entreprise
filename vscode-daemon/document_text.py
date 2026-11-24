"""The text of a document in the project, for the agents that document it (issue #766).

Documenting a whole project starts from the documents already in it
(docs/design/agent-toolsets.md, "Next changes" item 10): a specification in
DOCX, a manual in PDF. ``workspace.read`` with ``extract: "text"`` answers the
document's text instead of its bytes, in the reply shape of a read (numbered
lines from ``start_line``, bounded), with the sha256 and size of the file's
bytes as this daemon read them, and ``format``. Announced as the
``document_text`` feature of ``workspace.read`` (PROTOCOL.md §5.7).

- **DOCX** with the standard library alone: the archive's ``word/document.xml``
  (at most ``MAX_PART_BYTES`` uncompressed, no DTD accepted, so no entity can
  expand), one line per paragraph, a heading style as ``#`` marks, a tab or a
  line break kept.
- **PDF** is not extracted yet: none of this daemon's dependencies reads PDF
  (``requirements.txt``: nats-py, nkeys, pynacl), and a parser written here
  would misread too many files to be worth its risk. The request is refused
  (415) with that reason; the backend says so to the model.

The path rules are a read's (``workspace_read``): inside the project, symlinks
followed, never a file that may hold secrets. The file is at most
``MAX_DOCUMENT_BYTES``. Nothing is written.
"""

from __future__ import annotations

import hashlib
import io
import re
import zipfile
import xml.etree.ElementTree as ET  # noqa: S405 - no DTD accepted, so no entity expands.
from typing import Any, Dict, List

import workspace_read
from workspace_read import ReadError

MAX_DOCUMENT_BYTES = 20 * 1024 * 1024
MAX_PART_BYTES = 30 * 1024 * 1024
FORMATS = {".docx": "docx", ".pdf": "pdf"}
_W = "{http://schemas.openxmlformats.org/wordprocessingml/2006/main}"
_HEADING = re.compile(r"^(?:heading|título|titulo|berschrift)\s*([1-6])$", re.IGNORECASE)


def _docx_lines(data: bytes) -> List[str]:
    """One line per paragraph of ``word/document.xml``; ReadError when it is not a DOCX."""
    try:
        with zipfile.ZipFile(io.BytesIO(data)) as archive:
            info = archive.getinfo("word/document.xml")
            if info.file_size > MAX_PART_BYTES:
                raise ReadError(413, "The document's text part is too large to extract.")
            with archive.open(info) as part:
                xml = part.read(MAX_PART_BYTES + 1)
    except (zipfile.BadZipFile, KeyError) as exc:
        raise ReadError(415, f"Not a DOCX document: {exc}") from exc
    if len(xml) > MAX_PART_BYTES:
        raise ReadError(413, "The document's text part is too large to extract.")
    if b"<!DOCTYPE" in xml[:4096].upper() or b"<!ENTITY" in xml.upper():
        raise ReadError(415, "The document declares a DTD; it is not read.")
    try:
        body = ET.fromstring(xml)  # noqa: S314 - no DTD, checked above.
    except ET.ParseError as exc:
        raise ReadError(415, f"The document's XML does not parse: {exc}") from exc
    lines: List[str] = []
    for paragraph in body.iter(f"{_W}p"):
        parts: List[str] = []
        for node in paragraph.iter():
            if node.tag == f"{_W}t" and node.text:
                parts.append(node.text)
            elif node.tag == f"{_W}tab":
                parts.append("\t")
            elif node.tag in (f"{_W}br", f"{_W}cr"):
                parts.append("\n")
        text = "".join(parts)
        style = paragraph.find(f"{_W}pPr/{_W}pStyle")
        level = _HEADING.match((style.get(f"{_W}val") or "") if style is not None else "")
        if level and text.strip():
            text = "#" * int(level.group(1)) + " " + text.strip()
        lines.extend(text.split("\n"))
    return lines


def read_document(workspace_path: str, path: Any, start_line: Any, resolve, lines: Any = None) -> Dict[str, Any]:
    """The document's text as a read's reply (module docstring), or ReadError."""
    root = workspace_read._root(workspace_path)
    target = workspace_read._target(root, path, resolve)
    if not target.is_file():
        raise ReadError(404, f"No such file: {path}")
    if workspace_read.is_sensitive(root, target):
        raise ReadError(403, f"A file that may hold secrets is never shown: {path}")
    kind = FORMATS.get(target.suffix.lower())
    if kind is None:
        raise ReadError(415, f"Not a document this daemon extracts (DOCX, PDF): {path}")
    size = target.stat().st_size
    if size > MAX_DOCUMENT_BYTES:
        raise ReadError(413, f"The document is larger than {MAX_DOCUMENT_BYTES} bytes: {path}")
    data = target.read_bytes()[: MAX_DOCUMENT_BYTES + 1]
    if kind == "pdf":
        raise ReadError(415, "PDF text extraction is not available on this daemon yet "
                             "(no PDF library among its dependencies).")
    every = _docx_lines(data)
    window = workspace_read.window_lines(lines)
    try:
        first = max(1, int(start_line or 1))
    except (TypeError, ValueError):
        first = 1
    if first > max(len(every), 1):
        raise ReadError(400, f"start_line {first} is past the end of {path}, which has {len(every)} lines.")
    numbered: List[str] = []
    out = 0
    chosen = every[first - 1 : first - 1 + window]
    truncated = len(every) > first - 1 + len(chosen)
    for offset, line in enumerate(chosen):
        row = f"{first + offset:5d}| {line}"
        out += len(row) + 1
        if out > workspace_read.READ_MAX_BYTES:
            truncated = True
            break
        numbered.append(row)
    return {
        "path": path,
        "sha256": hashlib.sha256(data).hexdigest(),
        "bytes": len(data),
        "binary": False,
        "format": kind,
        "extracted": True,
        "text": "\n".join(numbered),
        "lines": len(every),
        "first_line": first,
        "last_line": first + len(numbered) - 1,
        "truncated": truncated,
    }
