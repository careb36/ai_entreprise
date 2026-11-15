"""The wiki's fixed scaffolding in the project's language, English or Spanish (issue #445).

A page's prose is written by its author in the project's language; what the
factory itself puts on a page is taken from these templates: the section
headings each kind of page has, the decisions page's lines, the words the
compilation request uses to ask for the language, and the lint's reasons.
The citation and facts syntax (``services/contracts/wiki.py``) is the same in
every language: ``[run:12]`` and ``key: value`` lines are never translated.

The language is the company's ``doc_language`` when it has one (``en`` or
``es``); anything else reads as English (``language_of``).
"""

from __future__ import annotations

from typing import Any

LANGUAGES = ("en", "es")
DEFAULT = "en"

_TEMPLATES: dict[str, dict[str, Any]] = {
    "en": {
        "name": "English",
        "sections": {
            "module": [
                "Responsibility",
                "How it works",
                "Interfaces",
                "Facts",
                "Open points",
            ],
            "concept": ["Summary", "Where it lives", "Facts", "Open points"],
            "glossary": ["Terms"],
            "decisions": ["Decisions in force"],
        },
        "decisions_intro": "The decisions of the approved architecture that are in force.",
        "decisions_none": "No decision is recorded in an approved architecture yet.",
        "glossary_title": "Glossary",
        "decisions_title": "Decisions in force",
        "write_in": "Write the page in English. Keep these section headings exactly",
        "lint": {
            "file_missing": "cites {path}, which is no longer on disk",
            "file_changed": "cites {path}@{cited}, observed now as {observed}",
            "module_changed": "verified run #{run_id} changed {path} after this version",
            "contradiction": "says {key} is '{value}'; page {other_page} says '{other_value}'",
        },
    },
    "es": {
        "name": "Spanish",
        "sections": {
            "module": [
                "Responsabilidad",
                "Cómo funciona",
                "Interfaces",
                "Hechos",
                "Puntos abiertos",
            ],
            "concept": ["Resumen", "Dónde vive", "Hechos", "Puntos abiertos"],
            "glossary": ["Términos"],
            "decisions": ["Decisiones vigentes"],
        },
        "decisions_intro": "Las decisiones de la arquitectura aprobada que están vigentes.",
        "decisions_none": "Todavía no hay decisiones registradas en una arquitectura aprobada.",
        "glossary_title": "Glosario",
        "decisions_title": "Decisiones vigentes",
        "write_in": "Escribe la página en español. Mantén exactamente estos encabezados de sección",
        "lint": {
            "file_missing": "cita {path}, que ya no está en el disco",
            "file_changed": "cita {path}@{cited}, observado ahora como {observed}",
            "module_changed": "la ejecución verificada #{run_id} cambió {path} después de esta versión",
            "contradiction": "dice que {key} es '{value}'; la página {other_page} dice '{other_value}'",
        },
    },
}


def language_of(company: Any) -> str:
    """The company's documentation language, ``en`` or ``es``; ``en`` when it
    has none (the setting may not exist yet: read defensively)."""
    value = str(getattr(company, "doc_language", None) or "").strip().lower()[:2]
    return value if value in LANGUAGES else DEFAULT


def template(language: str) -> dict[str, Any]:
    return _TEMPLATES.get(language, _TEMPLATES[DEFAULT])


def sections(kind: str, language: str) -> list[str]:
    """The section headings a page of *kind* has, in *language*."""
    return list(template(language)["sections"].get(kind, []))


def scaffold(kind: str, title: str, language: str) -> str:
    """An empty page of *kind*: its title and its section headings."""
    lines = [f"# {title}", ""]
    for heading in sections(kind, language):
        lines += [f"## {heading}", ""]
    return "\n".join(lines).rstrip() + "\n"


def describe_reason(reason: dict[str, Any], language: str) -> str:
    """One lint reason in *language* (``services/contracts/wiki_lint.py``)."""
    pattern = template(language)["lint"].get(str(reason.get("rule")))
    if pattern is None:
        return str(reason.get("rule"))
    values = {k: str(v) for k, v in reason.items()}
    for key in ("cited", "observed"):
        if key in values:
            values[key] = values[key][:12]
    try:
        return pattern.format(**values)
    except KeyError:
        return str(reason.get("rule"))
