"""The bench's scorecard (issue #316).

First attempts are counted from the board; the totals, the schema and the
Markdown summary; the committed scorecards keep to the schema; missing
observations are never green: a failed collection is an error entry, the
schema wants finite numbers and every counter, and a card off the schema is
not written or compared.

No stack here: ``scripts/bench/run.sh`` drives the factory; these tests check
the pieces it relies on. The shared readers and rows are in ``bench_helpers``.
"""

import json
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "scripts" / "bench"))
import compare  # scripts/bench/compare.py, by path
import scorecard  # scripts/bench/scorecard.py, by path
from bench_helpers import BENCH, _card, _comment, _entry


def test_a_spec_entry_counts_first_attempts_from_the_board():
    deliverable = _comment("Dev", "Delivered.")
    items = [
        # One delivery, approved: the first attempt.
        {
            "id": 1,
            "status": "done",
            "comments": [deliverable, _comment("QA", "APPROVED: fine.")],
        },
        # A quality gate sent it back before any run was ingested: one run, not first.
        {
            "id": 2,
            "status": "done",
            "comments": [
                _comment("Quality gate", "NEEDS WORK: too short."),
                deliverable,
            ],
        },
        # QA asked twice, and the factory deduplicated a repeat: still one run.
        {
            "id": 3,
            "status": "done",
            "comments": [
                _comment("QA", "NEEDS WORK: a."),
                _comment("Verifier", "NEEDS WORK: b."),
            ],
        },
        # A person sent it back to work.
        {
            "id": 4,
            "status": "done",
            "comments": [_comment("user", "Sent back to work: again.")],
        },
        {"id": 5, "status": "blocked", "comments": []},
        # Two deliveries with no request on the board are not a first attempt either.
        {"id": 6, "status": "done", "comments": []},
        # A carry-over note quotes another task's verdict, not this one's.
        {
            "id": 7,
            "status": "done",
            "comments": [_comment("system", "Carried over from task 3: NEEDS WORK: x")],
        },
    ]
    entry = scorecard.summarize_spec(
        name="x",
        status="completed",
        items=items,
        runs_per_task={1: 1, 2: 1, 3: 1, 4: 1, 5: 1, 6: 2, 7: 1},
        graded={
            "passed": 1,
            "total": 2,
            "results": {"REQ-1.1": "pass", "REQ-1.2": "fail"},
        },
        factory_verified=None,
        wall_clock_seconds=5,
        tokens={},
        models={},
    )
    assert entry["tasks"] == {
        "total": 7,
        "done": 6,
        "blocked": 1,
        "done_first_attempt": 2,
    }
    assert entry["rework_requests"] == 3
    assert (entry["first_attempt_rate"], entry["blocked_share"]) == (0.2857, 0.1429)
    assert entry["tokens"] == {
        "reported": 0,
        "turns_reported": 0,
        "turns_unreported": 0,
    }


def test_the_scorecard_totals_and_its_schema():
    card = _card(_entry("01-a"), _entry("02-b", results={"REQ-1.1": "fail"}, tokens=50))
    assert scorecard.validate(card) == []
    totals = card["totals"]
    assert (
        totals["criteria_passed"],
        totals["criteria_total"],
        totals["criteria_rate"],
    ) == (2, 3, 0.6667)
    assert (
        totals["tasks"],
        totals["done_first_attempt"],
        totals["tokens_reported"],
    ) == (4, 4, 150)
    assert card["specs"][0]["models"] == {"Developer": ["fake-model"]}
    assert json.loads(json.dumps(card)) == card


@pytest.mark.parametrize(
    ("change", "problem"),
    [
        (lambda c: c.update(schema_version=5), "schema_version"),
        (lambda c: c.update(commit=""), "commit"),
        (lambda c: c["specs"][0].update(status="done"), "status"),
        (lambda c: c["specs"][0]["criteria"].update(passed=7), "criteria.passed"),
        (
            lambda c: c["specs"][0]["criteria"]["results"].update({"REQ-9.9": "maybe"}),
            "pass, fail, missing",
        ),
        (
            lambda c: c["specs"][0]["tasks"].update(done_first_attempt=9),
            "done_first_attempt",
        ),
        (lambda c: c["specs"][0].pop("tokens"), "tokens"),
        (lambda c: c["totals"].pop("criteria_rate"), "totals.criteria_rate"),
        (lambda c: c.update(specs={}), "specs must be a list"),
    ],
)
def test_the_schema_names_what_is_wrong(change, problem):
    card = _card()
    change(card)
    assert any(problem in p for p in scorecard.validate(card))


