"""When ``fitness.decide`` keeps a candidate, and that it stays pure (rsi-loop §2.6).

Every guard passes and the target measure goes from fail on B to pass on C
(REQ-15). Noise within the baseline's spread, a re-run, an approved protected
path and a check from an untrusted app do not prevent it. The negative tests
are ``test_rsi_fitness.py``.
"""

import copy

import pytest
from fitness_helpers import C_SHA, check_run, codes, decide, observations, run
from services.evidence import fitness


def test_a_candidate_that_passes_every_guard_and_flips_the_target_is_kept():
    result = decide(observations())
    assert result["decision"] == fitness.KEEP
    assert codes(result, fitness.REVERT) == codes(result, fitness.UNMEASURED) == set()
    assert {
        "none",
        "equal",
        "green",
        "not_fewer",
        "no_regression",
        "full",
        "flipped",
    } <= codes(result, fitness.PASS)
    assert result["k"] == 3
    assert result["thresholds"]["tokens"] == 0.15
    assert result["thresholds"]["time"] == 0.25


def test_a_change_within_the_thresholds_is_kept():
    obs = observations()
    for r in obs["candidate"]["bench"]:
        r["totals"].update(
            criteria_rate=0.86, tokens_reported=114_000, wall_clock_seconds=1_240.0
        )
    assert decide(obs)["decision"] == fitness.KEEP


def test_the_baseline_spread_widens_the_threshold():
    obs = observations()
    for r, value in zip(obs["baseline"]["bench"], (0.8, 0.9, 1.0)):
        r["totals"]["criteria_rate"] = value  # spread 0.2
    for r in obs["candidate"]["bench"]:
        r["totals"]["criteria_rate"] = 0.75  # 0.15 worse: within the spread
    result = decide(obs)
    assert result["decision"] == fitness.KEEP
    row = next(
        m
        for m in result["metrics"]
        if m["scope"] == "totals" and m["metric"] == "criteria_rate"
    )
    assert row["threshold"] == pytest.approx(0.2)


def test_the_latest_rerun_of_a_check_decides():
    obs = observations()
    obs["candidate"]["ci"]["check_runs"] = [
        check_run(C_SHA, "backend-tests", "failure", run_id=1),
        check_run(C_SHA, "backend-tests", "success", run_id=2),
        check_run(C_SHA, "frontend-build"),
    ]
    assert decide(obs)["decision"] == fitness.KEEP


def test_a_protected_path_a_person_approved_at_c_is_kept():
    obs = observations()
    obs["protected"]["touched"] = [
        {"path": "scripts/bench/compare.py", "pattern": "scripts/bench/"}
    ]
    obs["protected"]["approval"] = {
        "produced_by": fitness.PLATFORM,
        "sha": C_SHA,
        "by": "person-2",
        "paths": ["scripts/bench/compare.py"],
    }
    result = decide(obs)
    assert result["decision"] == fitness.KEEP
    assert "approved" in codes(result, fitness.PASS)


def test_a_metric_target_improved_beyond_noise_is_kept():
    obs = observations()
    obs["target"] = {"produced_by": fitness.PLATFORM, "metric": "tokens_reported"}
    for r in obs["candidate"]["bench"]:
        r["totals"]["tokens_reported"] = 80_000  # 20% better, threshold 15%
    result = decide(obs)
    assert result["decision"] == fitness.KEEP
    assert "improved" in codes(result)


def test_a_check_from_an_untrusted_app_is_ignored_and_named():
    obs = observations()
    obs["candidate"]["ci"]["check_runs"].append(
        check_run(C_SHA, "claims-green", "failure", app="some-bot")
    )
    result = decide(obs)
    assert result["decision"] == fitness.KEEP
    assert "untrusted_app" in codes(result, fitness.IGNORED)


def test_an_unpaired_extra_run_is_ignored():
    obs = observations()
    obs["baseline"]["bench"].append(run(obs["baseline"]["sha"], 7))
    result = decide(obs)
    assert result["decision"] == fitness.KEEP
    assert "unpaired" in codes(result, fitness.IGNORED)


def test_the_prompt_weight_is_shown_not_judged_unless_targeted():
    obs = observations()
    for r in obs["candidate"]["bench"]:
        r["totals"]["prompt_tokens_mean"] = 2_500.0
    result = decide(obs)
    assert result["decision"] == fitness.KEEP
    assert all(m["metric"] != "prompt_tokens_mean" for m in result["metrics"])


def test_the_decision_does_not_change_its_inputs_and_repeats():
    obs = observations()
    before = copy.deepcopy(obs)
    first, second = decide(obs), decide(obs)
    assert obs == before
    assert first == second
