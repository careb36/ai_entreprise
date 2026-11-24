"""The prompt section of a task that writes a changelog fragment.

In sprint 8 (2026-11-24, issue #701) a fragment described the plan's task
titles instead of the diff: it claimed a flag and two functions the base
already had. A task whose title or description names the changelog or a
fragment gets this short section, which tells the writer to read the diff
first (``workspace__git`` ``changed`` and ``diff``); the Verifier then holds
the fragment to it (``evidence/changelog_diff.py``). Turned off by name with
``PROMPT_SECTIONS_OFF`` like any other optional section.
"""

from __future__ import annotations

import re
from typing import Any

from services.loop.worker.prompt_budget import Section
from services.planning.changelog_order import is_changelog_task

SECTION_NAME = "changelog_diff"
PRIORITY = 1
_FRAGMENT = re.compile(r"\bfragments?\b", re.IGNORECASE)
TEXT = (
    "\n\n## Changelog fragment\n"
    "Describe only what the diff shows: call workspace__git `changed` and "
    "`diff` first; never what the plan or earlier tasks' titles say. Cite a "
    "path only if the diff changed it; under Added, name only what the diff "
    "adds; mark anything else as context with 'already', 'existing' or "
    "'unchanged'. Use the sprint's issue number. The Verifier compares the "
    "fragment with the diff and sends back a claim the diff does not show."
)


def wanted(item: dict[str, Any]) -> bool:
    """The task names the changelog or a fragment in its title or description."""
    text = f"{item.get('title') or ''}\n{item.get('description') or ''}"
    return is_changelog_task(item) or bool(_FRAGMENT.search(text))


def section(item: dict[str, Any]) -> Section:
    """The section, empty (left out) for any other task."""
    return Section(SECTION_NAME, TEXT if wanted(item) else "", PRIORITY)
