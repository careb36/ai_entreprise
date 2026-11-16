"""The Verifier's checks read without the API: claimed paths and test claims
extracted from a producer's claims, and the ``tests_run`` observation of a
run the daemon timed out, truncated or failed (the failing tests first, the
end of the output last) (``services.evidence.verification``,
``criteria_tests``).
"""

from services.evidence import criteria_tests
from services.evidence.verification import (
    check_tests_run,
    extract_claimed_paths,
    extract_test_claims,
)


def test_claimed_path_extraction_walks_nested_claims_and_ignores_urls():
    claims = {
        "body": [
            "Changed backend/app/crypto.py and README.md",
            {
                "more": "backend/app/crypto.py; see https://github.com/acme/repo/blob/main/backend/app/secret.py"
            },
        ],
        "bare": "crypto.py",
    }

    assert extract_claimed_paths(claims) == [
        "backend/app/crypto.py",
    ]


def test_claimed_path_extraction_keeps_jsx_and_tsx_extensions_whole():
    claims = {
        "body": "Touched frontend/src/Login.tsx and src/App.jsx, plus src/vite.config.ts."
    }

    assert extract_claimed_paths(claims) == [
        "frontend/src/Login.tsx",
        "src/App.jsx",
        "src/vite.config.ts",
    ]


def test_test_claim_extraction_matches_claims_without_keyword_false_positives():
    claims = {
        "one": "75 passed",
        "nested": ["all tests pass", "CI is green", "passport.py and bypass"],
    }

    assert extract_test_claims(claims) == ["75 passed", "all tests pass", "CI is green"]


# --- a test run the daemon could not finish, or could not fit in the tail -----


def test_timed_out_test_run_refutes_instead_of_leaving_the_claim_unverifiable():
    """A suite that hung was asked to finish and did not: that is a refutation."""
    check = check_tests_run(
        {
            "test_run": {
                "command": "python -m pytest -q",
                "timed_out": True,
                "timeout_seconds": 120,
                "reason": "command timed out after 120s",
            }
        }
    )

    assert check["outcome"] == "refuted"
    assert "did not finish within 120s" in check["observation"]
    assert "timed out" in check["observation"]


def test_timed_out_without_the_limit_still_refutes():
    check = check_tests_run({"test_run": {"command": "npm test", "timed_out": True}})

    assert check["outcome"] == "refuted"
    assert "timed out and was killed" in check["observation"]


def test_truncated_output_is_declared_in_the_observation():
    """A cut log must never read as the whole story."""
    check = check_tests_run(
        {
            "test_run": {
                "command": "python -m pytest -q",
                "exit_code": 1,
                "output_tail": "1 failed",
                "output_bytes": 90000,
                "output_truncated": True,
                "output_limit": 2000,
            }
        }
    )

    assert check["outcome"] == "refuted"
    assert "exited 1" in check["observation"]
    assert "output truncated to the last 2000 bytes of 90000" in check["observation"]


def test_truncation_without_byte_counts_still_says_truncated():
    check = check_tests_run(
        {"test_run": {"command": "npm test", "exit_code": 0, "output_truncated": True}}
    )

    assert check["outcome"] == "pass"
    assert check["observation"].endswith("[output truncated]")


def test_untruncated_pass_carries_no_truncation_note():
    check = check_tests_run(
        {
            "test_run": {
                "command": "npm test",
                "exit_code": 0,
                "output_bytes": 12,
                "output_truncated": False,
            }
        }
    )

    assert check["outcome"] == "pass"
    assert "truncated" not in check["observation"]


def test_daemon_silence_stays_unverifiable():
    """The new refutations must not swallow the genuine 'never ran' case."""
    check = check_tests_run(
        {"test_run": {"command": "npm test", "reason": "the daemon did not reply"}}
    )

    assert check["outcome"] == "unverifiable"
    assert "not run" in check["observation"]


# --- a failed run says which tests failed, before the end of its output ------

_PYTEST_FAILING_WITH_WARNING = """\
tests/test_req_1.py::test_req_1_1_ok PASSED
tests/test_req_1.py::test_req_1_2_empty FAILED
FAILED tests/test_req_1.py::test_req_1_2_empty - AssertionError: ''
1 failed, 1 passed, 1 warning in 0.03s
tests/test_req_1.py:5: DeprecationWarning: asyncio_default_fixture_loop_scope unset
"""


def test_a_failure_leads_with_the_failing_tests_not_the_end_of_the_output():
    """The tail of a failed run is often a warning; the comment a person
    reads first must say which test failed, and for which criterion."""
    evidence = {
        "acceptance": {"all": ["REQ-1.1", "REQ-1.2"], "kind": "acceptance_tests"},
        "test_run": {
            "command": "python -m pytest -v",
            "exit_code": 1,
            "output_tail": _PYTEST_FAILING_WITH_WARNING[-400:],
        },
    }
    criteria_tests.record_results(evidence, _PYTEST_FAILING_WITH_WARNING)

    check = check_tests_run(evidence)

    observation = check["observation"]
    assert check["outcome"] == "refuted"
    assert observation.startswith(
        "`python -m pytest -v` exited 1: failing tests: "
        "tests/test_req_1.py::test_req_1_2_empty"
    )
    assert "criteria: REQ-1.1 pass, REQ-1.2 fail" in observation
    assert "output ends: " in observation
    assert observation.index("test_req_1_2_empty") < observation.index(
        "DeprecationWarning"
    )


def test_more_than_twenty_failing_tests_are_counted_not_all_quoted():
    """A broadly broken suite quotes 20 names and says how many it left out."""
    output = "\n".join(
        f"FAILED tests/test_many.py::test_case_{i:02d} - AssertionError"
        for i in range(25)
    )
    evidence = {
        "acceptance": {"all": []},
        "test_run": {"command": "python -m pytest -v", "exit_code": 1},
    }
    criteria_tests.record_results(evidence, output)

    check = check_tests_run(evidence)

    observation = check["observation"]
    assert check["outcome"] == "refuted"
    assert "failing tests: tests/test_many.py::test_case_00" in observation
    assert "tests/test_many.py::test_case_19" in observation
    assert "tests/test_many.py::test_case_20" not in observation
    assert "(and 5 more)" in observation
    assert evidence["test_run"]["failing_count"] == 25


def test_a_failure_with_no_test_name_falls_back_to_the_output_tail():
    """A run whose output named no test still says how it ended."""
    check = check_tests_run(
        {
            "test_run": {
                "command": "npm test",
                "exit_code": 1,
                "output_tail": "error: Cannot find module './app'\n",
            }
        }
    )

    assert check["outcome"] == "refuted"
    assert check["observation"] == (
        "`npm test` exited 1: error: Cannot find module './app'"
    )
    assert "failing tests" not in check["observation"]
