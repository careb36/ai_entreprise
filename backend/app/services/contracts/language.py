"""The language an agent writes to the person in.

A company has one language for what is written to its people,
``Company.doc_language`` (``en`` or ``es``), set from the creator's
interface language in "New company" and changed by an Owner or Manager. The
documentation packs already follow it; ``person_note`` is the line a prompt
adds so a model's prose follows it too.

Only the prose for the person changes. What a program reads stays as it is:
code, identifiers, paths, commit messages and the fixed markers the platform
parses without a model (``APPROVED:``, ``NEEDS WORK:``, the file blocks,
``ARCHITECT_CONSULTED``), since a translated marker would no longer be
recognised.
"""

from __future__ import annotations

DEFAULT = "en"
NAMES = {"en": "English", "es": "Spanish"}


def code(value: str | None) -> str:
    """The company's language code; English for anything unknown."""
    value = (value or "").strip().lower()
    return value if value in NAMES else DEFAULT


def person_note(value: str | None) -> str:
    """The prompt line for the company's language; empty for English, the
    language the prompts are written in."""
    lang = code(value)
    if lang == DEFAULT:
        return ""
    return (
        f"\n\nLANGUAGE: write the text meant for the person (summaries, "
        f"explanations, review reasons, questions) in {NAMES[lang]}. Keep in "
        "English: code, identifiers, file paths, commit messages, and every "
        "fixed marker the platform reads (APPROVED:, NEEDS WORK:, the file "
        "blocks, ARCHITECT_CONSULTED and the other upper-case markers)."
    )
