"""The bench's ``compare.py`` (issue #316).

The same numbers are no regression; a drop beyond the threshold is, a change
within it is not; a new specification is listed, not judged; the previous
scorecard is the newest earlier one, the command exits by what it found, and
the committed reference compares clean with itself.

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
from bench_helpers import BENCH, _card, _entry


def test_the_same_numbers_are_no_regression():
    card = _card()
    assert compare.compare(card, copy.deepcopy(card))["regressions"] == []


@pytest.mark.parametrize(
    ("after", "expected"),
    [
        (
            _entry(results={"REQ-1.1": "pass", "REQ-1.2": "fail"}),
            "criteria passed went from 1.00 to 0.50",
        ),
        (_entry(runs={1: 2, 2: 1}), "done at the first attempt went from 1.00 to 0.50"),
        (_entry(rework=2), "rework per task went from 0.00 to 1.00"),
        (_entry(tokens=200), "reported tokens grew from 100 to 200"),
        (_entry(status="timeout"), "01-a: completed before, timeout now"),
    ],
)
def test_a_drop_beyond_the_threshold_is_a_regression(after, expected):
    result = compare.compare(_card(_entry()), _card(after))
    assert any(expected in r for r in result["regressions"]), result["regressions"]


def test_a_change_within_the_threshold_is_not():
    result = compare.compare(_card(_entry(tokens=100)), _card(_entry(tokens=140)))
    assert result["regressions"] == []
    loose = compare.compare(
        _card(_entry()), _card(_entry(rework=2)), {"rework": 1.5, "rate": 0.5}
    )
    assert loose["regressions"] == []


def test_a_new_specification_is_listed_not_judged():
    result = compare.compare(
        _card(_entry("01-a")),
        _card(_entry("01-a"), _entry("02-b", results={"REQ-1.1": "fail"})),
    )
    assert result["new_specs"] == ["02-b"]
    assert all("02-b" not in r for r in result["regressions"])


def _write(directory: Path, card: dict) -> Path:
    path = directory / f"{card['stamp']}.json"
    path.write_text(json.dumps(card))
    return path


def test_the_previous_scorecard_is_the_newest_earlier_one(tmp_path):
    a = _write(tmp_path, _card(stamp="20260101T000000Z"))
    b = _write(tmp_path, _card(stamp="20260102T000000Z"))
    c = _write(tmp_path, _card(stamp="20260103T000000Z"))
    assert compare.previous_of(c, tmp_path) == b
    assert compare.previous_of(b, tmp_path) == a
    assert compare.previous_of(a, tmp_path) is None


def test_the_command_exits_by_what_it_found(tmp_path, capsys):
    first = _write(tmp_path, _card(stamp="20260101T000000Z"))
    assert compare.main([str(first), "--dir", str(tmp_path)]) == 0
    assert "first reference" in capsys.readouterr().out
    same = _write(tmp_path, _card(stamp="20260102T000000Z"))
    assert compare.main([str(same), "--dir", str(tmp_path)]) == 0
    assert "No regression beyond the thresholds." in capsys.readouterr().out
    worse = _write(
        tmp_path,
        _card(
            _entry(results={"REQ-1.1": "fail", "REQ-1.2": "fail"}),
            stamp="20260103T000000Z",
        ),
    )
    assert compare.main([str(worse), "--dir", str(tmp_path)]) == 1
    assert "### Regressions" in capsys.readouterr().out
    bad = tmp_path / "20260104T000000Z.json"
    bad.write_text(json.dumps({"schema_version": 1}))
    assert compare.main([str(bad), "--dir", str(tmp_path)]) == 2


def test_the_committed_reference_compares_clean_with_itself():
    latest = max((BENCH / "scorecards").glob("*.json"))
    assert compare.main([str(latest), "--previous", str(latest)]) == 0
