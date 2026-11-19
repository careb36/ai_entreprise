"""The bench scorecard's schema, as the backend reads it (issue #685).

Keep in step: any change to ``scripts/bench/schema.py`` (or the bench modules
it calls) updates this copy and ``bench_schema_blocks`` in the same pull
request; ``tests/test_scorecard_schema_parity.py`` fails until they agree.

A copy of ``scripts/bench/schema.validate`` (versions 1 to 7) as a pure
function: the backend shows the committed scorecards (``bench_scorecards``)
and never imports the bench's scripts, which are protected paths and not in
the backend's image. The parity test runs both validators over the same
cards, sound and broken, and fails on the first card they answer differently.

The optional blocks (``run``, ``graders``, ``sections_off``, ``ablation`` and
v5's ``humaneval``) are in ``bench_schema_blocks``, v6's ``swebench`` in
``bench_schema_swebench`` and v7's edit-format fields in
``bench_schema_edit_format``. No I/O, no clock, no model.
"""

from __future__ import annotations

import math
from typing import Any

from services.models import bench_schema_blocks as blocks
from services.models.bench_schema_edit_format import edit_format_problems
from services.models.bench_schema_swebench import swebench_problems

SCHEMA_VERSION = 7
READABLE_VERSIONS = (1, 2, 3, 4, 5, 6, 7)
PASS, FAIL, MISSING = "pass", "fail", "missing"
SPEC_STATUSES = ("completed", "timeout", "error", blocks.BUDGET_EXCEEDED)

