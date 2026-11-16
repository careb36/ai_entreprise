"""Every way ``fitness.decide`` must not keep a candidate (rsi-loop §2.6).

Negative tests first (``docs/design/rsi-loop.md`` §6): a protected path
without a person's recorded approval (REQ-5), graders that differ, CI that
failed, is missing a check, was read from the wrong sha or not finished
(REQ-8), fewer tests collected, a Verifier bench case lost, the fake bench
below 100%, a target measure that did not go from fail to pass (REQ-15), a
regression beyond threshold and spread, too few complete runs or a void pair
(REQ-16, REQ-7), and any observation the platform did not produce (REQ-27).
The keep path is ``test_rsi_fitness_keep.py``.
"""

import pytest
from fitness_helpers import B_SHA, C_SHA, check_run, ci, codes, decide, observations
from services.evidence import fitness

# --- Protected paths (REQ-5) --------------------------------------------------


def _touch(obs, approval=None):
    obs["protected"]["touched"] = [
        {"path": "bench/specs/01-a/acceptance/test_x.py", "pattern": "bench/"},
        {"path": "scripts/bench/compare.py", "pattern": "scripts/bench/"},
    ]
    obs["protected"]["approval"] = approval
    return obs


def _approval(**overrides):
    return {
        "produced_by": fitness.PLATFORM,
        "sha": C_SHA,
        "by": "person-2",
        "paths": ["bench/specs/01-a/acceptance/test_x.py", "scripts/bench/compare.py"],
        **overrides,
    }


@pytest.mark.parametrize(
    "approval",
    [
        None,
        _approval(produced_by="candidate"),
        _approval(sha=B_SHA),
        _approval(paths=["scripts/bench/compare.py"]),
        _approval(by=""),
    ],
    ids=["none", "self-reported", "wrong-sha", "not-every-path", "no-person"],
)
def test_a_protected_path_without_a_recorded_approval_is_reverted(approval):
    result = decide(_touch(observations(), approval))
    assert result["decision"] == fitness.REVERT
    assert "protected_path" in codes(result, fitness.REVERT)


def test_a_protected_path_reverts_even_when_other_guards_are_unmeasured():
    obs = _touch(observations())
    obs["candidate"]["ci"] = None
    obs["candidate"]["bench"] = []
    assert decide(obs)["decision"] == fitness.REVERT


@pytest.mark.parametrize(
    "protected",
    [
        None,
        {"produced_by": "candidate", "sha": C_SHA, "touched": []},
        {"produced_by": fitness.PLATFORM, "sha": B_SHA, "touched": []},
    ],
    ids=["missing", "self-reported", "wrong-sha"],
)
def test_an_unknown_protected_path_match_is_unmeasured(protected):
    obs = observations()
    obs["protected"] = protected
    assert decide(obs)["decision"] == fitness.UNMEASURED


# --- Graders and shas -----------------------------------------------------------


def test_graders_that_differ_are_unmeasured():
    obs = observations()
    obs["graders"]["candidate_tree"] = "u" * 40
    result = decide(obs)
    assert result["decision"] == fitness.UNMEASURED
    assert "graders_differ" in codes(result)


def test_graders_unknown_are_unmeasured():
    obs = observations()
    obs["graders"] = None
    assert decide(obs)["decision"] == fitness.UNMEASURED


def test_the_same_sha_on_both_sides_is_unmeasured():
    obs = observations()
    obs["candidate"]["sha"] = B_SHA
    assert decide(obs)["decision"] == fitness.UNMEASURED


# --- CI read by sha (O7, REQ-8) ----------------------------------------------------


@pytest.mark.parametrize("conclusion", fitness.CI_FAILING)
def test_a_failing_check_run_on_c_is_reverted(conclusion):
    obs = observations()
    obs["candidate"]["ci"] = ci(
        C_SHA,
        check_run(C_SHA, "backend-tests", conclusion),
        check_run(C_SHA, "frontend-build"),
    )
    result = decide(obs)
    assert result["decision"] == fitness.REVERT
    assert "ci_failed" in codes(result, fitness.REVERT)


def test_a_rerun_that_failed_after_a_success_is_reverted():
    obs = observations()
    obs["candidate"]["ci"]["check_runs"] += [
        check_run(C_SHA, "backend-tests", "failure", run_id=9)
    ]
    assert decide(obs)["decision"] == fitness.REVERT


def test_a_check_green_on_b_and_absent_on_c_is_reverted():
    obs = observations()
    obs["candidate"]["ci"] = ci(C_SHA, check_run(C_SHA, "backend-tests"))
    result = decide(obs)
    assert result["decision"] == fitness.REVERT
    assert "ci_check_missing" in codes(result)


def test_a_check_run_read_from_the_wrong_sha_is_refused():
    obs = observations()
    obs["candidate"]["ci"]["check_runs"] = [
        check_run(B_SHA, "backend-tests"),
        check_run(B_SHA, "frontend-build"),
    ]
    result = decide(obs)
    assert result["decision"] == fitness.UNMEASURED
    assert "wrong_sha" in codes(result)


