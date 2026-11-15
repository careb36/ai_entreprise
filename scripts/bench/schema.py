"""The bench scorecard's schema: what ``scorecard.py write`` and
``compare.py`` accept. A card off the schema is never written or compared
(exit status 2): every counter present, every number finite.

Version 2 (issue #319) adds the routing policy's label (``policy``) and, per
spec, the tasks split by difficulty and model (``routing``). A version 1 card
(the references kept before it) is still read, without either. Optional and
additive, still version 3 (issue #319, difficulty routing): a routing row may
carry the estimate's band (``difficulty_band``: ``low``, ``medium``,
``high``), its cost in US cents (``cost_cents``, a number or null when
unknown) and the tasks whose cost is unknown (``cost_unknown``); a row
without them is read as before.

Version 3 (issue #316, after #443) adds, per spec and in the totals, the
weight of the prompts the backend sent (``prompt``, ``prompt_weight.py``):
estimated tokens per section, the prompts with sections cut and with related
code, and the runs without a measure counted apart. Version 1 and 2 cards are
still read, without it.

Also in version 3, optional and additive (issue #316, the token budget): the
run-level block ``run`` (``budget.py``: ``status`` ``complete`` or
``budget_exceeded``, the ``budget``, the reported tokens spent, the turns with
and without reported usage, and the specifications ``skipped`` for the budget,
which have no entry and are never graded), and the spec status
``budget_exceeded`` for the sprint the budget stopped. A card without ``run``
(every card before it) reads as a complete run.
"""

import math
import sys
from pathlib import Path
from typing import Any

sys.path.insert(0, str(Path(__file__).resolve().parent))
import budget  # the sibling module, by path

SCHEMA_VERSION = 3
READABLE_VERSIONS = (1, 2, 3)
PASS, FAIL, MISSING = "pass", "fail", "missing"
SPEC_STATUSES = ("completed", "timeout", "error", budget.BUDGET_EXCEEDED)

_SPEC_FIELDS = {
    "name": str,
    "status": str,
    "criteria": dict,
    "tasks": dict,
    "rework_requests": int,
    "rework_per_task": (int, float),
    "blocked_share": (int, float),
    "first_attempt_rate": (int, float),
    "wall_clock_seconds": (int, float),
    "tokens": dict,
    "models": dict,
}
_TOKEN_FIELDS = ("reported", "turns_reported", "turns_unreported")
_NUMERIC_SPEC_FIELDS = (
    "rework_per_task",
    "blocked_share",
    "first_attempt_rate",
    "wall_clock_seconds",
)
_TOTAL_FIELDS = (
    "specs",
    "specs_completed",
    "criteria_passed",
    "criteria_total",
    "criteria_rate",
    "tasks",
    "done_first_attempt",
    "first_attempt_rate",
    "rework_per_task",
    "blocked_share",
    "wall_clock_seconds",
    "tokens_reported",
)


def _number(value: Any) -> bool:
    """A finite int or float, never a bool, NaN or infinity."""
    return (
        isinstance(value, (int, float))
        and not isinstance(value, bool)
        and math.isfinite(value)
    )


def _count(value: Any) -> bool:
    return isinstance(value, int) and not isinstance(value, bool) and value >= 0


_ROUTING_COUNTS = ("tasks", "done", "done_first_attempt", "blocked", "rework")
_ROUTING_RATES = ("rework_per_task", "blocked_share", "first_attempt_rate")
_DIFFICULTY_BANDS = ("low", "medium", "high")


def _routing_problems(where: str, rows: Any) -> list[str]:
    """What is wrong with a spec's split by difficulty and model."""
    if not isinstance(rows, list):
        return [f"{where}.routing must be a list"]
    problems = []
    for index, row in enumerate(rows):
        at = f"{where}.routing[{index}]"
        if not isinstance(row, dict):
            problems.append(f"{at} is not an object")
            continue
        difficulty = row.get("difficulty")
        if not _count(difficulty) or not 1 <= difficulty <= 5:
            problems.append(f"{at}.difficulty must be an integer from 1 to 5")
        if row.get("model") is not None and not isinstance(row.get("model"), str):
            problems.append(f"{at}.model must be a string or null")
        problems.extend(
            f"{at}.{k} must be a count"
            for k in _ROUTING_COUNTS
            if not _count(row.get(k))
        )
        problems.extend(
            f"{at}.{k} must be a finite number"
            for k in _ROUTING_RATES
            if not _number(row.get(k))
        )
        if row.get("difficulty_band") not in (None, *_DIFFICULTY_BANDS):
            problems.append(f"{at}.difficulty_band must be low, medium or high")
        cents = row.get("cost_cents")
        if cents is not None and (not _number(cents) or cents < 0):
            problems.append(f"{at}.cost_cents must be a finite number or null")
        if "cost_unknown" in row and not _count(row["cost_unknown"]):
            problems.append(f"{at}.cost_unknown must be a count")
        tokens = row.get("tokens") if isinstance(row.get("tokens"), dict) else {}
        problems.extend(
            f"{at}.tokens.{k} must be a count"
            for k in _TOKEN_FIELDS
            if not _count(tokens.get(k))
        )
        if all(_count(row.get(k)) for k in _ROUTING_COUNTS) and not (
            row["done_first_attempt"] <= row["done"] <= row["tasks"]
        ):
            problems.append(f"{at}: done_first_attempt <= done <= tasks does not hold")
    return problems


_PROMPT_COUNTS = (
    "prompts",
    "prompt_sections_missing",
    "tokens_estimated",
    "system_tokens_estimated",
    "cut_prompts",
    "related_code_prompts",
)


