"""The backend reads exactly the scorecards the bench's schema accepts
(``services/models/bench_schema.py`` beside ``scripts/bench/schema.py``;
issue #685).

The /bench page validates the committed cards with the backend's copy of the
schema, since the backend never imports the bench's scripts. This test runs
both validators over the same cards, the committed ones, cards the bench's own
writer builds and cards broken one field at a time, and fails on the first card
they answer differently: when the script's schema moves, the copy must follow.
"""

from __future__ import annotations

import copy
import json
import sys
from pathlib import Path

import pytest
from services.models import bench_schema

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "scripts" / "bench"))
import schema as script_schema  # scripts/bench/schema.py, by path
from bench_helpers import BENCH, _card, _entry

SHA = "a" * 40
COMMITTED = sorted((BENCH / "scorecards").rglob("*.json"))


def _same(card) -> None:
    assert bench_schema.validate(card) == script_schema.validate(card)


def test_the_versions_match():
    assert bench_schema.SCHEMA_VERSION == script_schema.SCHEMA_VERSION
    assert bench_schema.READABLE_VERSIONS == script_schema.READABLE_VERSIONS
    assert bench_schema.SPEC_STATUSES == script_schema.SPEC_STATUSES


@pytest.mark.parametrize("path", COMMITTED, ids=lambda p: p.name)
def test_the_committed_cards_read_the_same(path):
    _same(json.loads(path.read_text(encoding="utf-8")))


def _base() -> dict:
    return json.loads(
        (BENCH / "scorecards" / "20261114T201804Z.json").read_text(encoding="utf-8")
    )


def _set(card: dict, dotted: str, value) -> dict:
    """*card* with the field at *dotted* (``specs.0.tokens.reported``) set,
    or removed when *value* is ``DELETE``."""
    out = copy.deepcopy(card)
    *path, last = dotted.split(".")
    node = out
    for key in path:
        node = node[int(key)] if isinstance(node, list) else node[key]
    if isinstance(node, list):
        last = int(last)
    if value is DELETE:
        del node[last]
    else:
        node[last] = value
    return out


DELETE = object()
BREAKS = [
    ("schema_version", 5),
    ("schema_version", True),
    ("schema_version", 1),
    ("stamp", ""),
    ("policy", DELETE),
    ("specs", {}),
    ("totals", []),
    ("totals.criteria_rate", float("nan")),
    ("totals.tokens_reported", DELETE),
    ("totals.prompt", DELETE),
    ("totals.prompt.tokens_mean", "1"),
    ("totals.prompt.cut", {"x": -1}),
    ("totals.prompt.cut_prompts", 99),
    ("totals.prompt.wiki_prompts", -1),
    ("totals.prompt.sections.task.share", 1.5),
    ("specs.0", "not a spec"),
    ("specs.0.status", "done"),
    ("specs.0.status", "budget_exceeded"),
    ("specs.0.rework_requests", True),
    ("specs.0.wall_clock_seconds", float("inf")),
    ("specs.0.criteria.results", []),
    ("specs.0.criteria.results", {"REQ-1.1": "ok"}),
    ("specs.0.criteria.passed", 3),
    ("specs.0.criteria.total", -1),
    ("specs.0.tokens.turns_unreported", -2),
    ("specs.0.tasks.done_first_attempt", 9),
    ("specs.0.tasks.total", DELETE),
    ("specs.0.routing", {}),
    ("specs.0.routing.0.difficulty", 6),
    ("specs.0.routing.0.model", 3),
    ("specs.0.routing.0.done", 9),
    ("specs.0.routing.0.blocked_share", None),
    ("specs.0.routing.0.difficulty_band", "huge"),
    ("specs.0.routing.0.cost_cents", -1),
    ("specs.0.routing.0.cost_unknown", 1.5),
    ("specs.0.routing.0.tokens", DELETE),
    ("specs.0.prompt", None),
    ("run", []),
    ("run", {"status": "complete", "budget": 0, "skipped": "x"}),
    (
        "run",
        {
            "status": "budget_exceeded",
            "budget": None,
            "tokens_spent": 1,
            "turns_reported": 1,
            "turns_unreported": 0,
            "skipped": [{"name": "01-cli-wordcount", "reason": "budget"}],
        },
    ),
    (
        "run",
        {
            "status": "complete",
            "budget": 10,
            "tokens_spent": 1,
            "turns_reported": 1,
            "turns_unreported": 0,
            "skipped": [{"name": "09-new", "reason": "budget"}],
        },
    ),
    ("graders", "x"),
    ("graders", {"sha": "abc", "trees": {}, "sha256": "f" * 63}),
    ("graders", {"sha": SHA, "trees": {"bench": SHA}, "sha256": "f" * 64}),
    ("sections_off", ["memory", "memory"]),
    ("ablation", []),
]


@pytest.mark.parametrize(
    ("field", "value"), BREAKS, ids=[f"{f}-{i}" for i, (f, _) in enumerate(BREAKS)]
)
def test_a_broken_card_reads_the_same(field, value):
    _same(_set(_base(), field, value))