def test_the_markdown_summary():
    text = scorecard.render_markdown(
        _card(_entry("01-a"), _entry("02-b", results={"REQ-1.1": "fail"}))
    )
    assert "| 01-a | completed | 2/2 | 2/2 | 0.00 | 0% | 12 s | 100 |" in text
    assert "| **Total** | 2/2 completed | 2/3 |" in text
    assert "- Criteria not passing: REQ-1.1" in text


def test_the_committed_scorecards_keep_to_the_schema():
    cards = sorted((BENCH / "scorecards").glob("*.json"))
    assert cards, "a reference scorecard is committed for compare.py"
    for path in cards:
        card = json.loads(path.read_text(encoding="utf-8"))
        assert scorecard.validate(card) == [], path.name
        assert path.stem == card["stamp"]
        assert (path.with_suffix(".md")).is_file()


def test_a_failed_collection_marks_the_entry_error(tmp_path):
    grade_file = tmp_path / "grade.json"
    grade_file.write_text(json.dumps({"passed": 0, "total": 0, "results": {}}))
    out = tmp_path / "entry.json"
    status = scorecard.main(
        [
            "collect",
            "--api",
            "http://127.0.0.1:9",
            "--name",
            "01-a",
            "--status",
            "completed",
            "--company",
            "1",
            "--sprint",
            "2",
            "--grade",
            str(grade_file),
            "--out",
            str(out),
        ]
    )
    entry = json.loads(out.read_text())
    assert status == 0
    assert entry["status"] == "error"
    assert "collection failed" in entry["error"]


@pytest.mark.parametrize(
    ("change", "problem"),
    [
        (
            lambda c: c["totals"].update(criteria_rate=float("nan")),
            "totals.criteria_rate must be a finite number",
        ),
        (
            lambda c: c["totals"].update(tokens_reported="many"),
            "totals.tokens_reported must be a finite number",
        ),
        (
            lambda c: c["totals"].update(blocked_share=float("inf")),
            "totals.blocked_share",
        ),
        (lambda c: c["totals"].update(tasks=True), "totals.tasks"),
        (
            lambda c: c["specs"][0]["tokens"].pop("turns_unreported"),
            "tokens.turns_unreported must be a count",
        ),
        (
            lambda c: c["specs"][0]["tokens"].update(reported=-1),
            "tokens.reported must be a count",
        ),
        (
            lambda c: c["specs"][0].update(rework_per_task=float("nan")),
            "rework_per_task must be a finite number",
        ),
        (
            lambda c: c["specs"][0]["criteria"].update(total="2"),
            "criteria needs counts",
        ),
    ],
)
def test_the_schema_wants_finite_numbers_and_every_counter(change, problem):
    card = _card()
    change(card)
    assert any(problem in p for p in scorecard.validate(card)), scorecard.validate(card)


def test_writing_a_card_off_the_schema_exits_2(tmp_path):
    entry = _entry()
    entry["tokens"] = {"reported": 1}
    path = tmp_path / "entry.json"
    path.write_text(json.dumps(entry))
    assert scorecard.main(["write", "--out-dir", str(tmp_path / "out"), str(path)]) == 2
    assert not (tmp_path / "out").exists()


def test_compare_refuses_a_card_with_nan(tmp_path):
    card = _card(stamp="20260105T000000Z")
    card["totals"]["criteria_rate"] = float("nan")
    path = tmp_path / "20260105T000000Z.json"
    path.write_text(json.dumps(card))  # json writes NaN, and reads it back
    assert compare.main([str(path), "--dir", str(tmp_path)]) == 2
