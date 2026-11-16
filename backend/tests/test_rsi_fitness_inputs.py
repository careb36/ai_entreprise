"""Malformed inputs to ``fitness.decide`` fail closed (review of PR #621).

A protected-path match without a valid ``touched`` list, a Verifier case on
either side that is neither ``pass`` nor ``fail``, and a test count without
its own producer and sha are unknown, never an empty diff, a pass or a
count (rule 7 of ``docs/POSITIONING.md``). A check that counts as green on B
(``success``, ``neutral``, ``skipped``) and is absent on C is a missing
check. The other negative tests are ``test_rsi_fitness.py``.
"""

import pytest
from fitness_helpers import B_SHA, C_SHA, check_run, codes, decide, observations
from services.evidence import fitness


@pytest.mark.parametrize(
    "touched",
    [
        None,
        "bench/x.py",
        {},
        [None],
        [{"pattern": "bench/"}],
        [{"path": ""}],
        [{"path": 3}],
    ],
    ids=[
        "null",
        "not-a-list",
        "object",
        "null-entry",
        "no-path",
        "empty-path",
        "path-not-text",
    ],
)
def test_a_protected_match_without_a_valid_touched_list_is_unmeasured(touched):
    obs = observations()
    obs["protected"]["touched"] = touched
    result = decide(obs)
    assert result["decision"] == fitness.UNMEASURED
    assert "malformed" in codes(result, fitness.UNMEASURED)


def test_a_protected_match_without_touched_is_unmeasured():
    obs = observations()
    del obs["protected"]["touched"]
    result = decide(obs)
    assert result["decision"] == fitness.UNMEASURED
    assert "none" not in codes(result, fitness.PASS)


def test_only_an_empty_list_means_no_protected_path():
    result = decide(observations())
    assert result["decision"] == fitness.KEEP
    assert "none" in codes(result, fitness.PASS)


@pytest.mark.parametrize("side", ["baseline", "candidate"])
@pytest.mark.parametrize("value", ["passed", None, "", "PASS", 1])
def test_a_verifier_case_neither_pass_nor_fail_is_unmeasured(side, value):
    obs = observations()
    obs[side]["verifier_bench"]["cases"]["control-1"] = value
    result = decide(obs)
    assert result["decision"] == fitness.UNMEASURED
    assert "malformed" in codes(result, fitness.UNMEASURED)


def test_a_verifier_case_only_c_ran_with_an_odd_outcome_is_unmeasured():
    obs = observations()
    obs["candidate"]["verifier_bench"]["cases"]["extra"] = "flaky"
    assert decide(obs)["decision"] == fitness.UNMEASURED


def test_verifier_cases_that_are_not_an_object_are_unmeasured():
    obs = observations()
    obs["baseline"]["verifier_bench"]["cases"] = ["planted-1"]
    assert decide(obs)["decision"] == fitness.UNMEASURED


@pytest.mark.parametrize("conclusion", fitness.CI_PASSING)
def test_a_check_green_on_b_and_absent_on_c_is_reverted_whatever_green_means(
    conclusion,
):
    obs = observations()
    obs["baseline"]["ci"]["check_runs"].append(
        check_run(B_SHA, "e2e", conclusion, run_id=5)
    )
    result = decide(obs)
    assert result["decision"] == fitness.REVERT
    assert "ci_check_missing" in codes(result, fitness.REVERT)


def _tests(produced_by=fitness.PLATFORM, sha=C_SHA, suites=None):
    return {
        "produced_by": produced_by,
        "sha": sha,
        "suites": {"backend": 4000, "frontend": 900} if suites is None else suites,
    }


@pytest.mark.parametrize(
    "tests",
    [
        _tests(produced_by="candidate"),
        _tests(produced_by=None),
        _tests(sha=B_SHA),
        _tests(sha=None),
        _tests(suites={"backend": -1, "frontend": 900}),
        _tests(suites={"backend": True, "frontend": 900}),
        _tests(suites={"backend": 4000.0, "frontend": 900}),
        _tests(suites={}),
        _tests(suites=[4000]),
        {"backend": 4000, "frontend": 900},
    ],
    ids=[
        "self-reported",
        "no-producer",
        "wrong-sha",
        "no-sha",
        "negative",
        "bool",
        "float",
        "empty",
        "not-an-object",
        "no-envelope",
    ],
)
def test_tests_collected_without_a_valid_envelope_are_refused(tests):
    obs = observations()
    obs["candidate"]["ci"]["tests_collected"] = tests
    result = decide(obs)
    assert result["decision"] == fitness.UNMEASURED
    assert "not_fewer" not in codes(result)


def test_a_baseline_test_count_of_another_build_is_refused():
    obs = observations()
    obs["baseline"]["ci"]["tests_collected"] = _tests(sha=C_SHA)
    assert decide(obs)["decision"] == fitness.UNMEASURED
