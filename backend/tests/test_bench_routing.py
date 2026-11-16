"""The bench by difficulty and model, and two routing policies (issue #319).

Each entry splits its tasks by difficulty and model, schema v2 checks the
split and still reads a v1 card, and two policies compare band by band and
by model.

No stack here: ``scripts/bench/run.sh`` drives the factory; these tests check
the pieces it relies on. The shared readers and rows are in ``bench_helpers``.
"""

import copy
import json
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "scripts" / "bench"))
import compare  # scripts/bench/compare.py, by path
import scorecard  # scripts/bench/scorecard.py, by path
from bench_helpers import _card, _comment, _entry

ROUTED_ITEMS = [
    {"id": 1, "status": "done", "comments": [], "covers": ["REQ-1.1"]},
    {
        "id": 2,
        "status": "blocked",
        "comments": [_comment("QA", "NEEDS WORK: a.")],
        "covers": ["REQ-1.1", "REQ-1.2", "REQ-2.1"],
    },
]


def _routed(models, reported=(10, 30), policy=None):
    return scorecard.routing_rows(
        ROUTED_ITEMS,
        {1: models[0], 2: models[1]},
        {1: 1, 2: 2},
        {
            1: [("reported", reported[0])],
            2: [("reported", reported[1]), ("estimated", 0)],
        },
        policy,
    )


def test_the_entry_splits_tasks_by_difficulty_and_model():
    rows = _routed(["cheap", "strong"], policy={"bands": {"1-3": "a", "4-5": "b"}})
    assert [(r["difficulty"], r["band"], r["model"]) for r in rows] == [
        (2, "1-3", "cheap"),
        (4, "4-5", "strong"),
    ]
    easy, hard = rows
    assert easy["done_first_attempt"] == 1 and easy["tokens"]["reported"] == 10
    assert hard["blocked_share"] == 1.0 and hard["rework_per_task"] == 1.0
    assert hard["tokens"] == {
        "reported": 30,
        "turns_reported": 1,
        "turns_unreported": 1,
    }
    entry = _entry()
    entry["routing"] = rows
    card = scorecard.build_scorecard(
        [entry], stamp="20260101T000000Z", commit="c", llm="fake", policy="bands-a"
    )
    assert card["policy"] == "bands-a" and scorecard.validate(card) == []
    text = scorecard.render_markdown(card)
    assert "routing policy `bands-a`" in text
    assert "- Difficulty 4, strong: 0/1 at the first attempt, 0 done" in text


@pytest.mark.parametrize(
    ("change", "problem"),
    [
        (lambda c: c.pop("policy"), "policy must be a non-empty string"),
        (lambda c: c["specs"][0].pop("routing"), "routing must be a list"),
        (lambda c: c["specs"][0]["routing"][0].update(difficulty=9), "difficulty"),
        (
            lambda c: c["specs"][0]["routing"][0].update(done=5),
            "done_first_attempt <= done",
        ),
        (
            lambda c: c["specs"][0]["routing"][0]["tokens"].pop("reported"),
            "routing[0].tokens.reported must be a count",
        ),
        (
            lambda c: c["specs"][0]["routing"][0].update(blocked_share=float("nan")),
            "blocked_share must be a finite number",
        ),
    ],
)
def test_the_schema_checks_the_split(change, problem):
    entry = _entry()
    entry["routing"] = _routed(["cheap", "strong"])
    card = _card(entry)
    change(card)
    assert any(problem in p for p in scorecard.validate(card)), scorecard.validate(card)


def test_a_version_1_card_is_still_read_without_the_split():
    card = _card()
    card["schema_version"] = 1
    card.pop("policy")
    for spec in card["specs"]:
        spec.pop("routing")
    assert scorecard.validate(card) == []


