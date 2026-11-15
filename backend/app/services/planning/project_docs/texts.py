"""Every fixed string a pack writes, in English and Spanish.

A pack is written in the project's documentation language
(``Company.doc_language``: ``en`` or ``es``). Headings, labels, table
headers, the "no source recorded" lines, the person templates, our C4 labels
and the record statuses all come from here; record text (titles, names,
reasons) and ids stay exactly as recorded. ``TEXTS["en"]`` and
``TEXTS["es"]`` (``texts_en``, ``texts_es``) hold the same keys (``tests/test_project_docs_language.py``
holds them in step). The wording is ours in both languages; the Spanish is a
neutral professional register.

Pure; no I/O.
"""

from __future__ import annotations

from collections.abc import Callable
from typing import Any

from services.planning.project_docs import (
    texts_en,
    texts_es,
    texts_wstg_en,
    texts_wstg_es,
)

LANGUAGES = ("en", "es")
DEFAULT_LANGUAGE = "en"

# The security documents' strings (issue #539) live in their own modules.
TEXTS: dict[str, dict[str, str]] = {
    "en": {**texts_en.TEXTS, **texts_wstg_en.TEXTS},
    "es": {**texts_es.TEXTS, **texts_wstg_es.TEXTS},
}

DOMAIN_NAMES = {
    "es": {
        "Governance": "Gobernanza",
        "Scope": "Alcance",
        "Schedule": "Cronograma",
        "Finance": "Finanzas",
        "Stakeholders": "Interesados",
        "Resources": "Recursos",
        "Risk": "Riesgo",
    }
}
FOCUS_NAMES = {
    "es": {
        "Initiating": "Inicio",
        "Planning": "Planificación",
        "Executing": "Ejecución",
        "Monitoring and Controlling": "Monitoreo y control",
        "Closing": "Cierre",
    }
}
# Record statuses, verdicts and tiers as each language shows them; a value not
# listed is shown as recorded.
_STATUS_ES = {
    "draft": "borrador",
    "approved": "aprobado",
    "superseded": "reemplazado",
    "todo": "pendiente",
    "in_progress": "en curso",
    "in_review": "en revisión",
    "done": "terminada",
    "blocked": "bloqueada",
    "active": "activo",
    "completed": "completado",
    "planned": "planificado",
    "paused": "pausado",
    "cancelled": "cancelado",
    "needs_split": "por dividir",
    "verified": "verificado",
    "refuted": "refutado",
    "unverifiable": "no verificable",
    "high": "alto",
    "medium": "medio",
    "low": "bajo",
    "tests_passed": "pruebas superadas",
    "tests_failed": "pruebas fallidas",
    "untested": "sin pruebas",
    "conflict": "conflicto",
    "incomplete": "incompleta",
    "running": "en curso",
    "pending": "pendiente",
    "kept": "conservado",
    "reverted": "revertido",
    "proposed": "propuesto",
    "rejected": "rechazado",
    "retired": "retirado",
    "escalated": "escalada",
    "decided": "decisión",
    "risk_hold": "retención",
    "carried_over": "traslado",
    "specification": "Especificación",
    "architecture": "Arquitectura",
}


def language(value: Any) -> str:
    """*value* as a supported language, else the default."""
    text = str(value or "").strip().lower()[:2]
    return text if text in LANGUAGES else DEFAULT_LANGUAGE


def of(snapshot: dict[str, Any]) -> str:
    return language(snapshot.get("language"))


def translator(lang: str) -> Callable[..., str]:
    table = TEXTS[language(lang)]

    def tr(key: str, **values: Any) -> str:
        return table[key].format(**values) if values else table[key]

    return tr


def status(lang: str, value: Any) -> str:
    """A recorded status, verdict or tier in *lang* (underscores read as spaces in English)."""
    text = "" if value is None else str(value)
    if language(lang) == "es":
        return _STATUS_ES.get(text, text)
    return text.replace("_", " ")


def domain(lang: str, name: str) -> str:
    return DOMAIN_NAMES.get(language(lang), {}).get(name, name)


def focus(lang: str, name: str) -> str:
    return FOCUS_NAMES.get(language(lang), {}).get(name, name)
