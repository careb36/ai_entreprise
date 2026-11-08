"""The bench scorecard's schema: what ``scorecard.py write`` and
``compare.py`` accept. A card off the schema is never written or compared
(exit status 2): every counter present, every number finite.
"""

import math
from typing import Any

SCHEMA_VERSION = 1
PASS, FAIL, MISSING = "pass", "fail", "missing"
SPEC_STATUSES = ("completed", "timeout", "error")

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


def validate(card: Any) -> list[str]:
    """What is wrong with *card* against the schema; empty when it is sound."""
    if not isinstance(card, dict):
        return ["the scorecard is not an object"]
    problems = []
    if card.get("schema_version") != SCHEMA_VERSION:
        problems.append(
            f"schema_version is {card.get('schema_version')!r}, expected {SCHEMA_VERSION}"
        )
    for key in ("stamp", "commit", "llm"):
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
    return problems
