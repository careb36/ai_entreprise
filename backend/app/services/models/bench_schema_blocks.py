"""The scorecard's optional blocks, as ``bench_schema`` checks them.

Copies of the bench scripts' own checks (``scripts/bench/budget.run_problems``,
``graders.problems`` and ``ablation.problems``), pure functions with the same
messages; ``tests/test_scorecard_schema_parity.py`` holds them in step.
"""

from __future__ import annotations

import math
import re
from typing import Any

COMPLETE, BUDGET_EXCEEDED = "complete", "budget_exceeded"
RUN_STATUSES = (COMPLETE, BUDGET_EXCEEDED)
SKIPPED_BUDGET = "budget"
ABLATION_VERSION = 4
_SHA1 = re.compile(r"^[0-9a-f]{40}$")
_SHA256 = re.compile(r"^[0-9a-f]{64}$")
_SIDE_COUNTS = (
    "criteria_passed",
    "criteria_total",
    "tasks",
    "rework_requests",
    "prompts",
    "prompt_tokens",
    "tokens_reported",
    "turns_unreported",
    "section_tokens",
)
_SIDE_RATES = ("rework_per_task", "prompt_tokens_mean")
_DELTA_FIELDS = (
    "criteria_passed",
    "rework_requests",
    "rework_per_task",
    "prompt_tokens",
    "prompt_tokens_mean",
    "tokens_reported",
)
_EXACT = ("criteria_passed", "rework_requests", "prompt_tokens", "tokens_reported")


def count(value: Any) -> bool:
    """A non-negative int, never a bool."""
    return isinstance(value, int) and not isinstance(value, bool) and value >= 0


def _finite(value: Any) -> bool:
    return (
        isinstance(value, (int, float))
        and not isinstance(value, bool)
        and math.isfinite(value)
    )


def _names(value: Any) -> bool:
    return (
        isinstance(value, list)
        and all(isinstance(n, str) and n for n in value)
        and len(set(value)) == len(value)
    )


def is_cut(card: dict[str, Any]) -> bool:
    """Whether the run's token budget cut it (``run.status``)."""
    run = card.get("run")
    return isinstance(run, dict) and run.get("status") == BUDGET_EXCEEDED


def run_problems(card: dict[str, Any]) -> list[str]:
    """What is wrong with the optional ``run`` block of *card*."""
    if "run" not in card:
        return []
    run = card["run"]
    if not isinstance(run, dict):
        return ["run must be an object"]
    problems = []
    if run.get("status") not in RUN_STATUSES:
        problems.append(f"run.status must be one of {', '.join(RUN_STATUSES)}")
    budget = run.get("budget")
    if budget is not None and not (count(budget) and budget > 0):
        problems.append("run.budget must be a positive integer or null")
    if run.get("status") == BUDGET_EXCEEDED and budget is None:
        problems.append("run.budget is required when the budget cut the run")
    problems.extend(
        f"run.{k} must be a count"
        for k in ("tokens_spent", "turns_reported", "turns_unreported")
        if not count(run.get(k))
    )
    skipped = run.get("skipped")
    if not isinstance(skipped, list) or not all(
        isinstance(s, dict)
        and isinstance(s.get("name"), str)
        and s.get("reason") == SKIPPED_BUDGET
        for s in skipped
    ):
        return [*problems, "run.skipped must list {name, reason: budget}"]
    if skipped and run.get("status") != BUDGET_EXCEEDED:
        problems.append("run.skipped must be empty unless the budget cut the run")
    ran = {s.get("name") for s in card.get("specs") or [] if isinstance(s, dict)}
    problems.extend(
        f"run.skipped names {s['name']}, which has an entry"
        for s in skipped
        if s["name"] in ran
    )
    return problems


