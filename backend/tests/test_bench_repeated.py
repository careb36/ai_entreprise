"""Repeated bench runs read as medians with their spread, ``compare.py
--time``, void pairs and unmeasured metrics (``docs/design/rsi-loop.md``
§2.4 and §2.5; REQ-6, REQ-7, REQ-16).

Wall-clock is judged only with ``--time`` (0.25 when given alone). With
several cards per side, a regression must exceed both the threshold and the
baseline's own spread; a pair served different models is void and counts on
neither side; a metric with fewer than K complete runs is unmeasured, listed
and never a pass (exit 3), and never read as zero.
"""

import json
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "scripts" / "bench"))
import compare  # scripts/bench/compare.py, by path
import repeated  # scripts/bench/repeated.py, by path
from bench_helpers import _card, _entry

LIMITS = {**compare.DEFAULTS}


def _run(
    stamp: str, passed=10, wall=100.0, models=None, status="completed", tokens=100
):
    """A card of one spec with ten criteria, *passed* of them passing; *stamp*
    is the side's day and the run's number ("12": day 2, run 2)."""
    results = {f"REQ-1.{i}": "pass" if i <= passed else "fail" for i in range(1, 11)}
    entry = _entry("01-a", status=status, results=results, tokens=tokens)
    entry["wall_clock_seconds"] = wall
    if models is not None:
        entry["models"] = models
    day, run = (stamp + "0")[:2]
    return _card(entry, stamp=f"2026010{day}T00000{run}Z")


def _side(prefix, **per_run):
    """Three runs; each keyword gives the three values of one field."""
    n = len(next(iter(per_run.values())))
    return [
        _run(f"{prefix}{i}", **{k: v[i] for k, v in per_run.items()}) for i in range(n)
    ]


# ---------------------------------------------------------------------------
# --time on two single cards
# ---------------------------------------------------------------------------


def _write(directory: Path, card: dict) -> Path:
    path = directory / f"{card['stamp']}.json"
    path.write_text(json.dumps(card))
    return path


def test_wall_clock_is_judged_only_with_time(tmp_path, capsys):
    before = _write(tmp_path, _run("1", wall=100.0))
    after = _write(tmp_path, _run("2", wall=130.0))
    argv = [str(after), "--previous", str(before)]
    assert compare.main(argv) == 0  # shown, never judged, without --time
    capsys.readouterr()
    assert compare.main([*argv, "--time"]) == 1  # 0.25 when given alone
    out = capsys.readouterr().out
    assert "total: wall-clock grew from 100 s to 130 s" in out
    assert "01-a: wall-clock grew from 100 s to 130 s" in out
    assert compare.main([*argv, "--time", "0.5"]) == 0
    within = _write(tmp_path, _run("3", wall=124.0))
    assert compare.main([str(within), "--previous", str(before), "--time"]) == 0


def test_the_time_default_is_the_owners_threshold():
    assert compare.TIME_DEFAULT == 0.25
    assert compare.DEFAULTS["time"] is None
    result = compare.compare(
        _run("1", wall=100.0), _run("2", wall=126.0), {"time": 0.25}
    )
    assert result["regressions"] == [
        "total: wall-clock grew from 100 s to 126 s",
        "01-a: wall-clock grew from 100 s to 126 s",
    ]
    faster = compare.compare(
        _run("1", wall=100.0), _run("2", wall=50.0), {"time": 0.25}
    )
    assert faster["regressions"] == []


# ---------------------------------------------------------------------------
# Medians and spread
# ---------------------------------------------------------------------------


def test_the_same_runs_are_no_regression():
    before = _side("1", passed=[10, 9, 10])
    after = _side("2", passed=[10, 9, 10])
    result = repeated.compare_runs(before, after, LIMITS)
    assert result["regressions"] == [] and result["unmeasured"] == []
    assert result["k"] == 3 and result["pairs"] == 3
    total = result["rows"][0]
    assert total["criteria_rate"][0] == {"median": 1.0, "spread": 0.1, "runs": 3}


def test_medians_not_single_runs_decide():
    # One bad candidate run among three: its median is unchanged.
    before = _side("1", passed=[9, 9, 9])
    after = _side("2", passed=[9, 5, 9])
    assert repeated.compare_runs(before, after, LIMITS)["regressions"] == []


@pytest.mark.parametrize(
    ("after", "regressed"),
    [
        ([8, 8, 8], False),  # 0.9 → 0.8: beyond 0.05, within the spread 0.2
        ([7, 7, 7], False),  # 0.2 worse: not more than the spread
        ([6, 6, 6], True),  # 0.3 worse: beyond both
    ],
)
def test_a_regression_must_exceed_the_threshold_and_the_baselines_spread(
    after, regressed
):
    before = _side("1", passed=[10, 8, 9])  # median 0.9, spread 0.2
    result = repeated.compare_runs(before, _side("2", passed=after), LIMITS)
    found = [r for r in result["regressions"] if "criteria passed" in r]
    assert bool(found) is regressed, result["regressions"]
    if regressed:
        assert found[0].startswith("total: criteria passed went from 0.90 to 0.60")
        assert "beyond 0.20" in found[0]


def test_without_spread_the_threshold_alone_decides():
    result = repeated.compare_runs(
        _side("1", passed=[9, 9, 9]), _side("2", passed=[8, 8, 8]), LIMITS
    )
    assert any("beyond 0.05" in r for r in result["regressions"]), result


