"""The sprint board's notes as plain data: their kinds, bounds and format (issue #439).

A note is a short, typed claim an agent (or a person) leaves for the other
tasks of the same sprint: a decision, an interface or schema change, a
gotcha, a question. An agent writes it in its deliverable, outside every
fenced block, in a fixed form ``parse`` reads::

    NOTE kind=decision files=src/store.py,src/api.py requirements=REQ-1.2
    Timestamps are stored in UTC; convert at the edges.
    END

The attributes are ``kind`` (required), ``files``, ``symbols``,
``requirements``, ``modules`` (comma-separated) and ``supersedes`` (the id
of an earlier note of the sprint this one replaces). A delivery carries at
most ``MAX_NOTES_PER_DELIVERY`` notes of at most ``TEXT_LIMIT`` characters;
anything else is not a note and comes back in ``rejected`` with its reason,
so the worker records why it was ignored. Pure: no database, no I/O.

A note is a claim, never evidence (``docs/POSITIONING.md``): what was
observed about the files it names is read from the Verifier's records and
kept in a field of its own, never in the note.
"""

from __future__ import annotations

import re
from typing import Any

KINDS = ("decision", "interface_change", "schema_change", "gotcha", "question")
LINK_KINDS = ("files", "symbols", "requirements", "modules")
STATUSES = ("active", "superseded", "marked_wrong")
ACTIVE, SUPERSEDED, MARKED_WRONG = STATUSES

TEXT_LIMIT = 500
MAX_NOTES_PER_DELIVERY = 5
MAX_LINKS_PER_KIND = 10
LINK_LIMIT = 200
REASON_LIMIT = 300
EXCERPT_LIMIT = 120

_FENCE = re.compile(r"^```.*?^```[ \t]*$", re.DOTALL | re.MULTILINE)
# ``NOTE`` alone or followed by ``key=value`` pairs; ``NOTE: …`` in prose is not one.
_HEADER = re.compile(
    r"^[ \t]*NOTE(?:[ \t]+(?P<attrs>[A-Za-z_]+=[^\n]*?))?[ \t]*$", re.MULTILINE
)
_END = re.compile(r"^[ \t]*END[ \t.]*$", re.MULTILINE)
_ATTR = re.compile(r"(?P<key>[A-Za-z_]+)=(?P<value>\S*)")
_SAFE_LINK = re.compile(r"^[\w.\-/:#()\[\]<>@+]+$")

INSTRUCTIONS = (
    "To tell the other tasks of this sprint something they must not contradict "
    "(a decision, an interface or schema change, a gotcha, a question), add a note "
    "outside every code block, at most "
    f"{MAX_NOTES_PER_DELIVERY} per delivery, each at most {TEXT_LIMIT} characters:\n\n"
    "NOTE kind=<decision|interface_change|schema_change|gotcha|question> "
    "files=<a.py,b.py> symbols=<Name> requirements=<REQ-1.2> modules=<MOD-1> "
    "supersedes=<note id>\n<the note, short>\nEND\n\n"
    "Only kind is required. A note is your claim: the board shows beside it what "
    "the evidence observed."
)


def normalize_kind(value: Any) -> str | None:
    """``interface-change``, ``Interface Change`` → ``interface_change``; else None."""
    kind = re.sub(r"[\s\-]+", "_", str(value or "").strip().lower())
    return kind if kind in KINDS else None


def clean_links(raw: dict[str, Any] | None) -> tuple[dict[str, list[str]], str | None]:
    """The links, each kind a list of distinct, bounded strings; or the reason
    they are refused (an unsafe file path, a link too long, too many)."""
    links: dict[str, list[str]] = {kind: [] for kind in LINK_KINDS}
    for kind in LINK_KINDS:
        values = (raw or {}).get(kind) or []
        if isinstance(values, str):
            values = values.split(",")
        for value in values:
            text = str(value or "").strip()
            if not text:
                continue
            if len(text) > LINK_LIMIT or not _SAFE_LINK.match(text):
                return links, f"{kind}: {text[:40]!r} is not a valid link"
            if kind == "files":
                text = text.replace("\\", "/").removeprefix("./")
                if text.startswith("/") or ".." in text.split("/"):
                    return links, f"files: {text[:40]!r} is not a relative path"
            if text not in links[kind]:
                links[kind].append(text)
        if len(links[kind]) > MAX_LINKS_PER_KIND:
            return links, f"{kind}: more than {MAX_LINKS_PER_KIND} links"
    return links, None


