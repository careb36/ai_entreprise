"""Architect and editor mode (issue #319, A.10; taken from Aider's
architect/editor split).

Off by default (``ARCHITECT_EDITOR_MODE``). When on, a Developer task whose
difficulty (``model_routing.estimate_difficulty``) is at least
``ARCHITECT_EDITOR_MIN_DIFFICULTY`` consults the Architect, and the brief is
asked for as a concrete edit plan in a fixed format::

    EDIT PLAN
    1. path/to/file.py — what changes in it
    2. tests/test_file.py — what the test checks
    END

The worker reads the plan back (``parse``: numbered steps, relative paths,
nothing else counts) and the Developer gets it as the plan to apply, routed
to the policy's cheapest band model (``model_routing.cheapest_model``) unless
a person chose its model. An answer without a readable plan stays the
ordinary Architect guidance and the task keeps its routed model.

The plan is a claim: prompt context the Architect wrote, never evidence. The
Verifier still judges the files the daemon wrote, whatever the plan said.
"""

from __future__ import annotations

import re
from typing import Any

from config import settings

from services.models import model_routing
from services.platform.orchestration_metrics import (
    KPI_ROLE_DEVELOPER,
    canonical_kpi_role,
    count_architect_consults,
    count_qa_rework_requests,
)

PLAN_START = "EDIT PLAN"
PLAN_END = "END"
MAX_STEPS = 20
_STEP = re.compile(r"^\s*(\d+)[.)]\s+`?([^\s`]+?)`?(?:\s+[—–-]|\s*:)\s+(\S.*?)\s*$")


def enabled() -> bool:
    return bool(getattr(settings, "ARCHITECT_EDITOR_MODE", False))


def applies(item: dict[str, Any]) -> bool:
    """Editor mode is on and the task is at or above the difficulty band."""
    if not enabled():
        return False
    reworked = count_qa_rework_requests(item.get("comments")) > 0
    difficulty = model_routing.estimate_difficulty(item, rework=reworked)
    return difficulty >= int(getattr(settings, "ARCHITECT_EDITOR_MIN_DIFFICULTY", 4))


def wanted(item: dict[str, Any], agent: dict[str, Any]) -> bool:
    """Consult the Architect for a plan: a Developer task the mode covers,
    once per attempt (the first one, then one per rework request)."""
    if canonical_kpi_role(agent.get("role")) != KPI_ROLE_DEVELOPER:
        return False
    comments = item.get("comments")
    return applies(item) and count_architect_consults(
        comments
    ) <= count_qa_rework_requests(comments)


def request_section(item: dict[str, Any]) -> str:
    """What the Architect's prompt adds when the task gets an edit plan."""
    if not applies(item):
        return ""
    return (
        "\n\n## Answer as an edit plan\n"
        "A developer will apply your plan as written. Answer with the files to "
        "touch, what changes in each, in the order to make the changes, in "
        "exactly this format and nothing after it:\n\n"
        f"{PLAN_START}\n"
        "1. <relative path> — <what changes in that file>\n"
        "2. <relative path> — <what changes>\n"
        f"{PLAN_END}"
    )


def _safe_path(path: str) -> bool:
    return (
        bool(path)
        and not path.startswith(("/", "\\", "~"))
        and ".." not in path.split("/")
    )


def parse(text: str | None) -> list[dict[str, str]] | None:
    """The plan's steps ``[{"path", "change"}]`` in order, or None.

    Only the lines between ``EDIT PLAN`` and ``END`` count; every line there
    must be a numbered step with a relative path, else there is no plan."""
    lines = (text or "").replace("\r\n", "\n").split("\n")
    stripped = [line.strip().strip("*#").strip() for line in lines]
    try:
        start = stripped.index(PLAN_START)
        end = stripped.index(PLAN_END, start + 1)
    except ValueError:
        return None
    steps = []
    for line in lines[start + 1 : end]:
        if not line.strip():
            continue
        match = _STEP.match(line)
        if not match or not _safe_path(match.group(2)):
            return None
        steps.append({"path": match.group(2), "change": match.group(3)[:500]})
    return steps[:MAX_STEPS] or None


def plan_in(advice: str | None) -> list[dict[str, str]] | None:
    """The edit plan in the Architect's brief, only while the mode is on."""
    return parse(advice) if enabled() and advice else None


def prompt_section(advice: str | None) -> str:
    """The Developer prompt's section for the Architect's brief: the plan to
    apply when it is one, else the brief as guidance."""
    if not advice:
        return ""
    steps = plan_in(advice)
    if steps:
        return (
            "\n\n## Edit plan to apply\n"
            "The team Architect planned this change. Apply the steps in order, "
            "touching these files; deliver every file you change as usual. The "
            "plan is guidance, not a verdict: the delivered files are what gets "
            "verified.\n\n"
            + "\n".join(
                f"{i}. `{s['path']}` — {s['change']}" for i, s in enumerate(steps, 1)
            )
        )
    return (
        "\n\n## Architect Guidance\n"
        "The team Architect reviewed this task before implementation. "
        "Follow this brief unless it conflicts with the task requirements.\n\n"
        f"{advice[:3000]}"
    )
