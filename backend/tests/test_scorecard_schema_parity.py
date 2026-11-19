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


def _instance(iid: str, outcome: str = "pass", **changes) -> dict:
    passed = outcome == "pass"
    owner, rest = iid.split("__")
    row = {
        "instance_id": iid,
        "repo": f"{owner}/{rest.rsplit('-', 1)[0]}",
        "spec": "swe-" + iid.replace("__", "-"),
        "status": "completed",
        "outcome": outcome,
        "reason": {"fail": "a test failed", "unmeasured": "no environment"}.get(
            outcome
        ),
        "fail_to_pass": None
        if outcome == "unmeasured"
        else {"passed": 2 if passed else 1, "total": 2},
        "pass_to_pass": None if outcome == "unmeasured" else {"passed": 5, "total": 5},
        "tokens": {"reported": 300, "turns_reported": 3, "turns_unreported": 1},
    }
    row.update(changes)
    return row


def _swebench(rows: list) -> dict:
    outcomes = [r.get("outcome") for r in rows if isinstance(r, dict)]
    resolved, failed = outcomes.count("pass"), outcomes.count("fail")
    measured = resolved + failed
    return {
        "dataset": {
            "name": "princeton-nlp/SWE-bench_Lite",
            "revision": "c" * 40,
            "split": "test",
            "source": "example/SWE-bench_Lite",
            "license": "none declared",
            "instances": {"pallets__flask-4992": "d" * 64},
        },
        "subset": ["pallets__flask-4992", "psf__requests-3362"],
        "instances": rows,
        "skipped": [],
        "graded": len(rows),
        "measured": measured,
        "resolved": resolved,
        "failed": failed,
        "unmeasured": outcomes.count("unmeasured"),
        "resolved_rate": round(resolved / measured, 4) if measured else None,
        "tokens_reported": 300 * len(rows),
        "turns_unreported": len(rows),
    }


SWE_SOUND = _swebench(
    [_instance("pallets__flask-4992"), _instance("psf__requests-3362", "fail")]
)
SWEBENCH_BREAKS = [
    {},
    SWE_SOUND,
    # An unmeasured instance is left out of the rate, which stays measured.
    _swebench([_instance("pallets__flask-4992"), _instance("a__b-1", "unmeasured")]),
    _swebench([_instance("a__b-1", "unmeasured")]),
    _swebench([]),
    "not a block",
    {**SWE_SOUND, "dataset": {"revision": "abc", "instances": {"x": "y"}}},
    {**SWE_SOUND, "dataset": None},
    {**SWE_SOUND, "instances": {}},
    {**SWE_SOUND, "subset": None, "skipped": "x"},
    {**SWE_SOUND, "resolved_rate": 1.0},
    {**SWE_SOUND, "measured": 3, "tokens_reported": 0, "unmeasured": 1},
    _swebench([_instance("a__b-1"), _instance("a__b-1")]),
    _swebench([_instance("a__b-1", spec="01-cli-wordcount")]),
    _swebench([_instance("a__b-1", "unmeasured", reason="")]),
    _swebench([_instance("a__b-1", fail_to_pass={"passed": 1, "total": 2})]),
    _swebench([_instance("a__b-1", pass_to_pass=None)]),
    # A malformed group on a resolved row: a problem, never a crash.
    _swebench([_instance("a__b-1", fail_to_pass={"total": 1})]),
    _swebench([_instance("a__b-1", "fail", fail_to_pass={"passed": 3, "total": 2})]),
    _swebench(["not a row", _instance("x__y-1", "skip", status="done", repo="")]),
    _swebench([_instance("a__b-1", instance_id="not-an-id")]),
    _swebench([_instance("a__b-1", spec=None, tokens={"reported": -1})]),
]


@pytest.mark.parametrize("version", [4, 5, 6])
@pytest.mark.parametrize("block", SWEBENCH_BREAKS)
def test_a_swebench_block_reads_the_same(version, block):
    _same({**_base(), "schema_version": version, "swebench": block})


