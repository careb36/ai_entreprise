"""An architecture's decisions as ADR files, one per decision (issue #329).

``docs/ARCHITECTURE.md`` (``arch_check.as_markdown``) holds every decision in
one document; a repository that keeps its decisions as files also gets
``docs/adr/NNNN-<slug>.md``, one per decision, in the usual ADR sections
(Status, Context, Decision, Consequences, after Michael Nygard's template).
The text is the record's own, so the files are a rendering: edit the
architecture, not the files.

The number of decision ``ADR-k`` is ``first_number + k - 1``: a repository
whose ADRs started before the factory recorded them (La Fabrique's own start
at 0012) passes its first free number. The file name's slug is the
decision's ``slug`` when the record has one, else the first words of its
title. The status is "Accepted" with the decision's ``date`` (a person's
record; ``arch_author`` drops one a model wrote) or, without one, the date
the architecture was approved; a draft's decisions are "Proposed".

Pure; no I/O.
"""

from __future__ import annotations

import re
from typing import Any

ADR_DIRECTORY = "docs/adr"
SLUG_WORDS = 6


def slug(title: str, words: int = SLUG_WORDS) -> str:
    """The first *words* words of *title*, lowercase, joined by hyphens."""
    parts = re.findall(r"[a-z0-9]+", (title or "").lower())
    return "-".join(parts[:words]) or "decision"


def status_line(
    decision: dict[str, Any],
    arch_title: str,
    spec_title: str,
    version: int,
    approved_on: str | None,
) -> str:
    """The Status section's sentence: accepted when, as which ADR of which architecture."""
    date = decision.get("date") or approved_on
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
) -> str:
    """One decision as an ADR document."""
    lines = [
        f"# ADR {number:04d}: {decision.get('title') or ''}",
        "",
        "## Status",
        "",
        status_line(decision, arch_title, spec_title, version, approved_on),
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
) -> list[dict[str, Any]]:
    """Every decision of a normalized architecture as ``{id, number, path, markdown}``.

    *approved_on* (``YYYY-MM-DD``) is the architecture's approval date, the
    status of a decision that records no date of its own; ``None`` for a draft.
    """
    out = []
    for offset, decision in enumerate(arch.get("decisions") or []):
        number = first_number + offset
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
                ),
            }
        )
    return out