def graders_problems(card: dict[str, Any]) -> list[str]:
    """What is wrong with the optional ``graders`` block of *card*."""
    if "graders" not in card:
        return []
    block = card["graders"]
    if not isinstance(block, dict):
        return ["graders must be an object"]
    found = []
    if not isinstance(block.get("sha"), str) or not _SHA1.match(block["sha"]):
        found.append("graders.sha must be a full commit sha")
    trees = block.get("trees")
    if (
        not isinstance(trees, dict)
        or not trees
        or not all(
            isinstance(k, str) and isinstance(v, str) and _SHA1.match(v)
            for k, v in trees.items()
        )
    ):
        found.append("graders.trees must map each grading path to a tree id")
    if not isinstance(block.get("sha256"), str) or not _SHA256.match(block["sha256"]):
        found.append("graders.sha256 must be a sha256 hex digest")
    return found


def _side_problems(at: str, name: str, values: dict) -> list[str]:
    found = [
        f"{at}.{name}.{k} must be a count"
        for k in _SIDE_COUNTS
        if not count(values.get(k))
    ]
    found += [
        f"{at}.{name}.{k} must be a finite number or null"
        for k in _SIDE_RATES
        if values.get(k) is not None and not _finite(values.get(k))
    ]
    return found


def _arm_problems(at: str, row: Any) -> list[str]:
    if not isinstance(row, dict):
        return [f"{at} is not an object"]
    found = []
    for key in ("section", "stamp"):
        if not isinstance(row.get(key), str) or not row.get(key):
            found.append(f"{at}.{key} must be a non-empty string")
    if row.get("status") not in RUN_STATUSES:
        found.append(f"{at}.status must be one of {', '.join(RUN_STATUSES)}")
    for key in ("specs", "criteria_unmeasured"):
        if not _names(row.get(key)):
            found.append(f"{at}.{key} must list distinct names")
    for key in ("applied", "same_models"):
        if not isinstance(row.get(key), bool):
            found.append(f"{at}.{key} must be true or false")
    sides = {}
    for name in ("baseline", "arm"):
        values = row.get(name)
        if not isinstance(values, dict):
            found.append(f"{at}.{name} must be an object")
            continue
        found += _side_problems(at, name, values)
        sides[name] = values
    delta = row.get("delta")
    if not isinstance(delta, dict):
        return [*found, f"{at}.delta must be an object"]
    for key in _DELTA_FIELDS:
        value = delta.get(key)
        if value is not None and not _finite(value):
            found.append(f"{at}.delta.{key} must be a finite number or null")
        elif (
            key in _EXACT
            and value is not None
            and len(sides) == 2
            and all(count(s.get(key)) for s in sides.values())
            and value != sides["arm"][key] - sides["baseline"][key]
        ):
            found.append(f"{at}.delta.{key} must be the arm's less the baseline's")
    return found


def ablation_problems(card: dict[str, Any]) -> list[str]:
    """What is wrong with the optional ``sections_off`` and ``ablation`` of *card*."""
    found = []
    version = card.get("schema_version")
    newer = isinstance(version, int) and version >= ABLATION_VERSION
    if "sections_off" in card:
        if not newer:
            found.append("sections_off needs schema v4")
        if not _names(card["sections_off"]):
            found.append("sections_off must list distinct section names")
    if "ablation" not in card:
        return found
    if not newer:
        found.append("ablation needs schema v4")
    if card.get("sections_off"):
        found.append("sections_off must be empty on the baseline of an ablation")
    block = card["ablation"]
    if not isinstance(block, dict):
        return [*found, "ablation must be an object"]
    if not isinstance(block.get("request"), str) or not block.get("request"):
        found.append("ablation.request must be a non-empty string")
    if block.get("baseline") != card.get("stamp"):
        found.append("ablation.baseline must be this card's stamp")
    arms = block.get("arms")
    if not isinstance(arms, list):
        found.append("ablation.arms must be a list")
    else:
        for index, row in enumerate(arms):
            found += _arm_problems(f"ablation.arms[{index}]", row)
        sections = [r.get("section") for r in arms if isinstance(r, dict)]
        if len(set(sections)) != len(sections):
            found.append("ablation.arms names a section twice")
    not_run = block.get("not_run")
    if not isinstance(not_run, list) or not all(
        isinstance(r, dict)
        and isinstance(r.get("section"), str)
        and isinstance(r.get("reason"), str)
        for r in not_run
    ):
        found.append("ablation.not_run must list {section, reason}")
    return found
