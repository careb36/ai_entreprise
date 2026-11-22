"""The paired comparison of repeated bench runs (``scripts/bench/paired.py``):
McNemar on the criteria, Wilcoxon on the numbers, a verdict only when p is
below alpha. Reference p values are the textbook ones (exact binomial and
the enumerated signed-rank distribution)."""

import json
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "scripts" / "bench"))
import paired  # scripts/bench/paired.py, by path
from bench_helpers import _card, _entry

PASS4 = {f"REQ-1.{n}": "pass" for n in range(1, 5)}
FAIL4 = {f"REQ-1.{n}": "fail" for n in range(1, 5)}


@pytest.mark.parametrize(
    ("b", "c", "p"),
    [(0, 0, 1.0), (0, 5, 0.0625), (1, 9, 0.021484375), (5, 5, 1.0)],
)
def test_mcnemar_exact(b, c, p):
    assert paired.mcnemar(b, c) == pytest.approx(p)


def test_mcnemar_large_uses_the_corrected_chi_square():
    # 30 discordant, 10 vs 20: chi2 = (10-1)^2 / 30 = 2.7, p = 0.1003.
    assert paired.mcnemar(10, 20) == pytest.approx(0.1003, abs=1e-4)


@pytest.mark.parametrize(
    ("deltas", "p"),
    [
        ([1, 2, 3, 4, 5], 0.0625),
        ([1, 2, 3, 4, 5, 6], 0.03125),
        ([1, -2, 3, -4, 5, -6], 0.84375),
        ([0, 0, 0], 1.0),
    ],
)
def test_wilcoxon_exact(deltas, p):
    assert paired.wilcoxon(deltas) == pytest.approx(p)


def test_the_bootstrap_interval_is_seeded_and_holds_the_mean():
    deltas = [0.1, 0.4, 0.2, 0.6, 0.3]
    low, high = paired.bootstrap_ci(deltas)
    assert paired.bootstrap_ci(deltas) == (low, high)
    assert low <= sum(deltas) / len(deltas) <= high
    assert paired.bootstrap_ci([0.5, 0.5]) == (0.5, 0.5)


def _cards(results, stamp_base, name="05-a"):
    return [
        _card(_entry(name=name, results=r), stamp=f"2026010{i}T00000{stamp_base}Z")
        for i, r in enumerate(results, start=1)
    ]


def test_one_pair_never_gives_a_verdict():
    result = paired.compare(_cards([FAIL4], "0"), _cards([PASS4], "1"))

    assert result["pairs"] == 1
    assert result["criteria"]["fixed"] == 4 and result["criteria"]["broken"] == 0
    assert result["criteria"]["verdict"] == paired.SAME


def test_enough_fixed_criteria_are_better_and_broken_ones_worse():
    before, after = _cards([FAIL4] * 3, "0"), _cards([PASS4] * 3, "1")

    better = paired.compare(before, after)
    worse = paired.compare(after, before)

    assert better["criteria"]["fixed"] == 12 and better["criteria"]["p"] < 0.001
    assert better["criteria"]["verdict"] == paired.BETTER
    assert worse["criteria"]["verdict"] == paired.WORSE


def test_a_role_only_one_side_called_does_not_void_the_pair():
    before = _cards([FAIL4], "0")
    before[0]["specs"][0]["models"]["ScrumMaster"] = ["fake-model"]
    assert paired.compare(before, _cards([PASS4], "1"))["pairs"] == 1


def test_a_shared_role_on_another_model_voids_the_pair():
    after = _cards([PASS4], "1")
    after[0]["specs"][0]["models"]["Developer"] = ["other-model"]
    result = paired.compare(_cards([FAIL4], "0"), after)
    assert result["pairs"] == 0 and result["void"][0]["roles"] == ["Developer"]


def test_only_specs_both_cards_completed_count():
    before = _cards([FAIL4], "0")
    after = [
        _card(
            _entry(name="05-a", status="timeout", results=PASS4),
            stamp="20260101T000001Z",
        )
    ]
    result = paired.compare(before, after)
    assert result["criteria"]["fixed"] == 0
    assert all(row["n"] == 0 for row in result["numbers"])


def test_the_cli_exits_one_when_something_is_significantly_worse(tmp_path, capsys):
    paths = {}
    for side, results in (("b", PASS4), ("c", FAIL4)):
        for i, card in enumerate(_cards([results] * 3, "0" if side == "b" else "1")):
            path = tmp_path / f"{side}{i}.json"
            path.write_text(json.dumps(card), encoding="utf-8")
            paths.setdefault(side, []).append(str(path))
    previous = [arg for p in paths["b"] for arg in ("--previous", p)]

    assert paired.main([*paths["c"], *previous]) == 1
    assert "| criteria (McNemar) | 12 discordant |" in capsys.readouterr().out
    assert (
        paired.main([*paths["b"], *[a for p in paths["b"] for a in ("--previous", p)]])
        == 0
    )
