"""A person's statements in a documentation pack: the sections only a person can write.

The PMBOK Guide's appendix on AI and ``docs/POSITIONING.md`` agree that a
decision stays a person's: the business case, the budget, the risk appetite.
Phase 1 rendered those sections as templates; phase 2 lets an Owner or a
Manager fill them (``routers/person_sections.py``, the ``PersonStatement``
table). This module is the catalogue of those sections and the way a pack
shows a filled one.

A statement is a **claim** by the person who wrote it, never an observation:

- it lives in its own snapshot key per document (``statements_charter``,
  ``statements_risks``, ``statements_checks``,
  ``statements_status_report``, ``statements_release_plan``), never inside a compiled field, so a builder
  cannot mistake it for a record and a check result never reads it;
- the pack quotes it under its section with the author's company role and the
  time it was recorded, says it is a claim, and cites it as a ``statement``
  source marked as a claim, with the digest the backend computed;
- its text is sanitized like any other record text (``md_paragraphs``).

The keys are each document's input, so filling, changing or withdrawing a
statement makes a compiled pack stale (``pack.lint``).

Pure; no I/O.
"""

from __future__ import annotations

from typing import Any

from services.planning.project_docs import texts
from services.planning.project_docs.layout import person_section
from services.planning.project_docs.refs import Sources, ref
from services.planning.project_docs.sanitize import (
    PROSE_LIMIT,
    md_inline,
    md_paragraphs,
)

# The longest statement accepted, the same bound as the prose a pack quotes.
TEXT_LIMIT = PROSE_LIMIT
# ``<document>.<section>`` -> (document, heading key, "what to write" key).
SECTIONS: dict[str, tuple[str, str, str]] = {
    "charter.justification": (
        "charter",
        "person.justification",
        "person.justification_what",
    ),
    "charter.benefits": ("charter", "person.benefits", "person.benefits_what"),
    "charter.budget": ("charter", "person.budget", "person.budget_what"),
    "charter.sponsor": ("charter", "person.sponsor", "person.sponsor_what"),
    "risks.appetite": ("risks", "person.risk", "person.risk_what"),
    "checks.budget": ("checks", "check.budget", "person.check_budget_what"),
    "checks.external": ("checks", "check.external", "person.check_external_what"),
    "status_report.forecast": (
        "status_report",
        "person.forecast",
        "person.forecast_what",
    ),
    "release_plan.targets": (
        "release_plan",
        "person.targets",
        "person.targets_what",
    ),
}
DOCUMENTS = ("charter", "risks", "checks", "status_report", "release_plan")


def key_of(document: str) -> str:
    """The snapshot key holding *document*'s statements."""
    return f"statements_{document}"


def by_document(current: list[dict[str, Any]]) -> dict[str, dict[str, Any]]:
    """The snapshot keys: per document, ``{section: statement}`` for the
    current, not withdrawn statements in *current* (plain data, no address)."""
    out: dict[str, dict[str, Any]] = {key_of(d): {} for d in DOCUMENTS}
    for statement in current:
        entry = SECTIONS.get(str(statement.get("section")))
        if entry is None or not statement.get("text"):
            continue
        out[key_of(entry[0])][statement["section"]] = statement
    return out


def of(snapshot: dict[str, Any], section: str) -> dict[str, Any] | None:
    entry = SECTIONS[section]
    return (snapshot.get(key_of(entry[0])) or {}).get(section)


def recorded_at(value: Any) -> str:
    """``2026-11-17 14:03 UTC`` from a stored ISO time (kept in UTC)."""
    text = str(value or "")
    return f"{text[:16].replace('T', ' ')} UTC" if len(text) >= 16 else "?"


def summary(snapshot: dict[str, Any], section: str) -> dict[str, Any] | None:
    """What a built document lists about a filled section (no text, no address)."""
    statement = of(snapshot, section)
    if statement is None:
        return None
    return {
        "section": section,
        "statement_id": statement.get("id"),
        "author_role": statement.get("author_role"),
        "recorded_at": statement.get("created_at"),
        "sha256": statement.get("statement_sha256"),
    }


def lines(snapshot: dict[str, Any], section: str, src: Sources) -> list[str]:
    """The body of *section*: the person's statement quoted as a claim with its
    author's role and time, or the template when nobody has written it."""
    lang = texts.of(snapshot)
    tr = texts.translator(lang)
    _, heading, what = SECTIONS[section]
    statement = of(snapshot, section)
    paragraphs = md_paragraphs(statement.get("text"), TEXT_LIMIT) if statement else []
    if not paragraphs:
        return person_section(lang, tr(what))
    assert statement is not None
    mark = src.cite(
        ref(
            "statement",
            statement.get("id"),
            sha256=statement.get("statement_sha256"),
            label=tr(heading),
            claim=True,
        )
    )
    out = [
        tr(
            "statement_header",
            role=md_inline(statement.get("author_role") or "?", 40),
            at=recorded_at(statement.get("created_at")),
            mark=mark,
        ),
        ">",
    ]
    for i, paragraph in enumerate(paragraphs):
        if i:
            out.append(">")
        out.append(f"> {paragraph}")
    return out


def filled(snapshot: dict[str, Any], document: str) -> list[dict[str, Any]]:
    """The summaries of *document*'s filled sections, in catalogue order."""
    return [
        s
        for key, entry in SECTIONS.items()
        if entry[0] == document and (s := summary(snapshot, key)) is not None
    ]
