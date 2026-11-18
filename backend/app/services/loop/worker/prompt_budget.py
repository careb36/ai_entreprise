"""The weight of each prompt section, and a budget that cuts the least needed first (#443).

A task prompt is a list of named sections. ``fit`` joins them in their order
(the order the providers' prefix cache relies on), measures each one and,
when a budget is set and the prompt is over it, leaves out whole sections from
the lowest priority up until it fits. Required sections (the output format,
the task, the plan and the review feedback) are never left out. The prompt
names what was left out, so the model can ask for it through its tools.

The measure is the backend's own observation of the prompt it sent, recorded
beside the run as ``prompt_sections``; tokens are an estimate (characters / 4,
rounded up), since the provider's tokenizer is not available here and the
proxy reports only the total.

Budgets, in estimated tokens: ``PROMPT_BUDGET_<ROLE>`` (the role upper-cased,
anything but letters and digits as ``_``), else ``PROMPT_BUDGET``. Unset, 0 or
not a number: no budget, every section is kept and still measured.

Sections can also be turned off by name for a with/without comparison (the
bench, #316): ``PROMPT_SECTIONS_OFF`` lists them, comma-separated. They are
left out before the budget is applied, the prompt does not name them (the
comparison is of a prompt without the section, not of one that points at
it), and the measure records them as ``off`` (the names configured) and
``off_dropped`` (those this prompt would have carried), so a scorecard can
tell the runs apart. An unknown name, or a required section's, is ignored
with a warning at startup (``startup_warnings``). Unset: nothing is off.

Priorities follow what the issue asked to study: Aider caps its repository map
by a token budget, OpenHands' condenser drops the least recent context first.
No code was taken.
"""

from __future__ import annotations

import math
import os
import re
from dataclasses import dataclass
from typing import Any

from config import settings

REQUIRED = 0
CHARS_PER_TOKEN = 4

# The Developer prompt's sections, in prompt order (``worker/execute``);
# ``test_prompt_sections_off`` keeps this list in step with the prompt.
SECTION_NAMES = (
    "output_format",
    "standards_and_procedures",
    "lessons",
    "task",
    "requirements",
    "repository",
    "related_code",
    "wiki",
    "memory",
    "previous_attempt",
    "completed_tasks",
    "sprint_board",
    "console_assistant_notes",
    "architect_brief",
    "review_feedback",
)
# Never cut by the budget, and never turned off: without them there is no task.
REQUIRED_NAMES = frozenset(
    {"output_format", "task", "architect_brief", "review_feedback"}
)
OFF_SETTING = "PROMPT_SECTIONS_OFF"


@dataclass(frozen=True)
class Section:
    """One part of the prompt. ``priority`` 0 is required; a higher number is cut sooner."""

    name: str
    text: str
    priority: int = REQUIRED


def estimate_tokens(text: str) -> int:
    return math.ceil(len(text) / CHARS_PER_TOKEN)


def budget_for(role: str | None, env: dict[str, str] | None = None) -> int | None:
    """The role's budget in estimated tokens, else the default one, else None."""
    env = os.environ if env is None else env
    keys = ["PROMPT_BUDGET"]
    if role:
        keys.insert(0, "PROMPT_BUDGET_" + re.sub(r"[^A-Z0-9]", "_", role.upper()))
    for key in keys:
        raw = str(env.get(key) or "").strip()
        if raw:
            try:
                value = int(raw)
            except ValueError:
                return None
            return value if value > 0 else None
    return None


def _requested(raw: str | None) -> list[str]:
    raw = settings.PROMPT_SECTIONS_OFF if raw is None else raw
    names = (name.strip().lower() for name in str(raw or "").split(","))
    return list(dict.fromkeys(name for name in names if name))


def sections_off(raw: str | None = None) -> frozenset[str]:
    """The sections turned off: the known, optional names in *raw*
    (``PROMPT_SECTIONS_OFF`` by default); anything else is ignored."""
    return frozenset(
        name
        for name in _requested(raw)
        if name in SECTION_NAMES and name not in REQUIRED_NAMES
    )


def startup_warnings(raw: str | None = None) -> list[str]:
    """What ``PROMPT_SECTIONS_OFF`` ignores, and what it turns off, one line each."""
    requested = _requested(raw)
    warnings = []
    unknown = [name for name in requested if name not in SECTION_NAMES]
    if unknown:
        optional = [name for name in SECTION_NAMES if name not in REQUIRED_NAMES]
        warnings.append(
            f"{OFF_SETTING} names unknown prompt sections, ignored: "
            + ", ".join(unknown)
            + ". Known sections: "
            + ", ".join(optional)
            + "."
        )
    required = [name for name in requested if name in REQUIRED_NAMES]
    if required:
        warnings.append(
            f"{OFF_SETTING} names required prompt sections, which stay on: "
            + ", ".join(required)
            + "."
        )
    off = sections_off(raw)
    if off:
        warnings.append(
            f"{OFF_SETTING}: left out of every Developer task prompt "
            "(a with/without comparison): "
            + ", ".join(name for name in SECTION_NAMES if name in off)
            + "."
        )
    return warnings


def cut_note(names: list[str]) -> str:
    return (
        "\n\n## Left out of this prompt\n"
        "To keep the prompt within its budget these sections were left out: "
        + ", ".join(names)
        + ". If you need one, read what it would have said through your tools "
        "(the repository, the board) instead of guessing."
    )


def fit(
    sections: list[Section],
    budget: int | None = None,
    off: frozenset[str] = frozenset(),
) -> tuple[str, dict[str, Any]]:
    """The prompt text and its measure: ``{budget, tokens, sections: [...], cut: [...]}``,
    and ``off``/``off_dropped``.

    Empty sections are skipped, and so are the optional ones named in *off*
    (``off_dropped``; the prompt does not name them). Over budget, the
    highest-priority number goes first and, among equals, the later section
    (the more variable one).
    """
    off = frozenset(name for name in off if name not in REQUIRED_NAMES)

    def turned_off(s: Section) -> bool:
        return s.name in off and s.priority > REQUIRED

    dropped = [s.name for s in sections if s.text and turned_off(s)]
    present = [s for s in sections if s.text and not turned_off(s)]
    cut: set[int] = set()

    def render() -> str:
        text = "".join(s.text for i, s in enumerate(present) if i not in cut)
        if cut:
            text += cut_note([s.name for i, s in enumerate(present) if i in cut])
        return text

    if budget:
        candidates = sorted(
            (i for i, s in enumerate(present) if s.priority > REQUIRED),
            key=lambda i: (present[i].priority, i),
            reverse=True,
        )
        for index in candidates:
            if estimate_tokens(render()) <= budget:
                break
            cut.add(index)
    text = render()
    measure = {
        "budget": budget,
        "tokens": estimate_tokens(text),
        "chars": len(text),
        "sections": [
            {
                "name": s.name,
                "chars": len(s.text),
                "tokens": estimate_tokens(s.text),
                "priority": s.priority,
                "cut": i in cut,
            }
            for i, s in enumerate(present)
        ],
        "cut": [s.name for i, s in enumerate(present) if i in cut],
        # Turned off by name (PROMPT_SECTIONS_OFF), apart from the budget's cut.
        "off": sorted(off),
        "off_dropped": dropped,
    }
    return text, measure
