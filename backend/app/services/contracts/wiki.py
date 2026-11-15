"""The project wiki's syntax: citations, facts, statement blocks and links (issue #445).

Plain data and pure functions; no I/O. The store (``services/learning/wiki_store.py``)
resolves the citations this module reads against the records, and the lint
(``services/contracts/wiki_lint.py``) compares them with what was observed.

**Citations** (``citations``). A statement names its sources in square brackets:

- ``[run:123]`` an agent run, ``[verification:45]`` a verification of one;
- ``[task:12]`` a task, ``[note:7]`` a sprint board note, ``[retro:9]`` the
  retrospective of sprint 9;
- ``[file:src/store.py@<sha256>]`` a file as a recorded run observed it (the
  daemon's sha256, 12 to 64 hex characters, a prefix of the full one);
- ``[REQ-1.2]``, ``[NFR-1]`` a requirement or criterion, ``[ADR-3]`` a
  decision of an approved architecture, ``[MOD-2]`` a module (a link only:
  naming a module is not a source for a statement).

**Statement blocks** (``statement_blocks``). A paragraph, a list item, a table
row (not the header or its rule) and a fact line are statements; headings,
blank lines and fenced code are not. An agent's statement must carry at least
one source citation (``uncited``); a person's need not.

**Facts** (``facts``). A fenced block whose info string is ``facts`` holds
``key: value`` lines, each a statement with its own citations::

    ```facts
    auth.token_ttl: 15 minutes [run:12]
    db.engine: PostgreSQL 16 [file:docker-compose.yml@3f2a9c1b7d4e]
    ```

The key is lower case (letters, digits, ``_ . -``); the value is the rest of
the line without its citations, whitespace folded. Two pages that give the
same key different values contradict each other (the lint's third rule).

**Links** (``derive_links``). Every citation, plus the bare ``REQ-…``,
``NFR-…``, ``MOD-…`` and ``ADR-…`` ids of the body: what the page points at,
derived on every save, never sent by a client.
"""

from __future__ import annotations

import re
from typing import Any

KINDS = ("module", "concept", "glossary", "decisions")
STATUSES = ("current", "stale", "proposed")
CURRENT, STALE, PROPOSED = STATUSES
VERSION_STATUSES = ("current", "proposed", "rejected", "superseded")
REJECTED, SUPERSEDED = "rejected", "superseded"
AGENT, PERSON = "agent", "person"
AUTHOR_KINDS = (AGENT, PERSON)

SLUG = re.compile(r"^[a-z0-9][a-z0-9-]{0,79}$")
TITLE_LIMIT = 200
# A person's page; a compiled page is bounded lower (``WIKI_PAGE_MAX_CHARS``).
BODY_LIMIT = 20_000
REASON_LIMIT = 500
FACT_VALUE_LIMIT = 200
MAX_CITATIONS = 200
GLOSSARY_SLUG = "glossary"
DECISIONS_SLUG = "decisions"

# The citations that are a source for a statement; ``mod`` is a link only.
SOURCE_KINDS = ("run", "verification", "task", "note", "retro", "file", "req", "adr")
LINK_KINDS = (*SOURCE_KINDS, "mod")
_NUMBERED = ("run", "verification", "task", "note", "retro")

_CITATION = re.compile(
    r"\[(?:"
    r"(?P<num_kind>run|verification|task|note|retro):(?P<num>\d{1,12})"
    r"|file:(?P<path>[\w./\-]{1,300})@(?P<sha>[0-9a-fA-F]{12,64})"
    r"|(?P<id>(?:REQ|NFR)-\d{1,4}(?:\.\d{1,4})?|ADR-\d{1,4}|MOD-\d{1,4})"
    r")\]"
)
_BARE_ID = re.compile(
    r"(?<![\w\[-])((?:REQ|NFR)-\d{1,4}(?:\.\d{1,4})?|ADR-\d{1,4}|MOD-\d{1,4})\b"
)
_FENCE_OPEN = re.compile(r"^[ \t]{0,3}(```+|~~~+)[ \t]*([\w-]*)")
_HEADING = re.compile(r"^[ \t]{0,3}#{1,6}[ \t]")
_LIST_ITEM = re.compile(r"^[ \t]*(?:[-*+]|\d{1,3}[.)])[ \t]+")
_TABLE_RULE = re.compile(
    r"^[ \t]*\|?[ \t]*:?-{2,}:?[ \t]*(\|[ \t]*:?-{2,}:?[ \t]*)*\|?[ \t]*$"
)
_FACT = re.compile(
    r"^[ \t]*(?P<key>[a-z0-9][a-z0-9_.\-]{0,79})[ \t]*:[ \t]*(?P<rest>.+)$"
)


