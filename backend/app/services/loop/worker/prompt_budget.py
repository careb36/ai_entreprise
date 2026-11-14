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

REQUIRED = 0
CHARS_PER_TOKEN = 4


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


def cut_note(names: list[str]) -> str:
    return (
        "\n\n## Left out of this prompt\n"
        "To keep the prompt within its budget these sections were left out: "
        + ", ".join(names)
        + ". If you need one, read what it would have said through your tools "
        "(the repository, the board) instead of guessing."
    )


def fit(
    sections: list[Section], budget: int | None = None
) -> tuple[str, dict[str, Any]]:
    """The prompt text and its measure: ``{budget, tokens, sections: [...], cut: [...]}``.

    Empty sections are skipped. Over budget, the highest-priority number goes
    first and, among equals, the later section (the more variable one).
    """
    present = [s for s in sections if s.text]
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
    }
    return text, measure
