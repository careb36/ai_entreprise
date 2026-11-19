"""The /bench page's SWE-bench Lite section (schema v6, issue #687):
``GET /v1/bench/scorecards`` and ``services/models/bench_scorecards.py``.

EARS criteria held here:

- WHEN a v6 card holds a ``swebench`` block, its entry SHALL carry the resolve
  rate, the counts (graded, measured, resolved, failed, unmeasured), the
  dataset as pinned and each instance's outcome with its FAIL_TO_PASS and
  PASS_TO_PASS counts, as the card holds them.
- WHERE an instance's environment could not be built or did not reproduce it,
  its outcome SHALL be ``measured: false`` with the card's reason, and its
  test counts ``measured: false`` when its tests did not run, never 0; the
  resolve rate SHALL leave it out, as the card does.
- WHERE no instance was measured, the resolve rate SHALL be ``measured:
  false``, never 0; a card without the block SHALL carry ``swebench: null``.
- IF a ``swebench`` block sits on a card older than v6, THEN the card SHALL be
  listed ``invalid`` with the schema's problem.
"""

from __future__ import annotations

from pathlib import Path

import pytest
from config import settings
from test_scorecard_history import _card, _write


@pytest.fixture()
def repo(tmp_path, monkeypatch):
    monkeypatch.setattr(settings, "BENCH_REPOSITORY_DIR", str(tmp_path))
    return tmp_path


def _cards_dir(root: Path) -> Path:
    return root / "bench" / "scorecards"


def _row(iid: str, outcome: str, reason=None, turns_reported=3) -> dict:
    ran = outcome != "unmeasured"
    return {
        "instance_id": iid,
        "repo": "pallets/flask",
        "spec": "swe-" + iid.replace("__", "-"),
        "status": "completed",
        "outcome": outcome,
        "reason": reason,
        "isolation": "venv",
        "error": None,
        "fail_to_pass": {"passed": 2 if outcome == "pass" else 0, "total": 2}
        if ran
        else None,
        "pass_to_pass": {"passed": 40, "total": 40} if ran else None,
        "grade_seconds": 12.0,
        "tasks": {"total": 1, "done": 1, "blocked": 0, "done_first_attempt": 1},
        "rework_requests": 0,
        "wall_clock_seconds": 300.0,
        "tokens": {
            "reported": 5000 if turns_reported else 0,
            "turns_reported": turns_reported,
            "turns_unreported": 1,
        },
        "models": {},
    }


def _swe_card(rows, skipped=(), version=6) -> dict:
    count = {k: sum(r["outcome"] == k for r in rows) for k in ("pass", "fail")}
    measured = count["pass"] + count["fail"]
    card = _card(version=version)
    card["swebench"] = {
        "dataset": {
            "name": "princeton-nlp/SWE-bench_Lite",
            "revision": "6ec7bb89b934" + "0" * 28,
            "split": "test",
            "source": "example/SWE-bench_Lite",
            "license": "none declared; each instance is its repository's",
            "instances": {r["instance_id"]: "e" * 64 for r in rows},
        },
        "subset": [r["instance_id"] for r in rows] + list(skipped),
        "instances": rows,
        "skipped": list(skipped),
        "graded": len(rows),
        "measured": measured,
        "resolved": count["pass"],
        "failed": count["fail"],
        "unmeasured": len(rows) - measured,
        "resolved_rate": round(count["pass"] / measured, 4) if measured else None,
        "tokens_reported": sum(r["tokens"]["reported"] for r in rows),
        "turns_unreported": sum(r["tokens"]["turns_unreported"] for r in rows),
    }
    return card


def _entry(test_client) -> dict:
    return test_client.get("/v1/bench/scorecards").json()["cards"][0]


def test_a_swebench_block_carries_the_rate_and_each_outcome(repo, test_client):
    rows = [
        _row("pallets__flask-4992", "pass"),
        _row("pallets__flask-5063", "fail", "FAIL_TO_PASS: 2 of 2 failed"),
    ]
    _write(_cards_dir(repo), "20261114T201804Z.json", _swe_card(rows, ["a__b-1"]))

    entry = _entry(test_client)

    assert entry["status"] == "valid", entry["problems"]
    swe = entry["swebench"]
    assert swe["resolved_rate"] == {"measured": True, "value": 0.5}
    counts = ("graded", "measured", "resolved", "failed", "unmeasured")
    assert tuple(swe[k] for k in counts) == (2, 2, 1, 1, 0)
    assert swe["dataset"]["revision"].startswith("6ec7bb89b934")
    assert swe["dataset"]["pinned"] == 2
    assert swe["skipped"] == ["a__b-1"]
    assert swe["tokens"] == {
        "measured": True,
        "value": 10000,
        "turns_reported": 6,
        "turns_unreported": 2,
    }
    resolved, failed = swe["instances"]
    assert resolved["outcome"] == {"measured": True, "value": "pass"}
    assert resolved["fail_to_pass"] == {
        "measured": True,
        "value": {"passed": 2, "total": 2},
    }
    assert failed["outcome"] == {"measured": True, "value": "fail"}
    assert failed["reason"] == "FAIL_TO_PASS: 2 of 2 failed"
    assert failed["wall_clock_seconds"] == 300.0
    # The instances are not reference specifications.
    assert [s["name"] for s in entry["specs"]] == ["01-cli"]


def test_an_unmeasured_instance_is_never_a_zero_and_left_out_of_the_rate(
    repo, test_client
):
    rows = [
        _row("pallets__flask-4992", "pass"),
        _row("psf__requests-3362", "unmeasured", "no environment", turns_reported=0),
    ]
    _write(_cards_dir(repo), "20261114T201804Z.json", _swe_card(rows))

    swe = _entry(test_client)["swebench"]

    assert swe["resolved_rate"] == {"measured": True, "value": 1.0}
    assert (swe["measured"], swe["unmeasured"]) == (1, 1)
    row = swe["instances"][1]
    assert row["outcome"] == {
        "measured": False,
        "reason": "grade_unmeasured",
        "detail": "no environment",
    }
    for key in ("fail_to_pass", "pass_to_pass"):
        assert row[key] == {"measured": False, "reason": "tests_not_run"}
    assert row["tokens"]["measured"] is False
    assert "value" not in row["tokens"]


def test_no_instance_measured_and_cards_without_the_block(repo, test_client):
    cards = _cards_dir(repo)
    only = [_row("psf__requests-3362", "unmeasured", "no environment")]
    _write(cards, "20261114T201804Z.json", _swe_card(only))
    _write(cards, "20261108T140751Z.json", _card("20261108T140751Z"))

    body = test_client.get("/v1/bench/scorecards").json()
    entries = {c["file"]: c for c in body["cards"]}

    swe = entries["20261114T201804Z.json"]["swebench"]
    assert swe["resolved_rate"] == {
        "measured": False,
        "reason": "no_instances_measured",
    }
    assert entries["20261108T140751Z.json"]["swebench"] is None


def test_a_swebench_block_on_an_older_card_is_invalid(repo, test_client):
    card = _swe_card([_row("pallets__flask-4992", "pass")], version=5)
    _write(_cards_dir(repo), "20261114T201804Z.json", card)

    entry = _entry(test_client)

    assert entry["status"] == "invalid"
    assert entry["problems"] == ["swebench needs schema v6"]