def test_a_listing_saved_for_another_sha_is_refused():
    obs = observations()
    obs["candidate"]["ci"]["sha"] = B_SHA
    assert decide(obs)["decision"] == fitness.UNMEASURED


def test_a_ci_result_not_read_by_the_platform_is_refused():
    obs = observations()
    obs["candidate"]["ci"]["produced_by"] = "candidate"
    result = decide(obs)
    assert result["decision"] == fitness.UNMEASURED
    assert "not_produced_by_platform" in codes(result)


def test_a_success_from_an_untrusted_app_does_not_count():
    obs = observations()
    obs["candidate"]["ci"]["check_runs"] = [
        check_run(C_SHA, "backend-tests", app="some-bot"),
        check_run(C_SHA, "frontend-build", app="some-bot"),
    ]
    assert decide(obs)["decision"] == fitness.UNMEASURED


def test_a_check_still_running_is_unmeasured():
    obs = observations()
    obs["candidate"]["ci"]["check_runs"][0].update(
        status="in_progress", conclusion=None
    )
    result = decide(obs)
    assert result["decision"] == fitness.UNMEASURED
    assert "ci_pending" in codes(result)


def test_a_partial_listing_is_unmeasured():
    obs = observations()
    obs["candidate"]["ci"]["incomplete"] = True
    assert decide(obs)["decision"] == fitness.UNMEASURED


def test_a_baseline_that_is_not_green_is_unmeasured():
    obs = observations()
    obs["baseline"]["ci"]["check_runs"][0]["conclusion"] = "failure"
    result = decide(obs)
    assert result["decision"] == fitness.UNMEASURED
    assert "baseline_not_green" in codes(result)


@pytest.mark.parametrize(
    "tests",
    [{"backend": 3999, "frontend": 900}, {"backend": 4000}],
    ids=["fewer", "suite-gone"],
)
def test_fewer_tests_collected_on_c_is_reverted(tests):
    obs = observations()
    obs["candidate"]["ci"]["tests_collected"]["suites"] = tests
    result = decide(obs)
    assert result["decision"] == fitness.REVERT
    assert "fewer_tests" in codes(result)


def test_tests_collected_unknown_is_unmeasured():
    obs = observations()
    obs["candidate"]["ci"]["tests_collected"] = None
    assert decide(obs)["decision"] == fitness.UNMEASURED


# --- The Verifier bench (O6) and the fake bench ---------------------------------------


def test_a_verifier_case_b_passes_and_c_fails_is_reverted():
    obs = observations()
    obs["candidate"]["verifier_bench"]["cases"]["planted-1"] = "fail"
    result = decide(obs)
    assert result["decision"] == fitness.REVERT
    assert "verifier_regressed" in codes(result)


def test_a_verifier_case_not_run_on_c_is_unmeasured():
    obs = observations()
    del obs["candidate"]["verifier_bench"]["cases"]["planted-1"]
    assert decide(obs)["decision"] == fitness.UNMEASURED


def test_a_verifier_bench_of_another_build_is_refused():
    obs = observations()
    obs["candidate"]["verifier_bench"]["sha"] = B_SHA
    assert decide(obs)["decision"] == fitness.UNMEASURED


def test_the_fake_bench_below_full_is_reverted():
    obs = observations()
    obs["candidate"]["fake_bench"]["criteria_passed"] = 29
    result = decide(obs)
    assert result["decision"] == fitness.REVERT
    assert "fake_below_full" in codes(result)


def test_the_fake_bench_missing_is_unmeasured():
    obs = observations()
    del obs["candidate"]["fake_bench"]
    assert decide(obs)["decision"] == fitness.UNMEASURED


# --- The target measure (O8, REQ-15) ---------------------------------------------------


@pytest.mark.parametrize(
    ("baseline", "candidate", "code"),
    [("fail", "fail", "not_flipped"), ("pass", "pass", "already_passed")],
)
def test_a_target_that_did_not_flip_is_reverted(baseline, candidate, code):
    obs = observations()
    obs["target"]["baseline"]["result"] = baseline
    obs["target"]["candidate"]["result"] = candidate
    result = decide(obs)
    assert result["decision"] == fitness.REVERT
    assert code in codes(result, fitness.REVERT)


def test_a_target_that_could_not_run_is_unmeasured():
    obs = observations()
    obs["target"]["candidate"]["result"] = "error"
    assert decide(obs)["decision"] == fitness.UNMEASURED


def test_a_target_result_reported_by_the_build_is_refused():
    obs = observations()
    obs["target"]["produced_by"] = "candidate"
    result = decide(obs)
    assert result["decision"] == fitness.UNMEASURED
    assert "not_produced_by_platform" in codes(result)


def test_a_target_read_at_another_sha_is_refused():
    obs = observations()
    obs["target"]["candidate"]["sha"] = "d" * 40
    assert decide(obs)["decision"] == fitness.UNMEASURED


