"""The shape every document of a pack shares, and its input hash.

A built document is plain data::

    {"key", "title", "framework", "markdown", "sources": [...],
     "missing": [...], "claims": [...], "inputs": [...], "input_sha256"}

``inputs`` names the parts of the snapshot the builder read and
``input_sha256`` is the digest of exactly those parts: the drift check
(``pack.drift``) recomputes it from the current records, so a document is
stale as soon as anything it was built from changed, and only then.
``missing`` lists the sections that had no record behind them (each also says
so in the text); ``claims`` lists the prose a model wrote, which the text
marks as such.

Pure; no I/O.
"""

from __future__ import annotations

from typing import Any

from services.planning.project_docs.refs import NO_SOURCE, Sources, digest
from services.planning.project_docs.sanitize import md_paragraphs

GENERATED_NOTE = (
    "Compiled by La Fabrique from its records. Each statement cites its "
    "source in brackets; the sources are listed at the end."
)
PMBOK_EDITION = "PMBOK Guide, 8th edition (PMI, 2025)"
# The framework argument that asks ``build`` for the PMBOK note of the document's place.
PMBOK = "pmbok"
FOCUS_AREAS = (
    "Initiating",
    "Planning",
    "Executing",
    "Monitoring and Controlling",
    "Closing",
)
DOMAINS = (
    "Governance",
    "Scope",
    "Schedule",
    "Finance",
    "Stakeholders",
    "Resources",
    "Risk",
)
# Where each document sits in the 8th edition: its performance domain and the
# focus area it serves. Our placement, not the book's text.
PLACEMENT: dict[str, tuple[str, str]] = {
    "charter": ("Governance", "Initiating"),
    "scope": ("Scope", "Planning"),
    "wbs": ("Scope", "Planning"),
    "traceability": ("Scope", "Monitoring and Controlling"),
    "schedule": ("Schedule", "Planning"),
    "raci": ("Resources", "Planning"),
    "risks": ("Risk", "Planning"),
    "issues": ("Governance", "Executing"),
    "changes": ("Governance", "Monitoring and Controlling"),
    "lessons": ("Governance", "Executing"),
    "checks": ("Governance", "Monitoring and Controlling"),
}


def pmbok_note(key: str) -> str:
    domain, focus = PLACEMENT[key]
    return (
        f"Structured after the {PMBOK_EDITION}: the {domain} performance domain, "
        f"{focus} focus area. The wording is La Fabrique's own."
    )


def person_section(what: str) -> list[str]:
    """A section only a person can write (the book's AI appendix and our rule
    agree: a person stays accountable): a template, never filled by an agent."""
    return [
        (
            f"> **For a person to complete.** {what} La Fabrique records nothing here "
            "and no agent fills it in; the Owner or a Manager writes it."
        ),
    ]


def input_sha256(snapshot: dict[str, Any], inputs: tuple[str, ...]) -> str:
    return digest({name: snapshot.get(name) for name in inputs})


def missing_line(what: str) -> str:
    """The line a section without a record gets instead of invented content."""
    return f"_{NO_SOURCE}: {what}_"


def claim_block(role: str, prose: str | None, what: str) -> tuple[list[str], bool]:
    """The model's prose as a quoted block marked as a claim; the lines and
    whether there was any."""
    paragraphs = md_paragraphs(prose) if prose else []
    if not paragraphs:
        return [missing_line(f"no {what} was written.")], False
    lines = [
        (
            f"> **Claim.** Written by the {role} from the records above; not "
            "checked against them and not a source for any other statement."
        ),
        ">",
    ]
    for i, paragraph in enumerate(paragraphs):
        if i:
            lines.append(">")
        lines.append(f"> {paragraph}")
    return lines, True


def build(
    key: str,
    title: str,
    framework: str,
    body: list[str],
    sources: Sources,
    snapshot: dict[str, Any],
    inputs: tuple[str, ...],
    missing: list[str],
    claims: list[dict[str, Any]] | None = None,
    persons: list[str] | None = None,
) -> dict[str, Any]:
    if framework == PMBOK:
        framework = pmbok_note(key)
    domain, focus = PLACEMENT.get(key, ("", ""))
    lines = [f"# {title}", "", f"_{GENERATED_NOTE} {framework}_", "", *body, ""]
    markdown = "\n".join(lines) + "\n" + sources.section()
    return {
        "key": key,
        "title": title,
        "framework": framework,
        "markdown": markdown,
        "sources": sources.as_list(),
        "missing": missing,
        "claims": claims or [],
        "persons": persons or [],
        "domain": domain,
        "focus_area": focus,
        "inputs": list(inputs),
        "input_sha256": input_sha256(snapshot, inputs),
    }