def safe_path(path: str) -> bool:
    """A file citation's path: relative, no ``..``, no empty part."""
    parts = path.split("/")
    return (
        bool(path)
        and not path.startswith("/")
        and all(part not in ("", ".", "..") for part in parts)
    )


def _id_kind(value: str) -> str:
    head = value.split("-")[0]
    return {"REQ": "req", "NFR": "req", "ADR": "adr", "MOD": "mod"}[head]


def citations(text: str) -> list[dict[str, Any]]:
    """The citations of *text* in order, each ``{kind, ref, sha256?, raw}``;
    a file citation with an unsafe path is left out (``bad_citations`` names it)."""
    found: list[dict[str, Any]] = []
    for match in _CITATION.finditer(text or ""):
        if match.group("num_kind"):
            kind, ref = match.group("num_kind"), str(int(match.group("num")))
            found.append({"kind": kind, "ref": ref, "raw": match.group(0)})
        elif match.group("path"):
            path = match.group("path").removeprefix("./")
            if not safe_path(path):
                continue
            found.append(
                {
                    "kind": "file",
                    "ref": path,
                    "sha256": match.group("sha").lower(),
                    "raw": match.group(0),
                }
            )
        else:
            value = match.group("id").upper()
            found.append({"kind": _id_kind(value), "ref": value, "raw": match.group(0)})
        if len(found) >= MAX_CITATIONS:
            break
    return found


def bad_citations(text: str) -> list[str]:
    """Bracketed things that look like citations but are not valid ones
    (``[run:abc]``, ``[file:../x@...]``, ``[file:x]`` with no hash)."""
    valid = {c["raw"] for c in citations(text)}
    loose = re.findall(
        r"\[(?:run|verification|task|note|retro|file):[^\]]{0,320}\]", text or ""
    )
    return [c for c in dict.fromkeys(loose) if c not in valid][:20]


def is_sourced(text: str) -> bool:
    return any(c["kind"] in SOURCE_KINDS for c in citations(text))


def _block(kind: str, lines: list[str], start: int) -> dict[str, Any]:
    text = "\n".join(lines).strip()
    return {
        "kind": kind,
        "line": start + 1,
        "text": text,
        "citations": citations(text),
    }


def statement_blocks(body: str) -> list[dict[str, Any]]:
    """The body as blocks, in order: ``{kind, line, text, citations}`` where
    kind is ``heading``, ``paragraph``, ``item``, ``row``, ``fact`` or ``code``
    (``table_header`` for a table's header row)."""
    blocks: list[dict[str, Any]] = []
    lines = (body or "").replace("\r\n", "\n").split("\n")
    current: list[str] = []
    current_kind = ""
    current_start = 0
    i = 0

    def flush() -> None:
        nonlocal current, current_kind
        if current and "\n".join(current).strip():
            blocks.append(_block(current_kind, current, current_start))
        current, current_kind = [], ""

    while i < len(lines):
        line = lines[i]
        fence = _FENCE_OPEN.match(line)
        if fence:
            flush()
            marker, info = fence.group(1), fence.group(2).lower()
            j = i + 1
            inner: list[str] = []
            while j < len(lines) and not lines[j].strip().startswith(marker):
                inner.append(lines[j])
                j += 1
            if info == "facts":
                for offset, fact_line in enumerate(inner):
                    if fact_line.strip():
                        blocks.append(_block("fact", [fact_line], i + 1 + offset))
            else:
                blocks.append(_block("code", inner or [""], i))
            i = j + 1
            continue
        stripped = line.strip()
        if not stripped:
            flush()
        elif _HEADING.match(line):
            flush()
            blocks.append(_block("heading", [line], i))
        elif stripped.startswith("|"):
            flush()
            nxt = lines[i + 1] if i + 1 < len(lines) else ""
            if _TABLE_RULE.match(nxt):
                blocks.append(_block("table_header", [line], i))
                i += 2
                continue
            if not _TABLE_RULE.match(line):
                blocks.append(_block("row", [line], i))
        elif _LIST_ITEM.match(line):
            flush()
            current, current_kind, current_start = [line], "item", i
        elif current_kind == "item" and line[:1] in (" ", "\t"):
            current.append(line)
        else:
            if current_kind != "paragraph":
                flush()
                current_kind, current_start = "paragraph", i
            current.append(line)
        i += 1
    flush()
    return blocks