def check(
    kind: Any, text: Any, links: dict[str, Any] | None = None
) -> tuple[dict[str, Any] | None, str | None]:
    """A note's fields once checked, or the reason it is not a note."""
    normalized = normalize_kind(kind)
    if normalized is None:
        return None, f"unknown kind {str(kind or '')[:40]!r}"
    body = " ".join(str(text or "").split())
    if not body:
        return None, "the note has no text"
    if len(body) > TEXT_LIMIT:
        return None, f"the note is over {TEXT_LIMIT} characters"
    cleaned, reason = clean_links(links)
    if reason:
        return None, reason
    return {"kind": normalized, "text": body, "links": cleaned}, None


def _excerpt(text: str) -> str:
    return " ".join(text.split())[:EXCERPT_LIMIT]


def parse(deliverable: str) -> tuple[list[dict[str, Any]], list[dict[str, str]]]:
    """The notes in *deliverable* and the blocks refused, each with its reason.

    A note is ``{kind, text, links, supersedes}``. Fenced blocks are skipped
    (a file that contains ``NOTE`` is not a note). A ``NOTE`` line with no
    ``END`` before the next ``NOTE`` is refused; past
    ``MAX_NOTES_PER_DELIVERY`` notes the rest are refused.
    """
    text = _FENCE.sub("", (deliverable or "").replace("\r\n", "\n").replace("\r", "\n"))
    notes: list[dict[str, Any]] = []
    rejected: list[dict[str, str]] = []
    headers = list(_HEADER.finditer(text))
    for index, header in enumerate(headers):
        stop = headers[index + 1].start() if index + 1 < len(headers) else len(text)
        end = _END.search(text, header.end(), stop)
        excerpt = _excerpt(header.group(0))
        if end is None:
            rejected.append({"reason": "no END line", "excerpt": excerpt})
            continue
        attrs: dict[str, str] = {}
        unknown = []
        for match in _ATTR.finditer(header["attrs"] or ""):
            key = match["key"].lower()
            if key == "kind" or key == "supersedes" or key in LINK_KINDS:
                attrs[key] = match["value"]
            else:
                unknown.append(key)
        leftover = _ATTR.sub("", header["attrs"] or "").strip()
        if unknown or leftover:
            what = unknown[0] if unknown else leftover[:40]
            rejected.append(
                {"reason": f"unknown attribute {what!r}", "excerpt": excerpt}
            )
            continue
        supersedes: int | None = None
        if attrs.get("supersedes"):
            raw = attrs["supersedes"].lstrip("#")
            if not raw.isdigit():
                rejected.append(
                    {"reason": "supersedes is not a note id", "excerpt": excerpt}
                )
                continue
            supersedes = int(raw)
        note, reason = check(
            attrs.get("kind"),
            text[header.end() : end.start()],
            {kind: attrs.get(kind, "") for kind in LINK_KINDS},
        )
        if note is None:
            rejected.append({"reason": reason or "malformed", "excerpt": excerpt})
            continue
        if len(notes) >= MAX_NOTES_PER_DELIVERY:
            rejected.append(
                {
                    "reason": f"more than {MAX_NOTES_PER_DELIVERY} notes in one delivery",
                    "excerpt": excerpt,
                }
            )
            continue
        notes.append(note | {"supersedes": supersedes})
    return notes, rejected


def requirement_keys(values: list[str]) -> set[str]:
    """``REQ-1.2`` names ``REQ-1`` too, so a note on a requirement reaches the
    tasks covering any of its criteria and the other way round."""
    keys: set[str] = set()
    for value in values:
        key = str(value or "").strip().upper()
        if key:
            keys.add(key)
            keys.add(key.split(".")[0])
    return keys


def _file_matches(note_file: str, scope_paths: set[str]) -> bool:
    """A file matches a scope path that is the same file or a folder holding it
    (a module's ``path``), and a folder note matches the files under it."""
    note_file = note_file.rstrip("/")
    for path in scope_paths:
        path = path.rstrip("/")
        if not path:
            continue
        if (
            note_file == path
            or note_file.startswith(path + "/")
            or path.startswith(note_file + "/")
        ):
            return True
    return False


def relevance(note: dict[str, Any], scope: dict[str, Any]) -> str | None:
    """Why *note* concerns a task with *scope* (``files``, ``modules`` (ids,
    names), ``requirements``): ``"files"``, ``"modules"``, ``"requirements"``,
    ``"sprint"`` for a note with no links (it speaks to the whole sprint), or
    None. Deterministic overlap; no model."""
    links = note.get("links") or {}
    if not any(links.get(kind) for kind in LINK_KINDS):
        return "sprint"
    paths = {str(p) for p in scope.get("files") or []}
    if any(_file_matches(str(f), paths) for f in links.get("files") or []):
        return "files"
    modules = {str(m).strip().lower() for m in scope.get("modules") or []}
    if modules & {str(m).strip().lower() for m in links.get("modules") or []}:
        return "modules"
    if requirement_keys(links.get("requirements") or []) & requirement_keys(
        list(scope.get("requirements") or [])
    ):
        return "requirements"
    return None