_SPEC_FIELDS = (
    ("name", str),
    ("status", str),
    ("criteria", dict),
    ("tasks", dict),
    ("rework_requests", int),
    ("rework_per_task", (int, float)),
    ("blocked_share", (int, float)),
    ("first_attempt_rate", (int, float)),
    ("wall_clock_seconds", (int, float)),
    ("tokens", dict),
    ("models", dict),
)
TOKEN_FIELDS = ("reported", "turns_reported", "turns_unreported")
_NUMERIC_SPEC_FIELDS = (
    "rework_per_task",
    "blocked_share",
    "first_attempt_rate",
    "wall_clock_seconds",
)
TOTAL_FIELDS = (
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
_ROUTING_COUNTS = ("tasks", "done", "done_first_attempt", "blocked", "rework")
_ROUTING_RATES = ("rework_per_task", "blocked_share", "first_attempt_rate")
_DIFFICULTY_BANDS = ("low", "medium", "high")
_PROMPT_COUNTS = (
    "prompts",
    "prompt_sections_missing",
    "tokens_estimated",
    "system_tokens_estimated",
    "cut_prompts",
    "related_code_prompts",
)


def number(value: Any) -> bool:
    """A finite int or float, never a bool, NaN or infinity."""
    return (
        isinstance(value, (int, float))
        and not isinstance(value, bool)
        and math.isfinite(value)
    )


count = blocks.count


def _routing_problems(where: str, rows: Any) -> list[str]:
    if not isinstance(rows, list):
        return [f"{where}.routing must be a list"]
    problems = []
    for index, row in enumerate(rows):
        at = f"{where}.routing[{index}]"
        if not isinstance(row, dict):
            problems.append(f"{at} is not an object")
            continue
        difficulty = row.get("difficulty")
        if not count(difficulty) or not 1 <= difficulty <= 5:
            problems.append(f"{at}.difficulty must be an integer from 1 to 5")
        if row.get("model") is not None and not isinstance(row.get("model"), str):
            problems.append(f"{at}.model must be a string or null")
        problems.extend(
            f"{at}.{k} must be a count"
            for k in _ROUTING_COUNTS
            if not count(row.get(k))
        )
        problems.extend(
            f"{at}.{k} must be a finite number"
            for k in _ROUTING_RATES
            if not number(row.get(k))
        )
        if row.get("difficulty_band") not in (None, *_DIFFICULTY_BANDS):
            problems.append(f"{at}.difficulty_band must be low, medium or high")
        cents = row.get("cost_cents")
        if cents is not None and (not number(cents) or cents < 0):
            problems.append(f"{at}.cost_cents must be a finite number or null")
        if "cost_unknown" in row and not count(row["cost_unknown"]):
            problems.append(f"{at}.cost_unknown must be a count")
        tokens = row.get("tokens") if isinstance(row.get("tokens"), dict) else {}
        problems.extend(
            f"{at}.tokens.{k} must be a count"
            for k in TOKEN_FIELDS
            if not count(tokens.get(k))
        )
        if all(count(row.get(k)) for k in _ROUTING_COUNTS) and not (
            row["done_first_attempt"] <= row["done"] <= row["tasks"]
        ):
            problems.append(f"{at}: done_first_attempt <= done <= tasks does not hold")
    return problems


def _prompt_problems(where: str, obj: Any) -> list[str]:
    if not isinstance(obj, dict):
        return [f"{where}.prompt must be an object"]
    problems = [
        f"{where}.prompt.{k} must be a count"
        for k in _PROMPT_COUNTS
        if not count(obj.get(k))
    ]
    if not number(obj.get("tokens_mean")):
        problems.append(f"{where}.prompt.tokens_mean must be a finite number")
    sections = obj.get("sections")
    if not isinstance(sections, dict):
        problems.append(f"{where}.prompt.sections must be an object")
    else:
        for name, row in sections.items():
            if not (
                isinstance(row, dict)
                and count(row.get("tokens"))
                and count(row.get("prompts"))
                and number(row.get("share"))
                and 0 <= row["share"] <= 1
            ):
                problems.append(
                    f"{where}.prompt.sections.{name} needs counts tokens, prompts and a share from 0 to 1"
                )
    for key in ("wiki_prompts", "memory_prompts"):
        if key in obj and not count(obj[key]):
            problems.append(f"{where}.prompt.{key} must be a count")
    cut = obj.get("cut")
    if not isinstance(cut, dict) or not all(count(v) for v in cut.values()):
        problems.append(f"{where}.prompt.cut must map section names to counts")
    if all(
        count(obj.get(k)) for k in ("prompts", "cut_prompts", "related_code_prompts")
    ) and (max(obj["cut_prompts"], obj["related_code_prompts"]) > obj["prompts"]):
        problems.append(
            f"{where}.prompt: cut_prompts and related_code_prompts exceed prompts"
        )
    return problems


def _criteria_problems(where: str, criteria: dict) -> list[str]:
    results = (
        criteria.get("results") if isinstance(criteria.get("results"), dict) else None
    )
    if results is None:
        return [f"{where}.criteria.results must be an object"]
    problems = []
    if any(r not in (PASS, FAIL, MISSING) for r in results.values()):
        problems.append(
            f"{where}.criteria.results holds a value other than pass, fail, missing"
        )
    if criteria.get("total") != len(results):
        problems.append(f"{where}.criteria.total does not match its results")
    if criteria.get("passed") != sum(1 for r in results.values() if r == PASS):
        problems.append(f"{where}.criteria.passed does not match its results")
    return problems


def _spec_problems(where: str, spec: dict, version: Any) -> list[str]:
    problems = []
    for key, kind in _SPEC_FIELDS:
        if not isinstance(spec.get(key), kind) or isinstance(spec.get(key), bool):
            problems.append(f"{where}.{key} is missing or of the wrong type")
    if spec.get("status") not in SPEC_STATUSES:
        problems.append(f"{where}.status must be one of {', '.join(SPEC_STATUSES)}")
    criteria = spec.get("criteria") if isinstance(spec.get("criteria"), dict) else {}
    problems.extend(_criteria_problems(where, criteria))
    problems.extend(
        f"{where}.{key} must be a finite number"
        for key in _NUMERIC_SPEC_FIELDS
        if key in spec and not number(spec[key])
    )
    tokens = spec.get("tokens") if isinstance(spec.get("tokens"), dict) else {}
    problems.extend(
        f"{where}.tokens.{k} must be a count"
        for k in TOKEN_FIELDS
        if not count(tokens.get(k))
    )
    if not (count(criteria.get("passed")) and count(criteria.get("total"))):
        problems.append(f"{where}.criteria needs counts passed and total")
    tasks = spec.get("tasks") if isinstance(spec.get("tasks"), dict) else {}
    if not all(
        count(tasks.get(k)) for k in ("total", "done", "blocked", "done_first_attempt")
    ):
        problems.append(
            f"{where}.tasks needs integer total, done, blocked, done_first_attempt"
        )
    elif not tasks["done_first_attempt"] <= tasks["done"] <= tasks["total"]:
        problems.append(
            f"{where}.tasks: done_first_attempt <= done <= total does not hold"
        )
    if version in (2, 3, 4, 5, 6, 7):
        problems.extend(_routing_problems(where, spec.get("routing")))
    if version in (3, 4, 5, 6, 7):
        problems.extend(_prompt_problems(where, spec.get("prompt")))
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
        if version in (2, 3, 4, 5, 6, 7)
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
        problems.extend(_spec_problems(where, spec, version))
    totals = card.get("totals")
    if not isinstance(totals, dict):
        problems.append("totals must be an object")
    else:
        problems.extend(
            f"totals.{k} is missing" for k in TOTAL_FIELDS if k not in totals
        )
        problems.extend(
            f"totals.{k} must be a finite number"
            for k in TOTAL_FIELDS
            if k in totals and not number(totals[k])
        )
        if version in (3, 4, 5, 6, 7):
            problems.extend(_prompt_problems("totals", totals.get("prompt")))
    problems.extend(blocks.run_problems(card))
    problems.extend(blocks.ablation_problems(card))
    problems.extend(blocks.graders_problems(card))
    problems.extend(blocks.humaneval_problems(card, SPEC_STATUSES))
    problems.extend(swebench_problems(card, SPEC_STATUSES))
    problems.extend(edit_format_problems(card))
    if not blocks.is_cut(card) and any(
        isinstance(s, dict) and s.get("status") == blocks.BUDGET_EXCEEDED for s in specs
    ):
        problems.append(
            "a spec stopped for the budget needs run.status budget_exceeded"
        )
    return problems