def _arm(**changes) -> dict:
    side = {
        "criteria_passed": 30,
        "criteria_total": 30,
        "tasks": 21,
        "rework_requests": 0,
        "prompts": 21,
        "prompt_tokens": 38654,
        "tokens_reported": 84009,
        "turns_unreported": 0,
        "section_tokens": 4447,
        "rework_per_task": 0.0,
        "prompt_tokens_mean": 1840.7,
    }
    arm = {**side, "prompt_tokens": 34207, "section_tokens": 0}
    row = {
        "section": "repository",
        "stamp": "20261115T000000Z",
        "status": "complete",
        "specs": ["01-cli-wordcount"],
        "criteria_unmeasured": [],
        "applied": True,
        "same_models": True,
        "baseline": side,
        "arm": arm,
        "delta": {
            "criteria_passed": 0,
            "rework_requests": 0,
            "rework_per_task": 0.0,
            "prompt_tokens": -4447,
            "prompt_tokens_mean": None,
            "tokens_reported": 0,
        },
    }
    row.update(changes)
    return row


@pytest.mark.parametrize(
    "arms",
    [
        [_arm()],
        [_arm(), _arm()],
        [_arm(delta={**_arm()["delta"], "prompt_tokens": -1})],
        [_arm(applied="yes", specs=["a", "a"], status="done", section="")],
        [_arm(arm=None, delta=None)],
        ["not an arm"],
    ],
)
def test_an_ablation_reads_the_same(arms):
    card = {**_base(), "schema_version": 4}
    for block in (
        {"request": "each", "baseline": card["stamp"], "arms": arms, "not_run": []},
        {"request": "", "baseline": "other", "arms": arms, "not_run": [{"section": 1}]},
    ):
        _same({**card, "ablation": block})
        _same({**card, "ablation": block, "sections_off": ["memory"]})


def test_cards_the_writer_builds_read_the_same():
    _same(_card())
    _same(_card(_entry("01-a"), _entry("02-b", status="timeout", rework=2)))
    _same(_card(_entry(results={"REQ-1.1": "missing"})))
    _same("not a card")


def _problem(task: int, outcome: str = "pass", **changes) -> dict:
    row = {
        "task_id": f"HumanEval/{task}",
        "spec": f"he-{task:03d}",
        "entry_point": "f",
        "status": "completed",
        "outcome": outcome,
        "reason": "the sandbox could not run" if outcome == "unmeasured" else None,
        "tokens": {"reported": 100, "turns_reported": 2, "turns_unreported": 1},
    }
    row.update(changes)
    return row


def _humaneval(rows: list) -> dict:
    outcomes = [r.get("outcome") for r in rows if isinstance(r, dict)]
    graded, passed = len(rows), outcomes.count("pass")
    return {
        "dataset": {
            "name": "HumanEval",
            "source": "example/HumanEval.jsonl.gz",
            "sha256": "b" * 64,
            "license": "MIT",
        },
        "subset": [0, 1],
        "problems": rows,
        "skipped": [],
        "graded": graded,
        "passed": passed,
        "failed": outcomes.count("fail"),
        "unmeasured": outcomes.count("unmeasured"),
        "pass_at_1": (
            round(passed / graded, 4)
            if graded and "unmeasured" not in outcomes
            else None
        ),
        "tokens_reported": 100 * graded,
        "turns_unreported": graded,
    }


SOUND = _humaneval([_problem(0), _problem(1, "fail")])
HUMANEVAL_BREAKS = [
    {},
    SOUND,
    _humaneval([_problem(0), _problem(1, "unmeasured")]),
    _humaneval([]),
    "not a block",
    {**SOUND, "dataset": {"sha256": "x"}},
    {**SOUND, "problems": {}},
    {**SOUND, "subset": None, "skipped": "x"},
    {**SOUND, "pass_at_1": 0.0},
    {**SOUND, "graded": 3, "tokens_reported": 0},
    _humaneval([_problem(0), _problem(0)]),
    _humaneval([_problem(0, spec="01-cli-wordcount")]),
    _humaneval([_problem(0, "unmeasured", reason="")]),
    _humaneval(["not a row", _problem(1, "skip", status="done", task_id="HE/1")]),
    _humaneval([_problem(0, spec="", entry_point=None, tokens={"reported": -1})]),
]


@pytest.mark.parametrize("version", [3, 4, 5])
@pytest.mark.parametrize("block", HUMANEVAL_BREAKS)
def test_a_humaneval_block_reads_the_same(version, block):
    _same({**_base(), "schema_version": version, "humaneval": block})


def test_a_sound_humaneval_block_is_valid_on_v5_only():
    assert (
        bench_schema.validate({**_base(), "schema_version": 5, "humaneval": SOUND})
        == []
    )
    assert "humaneval needs schema v5" in bench_schema.validate(
        {**_base(), "schema_version": 4, "humaneval": SOUND}
    )
