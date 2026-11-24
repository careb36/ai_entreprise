"""A test command that found no tests is configuration, not failed code
(``services/evidence/test_collection.py``).

The self-hosting owner action's run 2 (2026-11-24, #701: company 3, sprint
9) ran ``python -m pytest -p no:cacheprovider tests -v`` at the root while the
plan's tests were in ``backend/tests``. Every delivery's reply was pytest's
exit 4, ``file or directory not found: tests``; ``tests_run`` refuted it as
"tests failed" and the gate asked for code reworks. The reply below is that
verification's (#117) ``test_run``, as the daemon reported it.
"""

import pytest
from services.evidence import command_runs, delivery_gate, test_collection
from services.evidence.verification import check_tests_run

RUN2_OUTPUT = (
    "============================= test session starts ==============================\n"
    "platform linux -- Python 3.12.16, pytest-8.3.5, pluggy-1.7.0 -- /usr/local/bin/python\n"
    "rootdir: /workspace\n"
    "plugins: platformdirs-4.14.8, anyio-4.16.0\n"
    "collecting ... collected 0 items\n\n"
    "============================ no tests ran in 0.01s =============================\n"
    "ERROR: file or directory not found: tests\n\n"
)
RUN2_TEST_RUN = {
    "command": "python -m pytest -p no:cacheprovider tests -v",
    "exit_code": 4,
    "output_tail": RUN2_OUTPUT,
    "timed_out": False,
    "reason": None,
}
NO_TESTS_COLLECTED = (
    "============================= test session starts ==============================\n"
    "collected 0 items\n\n"
    "============================ no tests ran in 0.02s =============================\n"
)


def test_run2s_reply_is_unverifiable_with_the_path_not_refuted():
    check = check_tests_run({"test_run": dict(RUN2_TEST_RUN)})
    assert check["outcome"] == "unverifiable"
    assert check["cause"] == test_collection.CAUSE
    assert check["observation"].startswith(
        "the test command found no tests at tests (file or directory not found)"
    )
    assert "exited 4" in check["observation"]


def test_pytest_exit_5_names_the_paths_of_the_command():
    run = {
        "command": "python -m pytest -q -p no:cacheprovider tests/unit tests/api",
        "exit_code": 5,
        "output_tail": NO_TESTS_COLLECTED,
    }
    assert test_collection.no_tests(run) == (
        "the test command found no tests at tests/unit, tests/api (pytest collected 0 items)"
    )
    check = check_tests_run({"test_run": run})
    assert (check["outcome"], check["cause"]) == ("unverifiable", "no_tests")


def test_a_command_without_paths_names_the_folder_it_ran_in():
    run = {"command": "pytest -q", "exit_code": 5, "output_tail": NO_TESTS_COLLECTED}
    assert "at the root (the folder it ran in)" in test_collection.no_tests(run)


def test_a_usage_error_is_configuration_too():
    run = {
        "command": "python -m pytest --bogus tests",
        "exit_code": 4,
        "output_tail": "ERROR: usage: __main__.py [options] [file_or_dir]\n"
        "__main__.py: error: unrecognized arguments: --bogus\n",
    }
    assert "usage error" in test_collection.no_tests(run)


def test_a_conftest_that_does_not_import_is_still_the_codes():
    # pytest also exits 4 when conftest.py fails to import: the delivery's code.
    run = {
        "command": "python -m pytest -q tests",
        "exit_code": 4,
        "output_tail": "ImportError while loading conftest '/workspace/tests/conftest.py'.\n"
        "tests/conftest.py:1: in <module>\n    import app\nE   ModuleNotFoundError: No module named 'app'\n",
    }
    assert test_collection.no_tests(run) is None
    assert check_tests_run({"test_run": run})["outcome"] == "refuted"


@pytest.mark.parametrize(
    "command,output",
    [
        (
            "npx jest",
            "No tests found, exiting with code 1\nRun with `--passWithNoTests` to exit with code 0",
        ),
        ("npx vitest run", "No test files found, exiting with code 1\n"),
    ],
)
def test_jest_and_vitest_saying_no_tests_are_configuration(command, output):
    check = check_tests_run(
        {"test_run": {"command": command, "exit_code": 1, "output_tail": output}}
    )
    assert (check["outcome"], check.get("cause")) == ("unverifiable", "no_tests")


@pytest.mark.parametrize(
    "run",
    [
        {
            "command": "python -m pytest -q tests",
            "exit_code": 1,
            "output_tail": "1 failed, 2 passed",
        },
        {
            "command": "python -m pytest -q tests",
            "exit_code": 0,
            "output_tail": "3 passed",
        },
        {
            "command": "make test",
            "exit_code": 5,
            "output_tail": "make: *** [test] Error 5",
        },
    ],
)
def test_failed_passed_or_other_runners_are_judged_as_before(run):
    assert test_collection.no_tests(run) is None
    assert check_tests_run({"test_run": run}).get("cause") is None


def test_a_folder_suite_that_found_no_tests_is_named_with_its_folder():
    suite = {
        "kind": "suite",
        "folder": "backend",
        "command": "PYTHONPATH=.:app poetry run python -m pytest tests -q -p no:cacheprovider",
        "exit_code": 5,
        "output_tail": NO_TESTS_COLLECTED,
    }
    outcome, text = command_runs.outcome_of(suite, {})
    assert outcome == "unverifiable"
    assert "the test command found no tests at backend/tests" in text
    # The folders' suites read together (delivery_suites.summarise): no exit status.
    evidence = {
        "test_run": {
            "summary_of": "command_runs",
            "command": suite["command"],
            "exit_code": None,
        },
        "command_runs": [suite],
    }
    check = check_tests_run(evidence)
    assert (check["outcome"], check["cause"]) == ("unverifiable", "no_tests")
    assert "backend/tests" in check["observation"]


def test_a_failed_suite_beside_wins_over_one_that_found_nothing():
    evidence = {
        "test_run": {
            "summary_of": "command_runs",
            "command": "x",
            "exit_code": 1,
            "output_tail": "1 failed",
        },
        "command_runs": [
            {"kind": "suite", "folder": "a", "command": "pytest", "exit_code": 5}
        ],
    }
    assert check_tests_run(evidence)["outcome"] == "refuted"


class _Verification:
    def __init__(self, checks):
        self.checks = checks
        self.evidence = {}


def _gate(monkeypatch, checks):
    monkeypatch.setattr(
        delivery_gate, "latest_verification", lambda *a: _Verification(checks)
    )
    return delivery_gate.gate(None, 1, 1)


def test_the_gate_names_a_platform_problem_not_a_code_rework(monkeypatch):
    found = check_tests_run({"test_run": dict(RUN2_TEST_RUN)})
    # Run 2's task 40 also had the acceptance test "edited"; no test could run.
    edited = {
        "name": "acceptance_tests_untouched",
        "outcome": "refuted",
        "observation": "edits x",
    }
    problem, observation = _gate(monkeypatch, [edited, found])
    assert problem == delivery_gate.TESTS_NOT_FOUND
    assert problem in delivery_gate.PLATFORM_PROBLEMS
    assert problem not in delivery_gate.CODE_PROBLEMS
    assert "found no tests at tests" in observation


def test_an_unverifiable_test_run_for_another_reason_still_goes_to_qa(monkeypatch):
    not_run = check_tests_run(
        {"test_run": {"command": "pytest", "reason": "no test command"}}
    )
    assert not_run["outcome"] == "unverifiable" and "cause" not in not_run
    assert _gate(monkeypatch, [not_run]) is None
