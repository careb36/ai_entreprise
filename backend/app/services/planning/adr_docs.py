"""An architecture's decisions as ADR files, one per decision (issue #329).

``docs/ARCHITECTURE.md`` (``arch_check.as_markdown``) holds every decision in
one document; a repository that keeps its decisions as files also gets
``docs/adr/NNNN-<slug>.md``, one per decision, in the usual ADR sections
(Status, Context, Decision, Consequences, after Michael Nygard's template).
The text is the record's own, so the files are a rendering: edit the
architecture, not the files.

The number of decision ``ADR-k`` is ``first_number + k - 1`` when the
repository has no file for it: a repository whose ADRs started before the
factory recorded them (La Fabrique's own start at 0012) passes its first free
number. The file name's slug is the decision's ``slug`` when the record has
one, else the first words of its title. The status is "Accepted" with the
decision's ``date`` (a person's record; ``arch_author`` drops one a model
wrote) or, without one, the date the architecture was approved; a draft's
decisions are "Proposed", and a decision the record marks ``superseded_by``
another one says so instead.

A write reads the repository's ``docs/adr`` through the daemon and passes
what it found: ``adr_files`` maps each file's slug to its number, so a
decision already written keeps its file and number, and
``first_free_number`` numbers the new ones after the highest file there.

Pure; no I/O.
"""

from __future__ import annotations

import re
from collections.abc import Iterable
from pathlib import PurePosixPath
from typing import Any

ADR_DIRECTORY = "docs/adr"
SLUG_WORDS = 6

# ``NNNN-<slug>.md`` as the write names them; a slug starts with a letter or
# a digit and goes on in lowercase words and hyphens.
_FILE_NAME = re.compile(r"(\d{1,4})-([a-z0-9][a-z0-9-]*)\.md")
# Any ``NNNN-*.md`` file, however its slug is written: its number is taken.
_NUMBERED = re.compile(r"(\d{1,4})-[^/]*\.md")


def _name(entry: str) -> str:
    return PurePosixPath(str(entry)).name


def slug(title: str, words: int = SLUG_WORDS) -> str:
    """The first *words* words of *title*, lowercase, joined by hyphens."""
    parts = re.findall(r"[a-z0-9]+", (title or "").lower())
    return "-".join(parts[:words]) or "decision"


def adr_files(entries: Iterable[str]) -> dict[str, int]:
    """The ADR files a ``docs/adr`` listing reports, as ``{slug: number}``.

    Only a file named ``NNNN-<slug>.md`` counts; the lowest number wins when
    a slug is there twice. A decision whose slug is in the answer keeps that
    file and number (``adr_documents``).
    """
    out: dict[str, int] = {}
    for entry in entries:
        match = _FILE_NAME.fullmatch(_name(entry))
        if match is not None:
            out.setdefault(match.group(2), int(match.group(1)))
    return out


def first_free_number(entries: Iterable[str]) -> int:
    """One past the highest number an ADR file of the repository holds (1 when none).

    A file whose slug is not well formed still takes its number: reusing it
    would shadow a decision already there. A gap is never filled, so a file
    deleted by hand does not lend its number to a new decision.
    """
    numbers = [
        int(match.group(1))
        for entry in entries
        if (match := _NUMBERED.fullmatch(_name(entry))) is not None
    ]
    return max(numbers, default=0) + 1


def status_line(
    decision: dict[str, Any],
    arch_title: str,
    spec_title: str,
    version: int,
    approved_on: str | None,
    numbers: dict[str, int] | None = None,
) -> str:
    """The Status section's sentence: accepted when, or superseded by which ADR."""
    date = decision.get("date") or approved_on
    superseded_by = decision.get("superseded_by")
    if superseded_by:
        target = (numbers or {}).get(superseded_by)
        head = (
            f"Superseded by ADR {target:04d}."
            if target is not None
            else f"Superseded by {superseded_by}."
        )
    else:
        head = f"Accepted ({date})." if date else "Proposed."
    return (
        f"{head} {decision['id']} of architecture v{version}, *{arch_title}*, "
        f"for the specification *{spec_title}*. Written by La Fabrique from the "
        "architecture record; edit it there, not here."
    )


def as_adr(
    decision: dict[str, Any],
    number: int,
    arch_title: str,
    spec_title: str,
    version: int,
    approved_on: str | None = None,
    numbers: dict[str, int] | None = None,
) -> str:
    """One decision as an ADR document."""
    lines = [
        f"# ADR {number:04d}: {decision.get('title') or ''}",
        "",
        "## Status",
        "",
        status_line(decision, arch_title, spec_title, version, approved_on, numbers),
        "",
    ]
    for heading, key in (
        ("Context", "context"),
        ("Decision", "decision"),
        ("Consequences", "consequences"),
    ):
        if decision.get(key):
            lines += [f"## {heading}", "", decision[key], ""]
    return "\n".join(lines).rstrip() + "\n"


def adr_documents(
    arch: dict[str, Any],
    spec_title: str,
    version: int,
    *,
    first_number: int = 1,
    approved_on: str | None = None,
    existing: dict[str, int] | None = None,
) -> list[dict[str, Any]]:
    """Every decision of a normalized architecture as ``{id, number, path, markdown}``.

    *approved_on* (``YYYY-MM-DD``) is the architecture's approval date, the
    status of a decision that records no date of its own; ``None`` for a draft.
    *existing* maps an ADR file's slug to the number it already holds
    (``adr_files``): a decision whose slug is there keeps that file and
    number, and the others are numbered from *first_number* (the repository's
    first free one, ``first_free_number``), skipping every number taken.
    """
    decisions = [d for d in arch.get("decisions") or [] if isinstance(d, dict)]
    # Numbers already on disk are never reused for a new decision; an existing
    # file's number is kept for the decision whose slug names it, once.
    reserved = set((existing or {}).values())
    used: set[int] = set()
    next_number = first_number
    numbers: list[int] = []
    for decision in decisions:
        name = decision.get("slug") or slug(decision.get("title") or "")
        number = (existing or {}).get(name)
        if number is not None and number not in used:
            used.add(number)
            numbers.append(number)
            continue
        while next_number in reserved or next_number in used:
            next_number += 1
        used.add(next_number)
        numbers.append(next_number)
        next_number += 1
    by_id = {decision["id"]: number for decision, number in zip(decisions, numbers)}
    out = []
    for decision, number in zip(decisions, numbers):
        name = decision.get("slug") or slug(decision.get("title") or "")
        out.append(
            {
                "id": decision["id"],
                "number": number,
                "path": f"{ADR_DIRECTORY}/{number:04d}-{name}.md",
                "markdown": as_adr(
                    decision,
                    number,
                    arch.get("title") or "Architecture",
                    spec_title,
                    version,
                    approved_on,
                    by_id,
                ),
            }
        )
    return out