STATEMENT_KINDS = ("paragraph", "item", "row", "fact")


def uncited(blocks: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """The statement blocks with no source citation: ``{line, excerpt}``."""
    return [
        {"line": b["line"], "excerpt": b["text"][:160]}
        for b in blocks
        if b["kind"] in STATEMENT_KINDS
        and not any(c["kind"] in SOURCE_KINDS for c in b["citations"])
    ]


def facts(body: str) -> list[dict[str, Any]]:
    """The ``key: value`` facts of the body's ``facts`` blocks:
    ``{key, value, line, citations}``; the first value of a key on a page wins."""
    seen: set[str] = set()
    out: list[dict[str, Any]] = []
    for block in statement_blocks(body):
        if block["kind"] != "fact":
            continue
        match = _FACT.match(block["text"])
        if not match:
            continue
        key = match.group("key")
        value = " ".join(_CITATION.sub(" ", match.group("rest")).split())
        value = value[:FACT_VALUE_LIMIT]
        if not value or key in seen:
            continue
        seen.add(key)
        out.append(
            {
                "key": key,
                "value": value,
                "line": block["line"],
                "citations": block["citations"],
            }
        )
    return out


def normalize_fact(value: str) -> str:
    """How two facts' values are compared: case and whitespace folded, a
    trailing period dropped."""
    return " ".join(str(value or "").casefold().split()).rstrip(".")


def derive_links(body: str) -> list[dict[str, Any]]:
    """What the body points at: ``{kind, target, sha256}``, one per distinct
    link, citations first then bare ids."""
    links: dict[tuple[str, str, str | None], dict[str, Any]] = {}
    for c in citations(body):
        key = (c["kind"], c["ref"], c.get("sha256"))
        links.setdefault(
            key, {"kind": c["kind"], "target": c["ref"], "sha256": c.get("sha256")}
        )
    for match in _BARE_ID.finditer(body or ""):
        value = match.group(1).upper()
        key = (_id_kind(value), value, None)
        if not any(k[:2] == key[:2] for k in links):
            links[key] = {"kind": key[0], "target": value, "sha256": None}
    return list(links.values())[:MAX_CITATIONS]


def slugify(text: str, prefix: str = "") -> str:
    """A slug from *text*: lower case, ``-`` between words, at most 80 characters."""
    words = re.sub(r"[^a-z0-9]+", "-", str(text or "").casefold()).strip("-")
    slug = f"{prefix}{words}".strip("-")[:80].rstrip("-")
    return slug if SLUG.match(slug) else ""


def check_page(slug: str, kind: str, title: str, body: str) -> str | None:
    """Why a page cannot be written, or None."""
    if not SLUG.match(slug or ""):
        return "slug: lower-case letters, digits and '-', at most 80 characters"
    if kind not in KINDS:
        return f"kind: one of {', '.join(KINDS)}"
    if not (title or "").strip() or len(title) > TITLE_LIMIT:
        return f"title: 1 to {TITLE_LIMIT} characters"
    if len(body or "") > BODY_LIMIT:
        return f"body: at most {BODY_LIMIT} characters"
    return None


# Lines in a page that read like the loop's own control formats. In a prompt
# they are quoted, so a page can never pass for an instruction block: the
# file manifest's fences, the board's NOTE … END, the ScrumMaster's DECISION.
_CONTROL = re.compile(
    r"^[ \t]*(?:(?:```|~~~)\S*[ \t]+path=|NOTE[ \t]+kind=|DECISION\b|END[ \t.]*$|"
    r"<<<<<<<|>>>>>>>|=======)",
    re.IGNORECASE,
)


def for_prompt(body: str) -> str:
    """The body as a prompt shows it: control-looking lines quoted with ``> ``."""
    return "\n".join(
        f"> {line}" if _CONTROL.match(line) else line
        for line in (body or "").split("\n")
    )