def test_two_policies_compare_band_by_band(tmp_path, capsys):
    before, after = _entry(), _entry()
    before["routing"] = _routed(["strong", "strong"], reported=(40, 60))
    after["routing"] = _routed(["cheap", "strong"], reported=(10, 60))
    old = _card(before, stamp="20260101T000000Z")
    old["policy"] = "all-strong"
    new = _card(after, stamp="20260102T000000Z")
    new["policy"] = "cheap-easy"
    bands = compare.by_difficulty(new)
    assert bands[2]["models"] == {"cheap"} and bands[2]["tokens"] == 10
    text = compare.render(old, new, compare.compare(old, new))
    assert "routing policy `all-strong` → `cheap-easy`" in text
    assert "### By difficulty (policy `all-strong` → `cheap-easy`)" in text
    assert (
        "| 2 | 1 → 1 | 1.00 → 1.00 | 0.00 → 0.00 | 0.00 → 0.00 | 40 → 10 | strong → cheap |"
        in text
    )
    # Written with --policy and compared from the command line.
    entries = []
    for name, entry in (("old", before), ("new", after)):
        path = tmp_path / f"{name}.json"
        path.write_text(json.dumps(entry))
        entries.append(path)
    out = tmp_path / "cards"
    for stamp, path, policy in (
        ("20260101T000000Z", entries[0], "all-strong"),
        ("20260102T000000Z", entries[1], "cheap-easy"),
    ):
        args = ["write", "--out-dir", str(out), "--stamp", stamp, "--policy", policy]
        assert scorecard.main([*args, str(path)]) == 0
    capsys.readouterr()
    assert compare.main([str(out / "20260102T000000Z.json"), "--dir", str(out)]) == 0
    assert "### By difficulty" in capsys.readouterr().out
    # Against a card from before the split, the table shows the new side only;
    # two such cards show none.
    legacy = copy.deepcopy(old)
    legacy["specs"][0].pop("routing")
    assert "By difficulty" in compare.render(legacy, new, compare.compare(legacy, new))
    assert compare.render_bands(legacy, legacy) == []


def test_the_stored_estimate_places_a_task_and_two_policies_compare_by_band_and_model():
    import routing_split  # scripts/bench/routing_split.py, by path

    # Task 1 has one criterion (low as planned) but its first attempt was
    # estimated high (the architecture mapped it to three modules).
    stored = {1: {"difficulty": {"score": 4, "band": "high"}}}
    before, after = _entry(), _entry()
    before["routing"] = _routed(["strong", "strong"], reported=(40, 60))
    after["routing"] = scorecard.routing_rows(
        ROUTED_ITEMS,
        {1: "cheap", 2: "strong"},
        {1: 1, 2: 2},
        {1: [("reported", 10)], 2: [("reported", 60)]},
        None,
        stored,
    )
    assert [(r["difficulty"], r["difficulty_band"]) for r in after["routing"]] == [
        (4, "high"),
        (4, "high"),
    ]
    old, new = _card(before), _card(after, stamp="20260102T000000Z")
    old["policy"], new["policy"] = "all-strong", "cheap-easy"
    assert scorecard.validate(new) == []
    bands = routing_split.split(new, "band")
    assert list(bands) == ["high"] and bands["high"]["tasks"] == 2
    # The bench prices no turn: the cost is unknown for every task, never 0.
    assert bands["high"]["cents"] is None and bands["high"]["cost_unknown"] == 2
    text = compare.render(old, new, compare.compare(old, new))
    assert "### By difficulty band (policy `all-strong` → `cheap-easy`)" in text
    assert "| low | 1 → – | 1 → – | 1.00 → – |" in text
    assert "| high | 1 → 2 | 0 → 1 |" in text
    assert "### By served model (policy `all-strong` → `cheap-easy`)" in text
    assert (
        "| cheap | – → 1 | – → 1 | – → 1.00 | – → 0.00 | – → 0.00 | – → 10 | – → unknown |"
        in text
    )
    # A card from before the band was recorded is placed by its score.
    legacy = copy.deepcopy(old)
    for row in legacy["specs"][0]["routing"]:
        row.pop("difficulty_band"), row.pop("cost_cents"), row.pop("cost_unknown")
    assert list(routing_split.split(legacy, "band")) == ["low", "high"]
    assert scorecard.validate(legacy) == []


@pytest.mark.parametrize(
    ("change", "problem"),
    [
        (lambda r: r.update(difficulty_band="extreme"), "difficulty_band must be"),
        (lambda r: r.update(cost_cents=-1), "cost_cents must be"),
        (lambda r: r.update(cost_cents=float("inf")), "cost_cents must be"),
        (lambda r: r.update(cost_unknown=-2), "cost_unknown must be a count"),
    ],
)
def test_the_schema_checks_the_optional_band_and_cost(change, problem):
    entry = _entry()
    entry["routing"] = _routed(["cheap", "strong"])
    card = _card(entry)
    change(card["specs"][0]["routing"][0])
    assert any(problem in p for p in scorecard.validate(card)), scorecard.validate(card)