def test_relative_metrics_are_widened_by_the_relative_spread():
    limits = {**LIMITS, "time": 0.25}
    steady = _side("1", wall=[100.0, 110.0, 120.0])  # median 110, spread 20 (18%)
    slower = _side("2", wall=[140.0, 140.0, 140.0])  # +27%
    found = repeated.compare_runs(steady, slower, limits)["regressions"]
    assert any(r.startswith("total: wall-clock grew from 110 to 140") for r in found)
    assert (
        repeated.compare_runs(steady, slower, LIMITS)["regressions"] == []
    )  # no --time
    noisy = _side("1", wall=[100.0, 150.0, 200.0])  # median 150, spread 100 (67%)
    much_slower = _side("2", wall=[200.0, 200.0, 200.0])  # +33%: within the spread
    assert repeated.compare_runs(noisy, much_slower, limits)["regressions"] == []
    # Tokens: totals only, by the same rule.
    tokens = repeated.compare_runs(
        _side("1", tokens=[100, 100, 100]), _side("2", tokens=[200, 200, 200]), LIMITS
    )["regressions"]
    assert [r for r in tokens if "tokens" in r] == [
        "total: reported tokens grew from 100 to 200 (medians over 3 runs, +100% beyond +50%)"
    ]


# ---------------------------------------------------------------------------
# Void pairs and unmeasured metrics
# ---------------------------------------------------------------------------

OTHER = {"Developer": ["another-model"]}


def test_a_pair_served_different_models_is_void():
    before = _side("1", passed=[10, 10, 10])
    after = _side("2", passed=[10, 0, 10], models=[None, OTHER, None])
    result = repeated.compare_runs(before, after, LIMITS)
    assert result["void_pairs"] == [
        {
            "before": "20260101T000001Z",
            "after": "20260102T000001Z",
            "roles": ["Developer"],
        }
    ]
    # The bad run is not counted, and neither is its partner: 2 of K = 3.
    assert result["pairs"] == 2
    assert result["regressions"] == []
    assert result["unmeasured"][0].startswith("total: criteria passed")
    assert "(2 of 3 complete runs before, 2 of 3 after)" in result["unmeasured"][0]
    # With K = 2 the two counted pairs are enough.
    two = repeated.compare_runs(before, after, LIMITS, runs=2)
    assert two["unmeasured"] == [] and two["regressions"] == []


def test_too_few_complete_runs_leave_a_metric_unmeasured_not_zero():
    before = _side(
        "1", passed=[10, 10, 10], status=["completed", "timeout", "completed"]
    )
    after = _side("2", passed=[10, 0, 10], status=["completed", "error", "completed"])
    result = repeated.compare_runs(before, after, LIMITS)
    assert result["regressions"] == []  # completed in 2 of 3 on both sides
    wheres = [u.split(":")[0] for u in result["unmeasured"]]
    assert wheres == ["total", "01-a"]
    row = result["rows"][1]
    assert row["criteria_rate"] == (None, None)  # unmeasured, never 0.0
    text = repeated.render(before, after, result)
    assert "| 01-a | 2 → 2 | unmeasured → unmeasured |" in text
    assert "### Unmeasured (never a pass)" in text
    assert "No regression among the measured metrics; the rest is unmeasured." in text


def test_a_spec_completing_less_often_is_a_regression():
    before = _side("1", status=["completed", "completed", "completed"])
    after = _side("2", status=["completed", "timeout", "completed"])
    result = repeated.compare_runs(before, after, LIMITS)
    assert "01-a: completed in 3 of 3 runs before, 2 of 3 now" in result["regressions"]
    assert result["unmeasured"]  # and its numbers are unmeasured, not judged


def test_a_card_without_a_partner_is_not_counted():
    result = repeated.compare_runs(
        _side("1", passed=[10, 10, 10]), _side("2", passed=[10, 10]), LIMITS
    )
    assert result["unpaired"] == ["20260101T000002Z"]
    assert result["pairs"] == 2 and result["unmeasured"]


def test_the_command_reads_repeated_runs_and_exits_by_what_it_found(tmp_path, capsys):
    def paths(cards):
        return [str(_write(tmp_path, c)) for c in cards]

    before = paths(_side("1", passed=[10, 9, 10]))
    prev = [x for p in before for x in ("--previous", p)]
    same = paths(_side("2", passed=[10, 10, 9]))
    assert compare.main([*same, *prev]) == 0
    out = capsys.readouterr().out
    assert "## Bench: 3 run(s) against 3, K = 3" in out
    assert "No regression beyond the thresholds and the baseline's spread." in out
    worse = paths(_side("3", passed=[5, 5, 5]))
    assert compare.main([*worse, *prev]) == 1
    assert "### Regressions" in capsys.readouterr().out
    void = paths(_side("4", passed=[10, 10, 10], models=[OTHER, None, None]))
    assert compare.main([*void, *prev]) == compare.EXIT_UNMEASURED
    assert "Void pair" in capsys.readouterr().out
    assert compare.main([*void, *prev, "--runs", "2"]) == 0
    # One card per side with --runs is a pair too: models differ → unmeasured.
    assert compare.main([void[0], "--previous", before[0], "--runs", "1"]) == 3
    assert compare.main([*same]) == 2  # repeated runs need the baseline
    bad = tmp_path / "bad.json"
    bad.write_text("{}")
    assert compare.main([*same, "--previous", str(bad), "--previous", before[0]]) == 2
    with pytest.raises(SystemExit):
        compare.main([*same, *prev, "--runs", "0"])


def test_served_models_are_the_union_per_role():
    card = _card(
        _entry("01-a"),
        {**_entry("02-b"), "models": {"QA": ["m2"], "Developer": ["m3"]}},
    )
    assert repeated.served_models(card) == {
        "Developer": ["fake-model", "m3"],
        "QA": ["m2"],
    }