def test_a_sound_swebench_block_is_valid_on_v6_only():
    assert (
        bench_schema.validate({**_base(), "schema_version": 6, "swebench": SWE_SOUND})
        == []
    )
    assert "swebench needs schema v6" in bench_schema.validate(
        {**_base(), "schema_version": 5, "swebench": SWE_SOUND}
    )


def _summary(**changes) -> dict:
    summary = {
        "deliveries": 4,
        "observed": 3,
        "missing": 1,
        "well_formed": 2,
        "whole_files": 3,
        "edit_blocks": 2,
        "malformed_edit_blocks": 1,
        "off_format": 0,
        "rejected_edits": 1,
        "tool_edits": 0,
        "tool_edit_errors": 0,
        "requested": {"search_replace": 3},
    }
    summary.update(changes)
    return summary


def _format_row(outcome: str = "pass", **changes) -> dict:
    row = {
        "model": "fake-model",
        "format": "search_replace",
        "card": "20261114T201804Z",
        "served_models": ["fake-model"],
        "outcome": outcome,
        "reason": None if outcome in ("pass", "fail") else "no delivery observed",
        "criteria_passed": 30,
        "criteria_total": 30,
        "rework_requests": 0,
        "tokens_reported": 84009,
        **_summary(),
        "well_formed_rate": 0.6667,
    }
    del row["requested"]
    row.update(changes)
    return row


def _formats(*rows) -> dict:
    return {"formats": ["search_replace"], "models": ["fake-model"], "rows": list(rows)}


# (the card's edit_format, its first spec's edit_format, edit_formats); None
# leaves the field out.
EDIT_FORMAT_CASES = [
    ("whole", _summary(), _formats(_format_row(), _format_row("fail"))),
    ("search_replace", None, None),
    (None, _summary(), None),
    (None, None, _formats(_format_row("unusable", well_formed=0))),
    (None, None, _formats(_format_row("unmeasured", well_formed_rate=None))),
    ("diff", None, None),
    (3, None, None),
    (None, "not a summary", None),
    (None, _summary(observed=2), None),
    (None, _summary(well_formed=5, observed=4, missing=0), None),
    (None, _summary(deliveries=-1, requested={"whole": -1}), None),
    (None, _summary(requested=None), None),
    (None, None, "not a block"),
    (None, None, {"rows": {}}),
    (None, None, _formats("not a row")),
    (None, None, _formats(_format_row(format="diff", model="", outcome="won"))),
    (None, None, _formats(_format_row("unusable", reason=""))),
    (None, None, _formats(_format_row("unmeasured", reason=None))),
    # A format no delivery used well is never a pass.
    (None, None, _formats(_format_row(well_formed=0))),
    (None, None, _formats(_format_row(criteria_passed=True, tokens_reported=-1))),
    (None, None, _formats(_format_row(well_formed_rate=1.5))),
    (None, None, _formats(_format_row(well_formed_rate=float("nan")))),
    (None, None, _formats(_format_row(well_formed_rate=True))),
]


@pytest.mark.parametrize("version", [5, 6, 7])
@pytest.mark.parametrize(("run_format", "summary", "block"), EDIT_FORMAT_CASES)
def test_the_edit_format_fields_read_the_same(version, run_format, summary, block):
    card = {**_base(), "schema_version": version}
    if run_format is not None:
        card["edit_format"] = run_format
    if summary is not None:
        card = _set(card, "specs.0.edit_format", summary)
    if block is not None:
        card["edit_formats"] = block
    _same(card)


def test_sound_edit_format_fields_are_valid_on_v7_only():
    card = _set(_base(), "specs.0.edit_format", _summary())
    card.update(edit_format="whole", edit_formats=_formats(_format_row()))
    assert bench_schema.validate({**card, "schema_version": 7}) == []
    assert bench_schema.validate({**card, "schema_version": 6}) == [
        "edit_format needs schema v7",
        "edit_formats needs schema v7",
        "specs[0].edit_format needs schema v7",
    ]