def _prompt_problems(where: str, obj: Any) -> list[str]:
    """What is wrong with a ``prompt`` object (``prompt_weight.weigh``)."""
    if not isinstance(obj, dict):
        return [f"{where}.prompt must be an object"]
    problems = [
        f"{where}.prompt.{k} must be a count"
        for k in _PROMPT_COUNTS
        if not _count(obj.get(k))
    ]
    if not _number(obj.get("tokens_mean")):
        problems.append(f"{where}.prompt.tokens_mean must be a finite number")
    sections = obj.get("sections")
    if not isinstance(sections, dict):
        problems.append(f"{where}.prompt.sections must be an object")
    else:
        for name, row in sections.items():
            if not (
                isinstance(row, dict)
                and _count(row.get("tokens"))
                and _count(row.get("prompts"))
                and _number(row.get("share"))
                and 0 <= row["share"] <= 1
            ):
                problems.append(
                    f"{where}.prompt.sections.{name} needs counts tokens, prompts and a share from 0 to 1"
                )
    for key in ("wiki_prompts", "memory_prompts"):  # optional: cards before #445
        if key in obj and not _count(obj[key]):
            problems.append(f"{where}.prompt.{key} must be a count")
    cut = obj.get("cut")
    if not isinstance(cut, dict) or not all(_count(v) for v in cut.values()):
        problems.append(f"{where}.prompt.cut must map section names to counts")
    if all(
        _count(obj.get(k)) for k in ("prompts", "cut_prompts", "related_code_prompts")
    ) and (max(obj["cut_prompts"], obj["related_code_prompts"]) > obj["prompts"]):
        problems.append(
            f"{where}.prompt: cut_prompts and related_code_prompts exceed prompts"
        )
    return problems


def validate(card: Any) -> list[str]:
    """What is wrong with *card* against the schema; empty when it is sound."""
    if not isinstance(card, dict):
        return ["the scorecard is not an object"]
    problems = []
    version = card.get("schema_version")
    if version not in READABLE_VERSIONS or isinstance(version, bool):
        problems.append(f"schema_version is {version!r}, expected {SCHEMA_VERSION}")
    keys = (
        ("stamp", "commit", "llm", "policy")
        if version in (2, 3)
        else ("stamp", "commit", "llm")
    )
    for key in keys:
        if not isinstance(card.get(key), str) or not card.get(key):
            problems.append(f"{key} must be a non-empty string")
    specs = card.get("specs")
    if not isinstance(specs, list):
        return [*problems, "specs must be a list"]
    for index, spec in enumerate(specs):
        where = f"specs[{index}]"
        if not isinstance(spec, dict):
            problems.append(f"{where} is not an object")
            continue
        for key, kind in _SPEC_FIELDS.items():
            if not isinstance(spec.get(key), kind) or isinstance(spec.get(key), bool):
                problems.append(f"{where}.{key} is missing or of the wrong type")
        if spec.get("status") not in SPEC_STATUSES:
            problems.append(f"{where}.status must be one of {', '.join(SPEC_STATUSES)}")
        criteria = (
            spec.get("criteria") if isinstance(spec.get("criteria"), dict) else {}
        )
        results = (
            criteria.get("results")
            if isinstance(criteria.get("results"), dict)
            else None
        )
        if results is None:
            problems.append(f"{where}.criteria.results must be an object")
        else:
            if any(r not in (PASS, FAIL, MISSING) for r in results.values()):
                problems.append(
                    f"{where}.criteria.results holds a value other than pass, fail, missing"
                )
            if criteria.get("total") != len(results):
                problems.append(f"{where}.criteria.total does not match its results")
            if criteria.get("passed") != sum(1 for r in results.values() if r == PASS):
                problems.append(f"{where}.criteria.passed does not match its results")
        problems.extend(
            f"{where}.{key} must be a finite number"
            for key in _NUMERIC_SPEC_FIELDS
            if key in spec and not _number(spec[key])
        )
        tokens = spec.get("tokens") if isinstance(spec.get("tokens"), dict) else {}
        problems.extend(
            f"{where}.tokens.{k} must be a count"
            for k in _TOKEN_FIELDS
            if not _count(tokens.get(k))
        )
        if not (_count(criteria.get("passed")) and _count(criteria.get("total"))):
            problems.append(f"{where}.criteria needs counts passed and total")
        tasks = spec.get("tasks") if isinstance(spec.get("tasks"), dict) else {}
        if not all(
            _count(tasks.get(k))
            for k in ("total", "done", "blocked", "done_first_attempt")
        ):
            problems.append(
                f"{where}.tasks needs integer total, done, blocked, done_first_attempt"
            )
        elif not tasks["done_first_attempt"] <= tasks["done"] <= tasks["total"]:
            problems.append(
                f"{where}.tasks: done_first_attempt <= done <= total does not hold"
            )
        if version in (2, 3):
            problems.extend(_routing_problems(where, spec.get("routing")))
        if version == 3:
            problems.extend(_prompt_problems(where, spec.get("prompt")))
    totals = card.get("totals")
    if not isinstance(totals, dict):
        problems.append("totals must be an object")
    else:
        problems.extend(
            f"totals.{k} is missing" for k in _TOTAL_FIELDS if k not in totals
        )
        problems.extend(
            f"totals.{k} must be a finite number"
            for k in _TOTAL_FIELDS
            if k in totals and not _number(totals[k])
        )
        if version == 3:
            problems.extend(_prompt_problems("totals", totals.get("prompt")))
    problems.extend(budget.run_problems(card, _count))
    if not budget.is_cut(card) and any(
        isinstance(s, dict) and s.get("status") == budget.BUDGET_EXCEEDED for s in specs
    ):
        problems.append(
            "a spec stopped for the budget needs run.status budget_exceeded"
        )
    return problems