def test_no_target_is_unmeasured():
    obs = observations()
    obs["target"] = None
    assert decide(obs)["decision"] == fitness.UNMEASURED


def test_a_metric_target_not_improved_beyond_noise_is_reverted():
    obs = observations()
    obs["target"] = {"produced_by": fitness.PLATFORM, "metric": "tokens_reported"}
    for r in obs["candidate"]["bench"]:
        r["totals"]["tokens_reported"] = 90_000  # 10% better, threshold 15%
    result = decide(obs)
    assert result["decision"] == fitness.REVERT
    assert "not_improved" in codes(result)


# --- The bench: regression, noise, missing runs (§2.4, §2.5) ------------------------------

_WORSE = {
    "criteria_rate": 0.84,
    "first_attempt_rate": 0.74,
    "blocked_share": 0.16,
    "rework_per_task": 0.76,
    "tokens_reported": 116_000,
    "wall_clock_seconds": 1_260.0,
}


@pytest.mark.parametrize("metric", sorted(_WORSE))
def test_a_regression_beyond_threshold_and_spread_is_reverted(metric):
    obs = observations()
    for r in obs["candidate"]["bench"]:
        r["totals"][metric] = _WORSE[metric]
    result = decide(obs)
    assert result["decision"] == fitness.REVERT
    row = next(
        m for m in result["metrics"] if m["scope"] == "totals" and m["metric"] == metric
    )
    assert row["outcome"] == fitness.REVERT


def test_a_regression_in_one_specification_is_reverted():
    obs = observations()
    for r in obs["candidate"]["bench"]:
        r["specs"]["01-a"]["criteria_rate"] = 0.5
    result = decide(obs)
    assert result["decision"] == fitness.REVERT
    assert any("01-a criteria_rate" in r["detail"] for r in result["reasons"])


def test_a_regression_wider_than_a_small_spread_is_reverted():
    obs = observations()
    for r, value in zip(obs["baseline"]["bench"], (0.88, 0.9, 0.92)):
        r["totals"]["criteria_rate"] = value  # spread 0.04 < rate 0.05
    for r in obs["candidate"]["bench"]:
        r["totals"]["criteria_rate"] = 0.84
    assert decide(obs)["decision"] == fitness.REVERT


def test_the_prompt_weight_is_judged_when_targeted():
    obs = observations()
    obs["target"]["prompt_targeted"] = True
    for r in obs["candidate"]["bench"]:
        r["totals"]["prompt_tokens_mean"] = 2_500.0
    assert decide(obs)["decision"] == fitness.REVERT


def test_a_missing_run_is_unmeasured():
    obs = observations()
    obs["candidate"]["bench"].pop()
    result = decide(obs)
    assert result["decision"] == fitness.UNMEASURED
    assert "too_few_runs" in codes(result)


@pytest.mark.parametrize("status", ["timeout", "budget_exceeded", "error"])
def test_an_incomplete_run_is_not_zero_and_leaves_too_few(status):
    obs = observations()
    obs["candidate"]["bench"][1]["status"] = status
    obs["candidate"]["bench"][1]["totals"]["criteria_rate"] = 0.0
    assert decide(obs)["decision"] == fitness.UNMEASURED


def test_a_void_pair_counts_on_neither_side():
    obs = observations()
    obs["candidate"]["bench"][2]["models"]["Developer"] = ["fallback-model"]
    result = decide(obs)
    assert result["decision"] == fitness.UNMEASURED
    assert {"void_pair", "too_few_runs"} <= codes(result)
    assert result["metrics"][0]["runs_b"] == result["metrics"][0]["runs_c"] == 2


def test_a_bench_run_reported_by_the_build_is_refused():
    obs = observations()
    obs["candidate"]["bench"][0]["produced_by"] = "candidate"
    assert decide(obs)["decision"] == fitness.UNMEASURED


def test_a_bench_run_of_another_commit_is_refused():
    obs = observations()
    obs["candidate"]["bench"][0]["sha"] = B_SHA
    result = decide(obs)
    assert result["decision"] == fitness.UNMEASURED
    assert "wrong_sha" in codes(result)


def test_a_metric_a_run_did_not_measure_is_unmeasured():
    obs = observations()
    obs["candidate"]["bench"][0]["totals"]["tokens_reported"] = None
    assert decide(obs)["decision"] == fitness.UNMEASURED


def test_a_relative_change_from_a_zero_baseline_is_unmeasured():
    obs = observations()
    for r in obs["baseline"]["bench"]:
        r["totals"]["tokens_reported"] = 0
    assert decide(obs)["decision"] == fitness.UNMEASURED


def test_unmeasured_wins_over_a_regression():
    obs = observations()
    obs["candidate"]["fake_bench"]["criteria_passed"] = 29
    obs["candidate"]["bench"].pop()
    result = decide(obs)
    assert result["decision"] == fitness.UNMEASURED
    assert "fake_below_full" in codes(result, fitness.REVERT)
