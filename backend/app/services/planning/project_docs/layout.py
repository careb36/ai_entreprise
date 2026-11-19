"""The shape every document of a pack shares, its placement and its input hash.

A built document is plain data::

    {"key", "title", "framework", "markdown", "sources": [...],
     "missing": [...], "claims": [...], "persons": [...], "statements": [...],
     "domain",
     "focus_area", "inputs": [...], "input_sha256"}

``inputs`` names the parts of the snapshot the builder read and
``input_sha256`` is the digest of exactly those parts: the drift check
(``pack.lint``) recomputes it from the current records, so a document is
stale as soon as anything it was built from changed, and only then. The
language is not an input: a pack in another language than the company's
current one is flagged by the lint on its own. ``missing`` lists the sections
that had no record behind them, ``persons`` the sections only a person can
write and ``claims`` the prose a model wrote; all three in the pack's
language. ``statements`` lists the person sections a person has filled
(``statements.summary``: the statement's id, author role, time and digest),
kept apart from ``claims``, which are a model's. ``domain`` and ``focus_area`` stay the English names of
``DOMAINS`` and ``FOCUS_AREAS`` (data, not text); the Markdown shows them in
the pack's language.

Pure; no I/O.
"""

from __future__ import annotations

from collections.abc import Callable
from typing import Any

from services.planning.project_docs import texts
from services.planning.project_docs.refs import Sources, digest
from services.planning.project_docs.sanitize import md_paragraphs

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
# Where each document sits in the PMBOK Guide, 8th edition: its performance
# domain and the focus area it serves. Our placement, not the book's text.
PLACEMENT: dict[str, tuple[str, str]] = {
    "charter": ("Governance", "Initiating"),
    "scope": ("Scope", "Planning"),
    "wbs": ("Scope", "Planning"),
    # Quality is planned in Scope; communications in Stakeholders.
    "quality_plan": ("Scope", "Planning"),
    "traceability": ("Scope", "Monitoring and Controlling"),
    "schedule": ("Schedule", "Planning"),
    "release_plan": ("Schedule", "Planning"),
    "raci": ("Resources", "Planning"),
    # Stakeholders are identified at the start, then communications are planned.
    "stakeholder_register": ("Stakeholders", "Initiating"),
    "communications_plan": ("Stakeholders", "Planning"),
    "risks": ("Risk", "Planning"),
    "issues": ("Governance", "Executing"),
    "changes": ("Governance", "Monitoring and Controlling"),
    "lessons": ("Governance", "Executing"),
    "checks": ("Governance", "Monitoring and Controlling"),
    "status_report": ("Governance", "Monitoring and Controlling"),
    "final_report": ("Governance", "Closing"),
    # The ITIL transition set (#696): each release's change, notes, known
    # errors and handover, as the work is executed.
    "transition": ("Governance", "Executing"),
    # Security testing is quality (Scope); its findings are risk (issue #539).
    "wstg_checklist": ("Scope", "Monitoring and Controlling"),
    "security_report": ("Risk", "Monitoring and Controlling"),
}


def tr_of(snapshot: dict[str, Any]) -> tuple[str, Callable[..., str]]:
    """The snapshot's language and its translator."""
    lang = texts.of(snapshot)
    return lang, texts.translator(lang)


def pmbok_note(key: str, lang: str) -> str:
    domain, focus = PLACEMENT[key]
    return texts.translator(lang)(
        "pmbok_note", domain=texts.domain(lang, domain), focus=texts.focus(lang, focus)
    )


def person_section(lang: str, what: str) -> list[str]:
    """A section only a person can write (the PMBOK Guide's AI appendix and our
    rule agree: a person stays accountable): a template, never filled by an agent."""
    return [texts.translator(lang)("person_section", what=what)]


def input_sha256(snapshot: dict[str, Any], inputs: tuple[str, ...]) -> str:
    return digest({name: snapshot.get(name) for name in inputs})


def missing_line(lang: str, what: str) -> str:
    """The line a section without a record gets instead of invented content."""
    return texts.translator(lang)("missing_line", what=what)


def claim_block(
    lang: str, role: str, prose: str | None, what: str
) -> tuple[list[str], bool]:
    """The model's prose as a quoted block marked as a claim; the lines and
    whether there was any."""
    tr = texts.translator(lang)
    paragraphs = md_paragraphs(prose) if prose else []
    if not paragraphs:
        return [missing_line(lang, tr("claim_none", what=what))], False
    lines = [tr("claim_header", role=role), ">"]
    for i, paragraph in enumerate(paragraphs):
        if i:
            lines.append(">")
        lines.append(f"> {paragraph}")
    return lines, True


def build(
    key: str,
    framework: str,
    body: list[str],
    sources: Sources,
    snapshot: dict[str, Any],
    inputs: tuple[str, ...],
    missing: list[str],
    claims: list[dict[str, Any]] | None = None,
    persons: list[str] | None = None,
    statements: list[dict[str, Any]] | None = None,
) -> dict[str, Any]:
    lang, tr = tr_of(snapshot)
    title = tr(f"title.{key}")
    if framework == PMBOK:
        framework = pmbok_note(key, lang)
    domain, focus = PLACEMENT.get(key, ("", ""))
    lines = [f"# {title}", "", f"_{tr('generated_note')} {framework}_", "", *body, ""]
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
        "statements": statements or [],
        "domain": domain,
        "focus_area": focus,
        "inputs": list(inputs),
        "input_sha256": input_sha256(snapshot, inputs),
    }
