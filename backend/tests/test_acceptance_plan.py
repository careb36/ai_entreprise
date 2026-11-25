"""Acceptance tests per criterion without the daemon
(``services.evidence.acceptance``, ``criteria_tests``, ``spec_planning``):
criterion ids as test-name tokens and the runner's per-test lines read back
into them, the plan putting QA's test-writing tasks first within its scope,
and the independent history check refusing a criterion verified without an
observed test run. The shared rows are in ``acceptance_helpers``.
"""

import textwrap

import history_check
from acceptance_helpers import SPEC
from models.agent_run import Verification
from services.evidence import acceptance, criteria_tests

# --- reading the runner's output ---------------------------------------------


def test_criterion_ids_become_test_name_tokens_and_are_found_as_whole_words():
    assert criteria_tests.token("REQ-1.2") == "req_1_2"
    assert criteria_tests.names_criterion(
        "tests/test_a.py::test_req_1_2_lists", "REQ-1.2"
    )
    assert criteria_tests.names_criterion("REQ-1.2 lists the tasks", "REQ-1.2")
    # ``req_1_2`` is not ``req_1_21``, nor ``req_11_2``.
    assert not criteria_tests.names_criterion("test_req_1_21_x", "REQ-1.2")
    assert not criteria_tests.names_criterion("test_req_11_2_x", "REQ-1.2")


PYTEST_OUTPUT = textwrap.dedent(
    """
    tests/test_req_1.py::test_req_1_1_name PASSED                      [ 33%]
    tests/test_req_1.py::test_req_1_2_empty FAILED                     [ 66%]
    tests/test_other.py::test_helper PASSED                            [100%]
    =================================== FAILURES ===================================
    FAILED tests/test_req_1.py::test_req_1_2_empty - AssertionError
    ========================= 1 failed, 2 passed in 0.02s ==========================
    """
)


JEST_OUTPUT = "  ✓ REQ-1.1 greets by name (3 ms)\n  ✕ REQ-1.2 greets the world (1 ms)\n"


def test_per_test_lines_of_pytest_jest_go_and_cargo_are_read():
    assert criteria_tests.results_from_output(
        PYTEST_OUTPUT, ["REQ-1.1", "REQ-1.2", "REQ-2.1"]
    ) == {
        "REQ-1.1": "pass",
        "REQ-1.2": "fail",
        "REQ-2.1": "missing",
    }
    assert criteria_tests.failing_tests(PYTEST_OUTPUT) == [
        "tests/test_req_1.py::test_req_1_2_empty"
    ]
    assert criteria_tests.results_from_output(JEST_OUTPUT, ["REQ-1.1", "REQ-1.2"]) == {
        "REQ-1.1": "pass",
        "REQ-1.2": "fail",
    }
    assert criteria_tests.results_from_output(
        "--- PASS: TestReq_1_1 (0.00s)\n--- FAIL: TestReq_1_2 (0.00s)\n",
        ["REQ-1.1", "REQ-1.2"],
    ) == {"REQ-1.1": "pass", "REQ-1.2": "fail"}
    assert criteria_tests.results_from_output(
        "test req_1_1_name ... ok\ntest req_1_2_empty ... FAILED\n",
        ["REQ-1.1", "REQ-1.2"],
    ) == {"REQ-1.1": "pass", "REQ-1.2": "fail"}
    # Two tests of one criterion: it passes only when both do.
    assert criteria_tests.results_from_output(
        "a.py::test_req_1_1_a PASSED\na.py::test_req_1_1_b FAILED\n", ["REQ-1.1"]
    ) == {"REQ-1.1": "fail"}
    # ANSI colour is ignored; a summary line names no test.
    assert criteria_tests.results_from_output(
        "\x1b[32ma.py::test_req_1_1_a PASSED\x1b[0m\n2 passed in 0.1s\n", ["REQ-1.1"]
    ) == {"REQ-1.1": "pass"}


def test_pytest_gets_per_test_output_and_other_runners_are_left_alone():
    assert (
        criteria_tests.verbose_command("python -m pytest -q") == "python -m pytest -v"
    )
    assert criteria_tests.verbose_command("pytest -v tests") == "pytest -v tests"
    assert criteria_tests.verbose_command(
        "python -m pytest -q -p no:cacheprovider"
    ) == ("python -m pytest -p no:cacheprovider -v")
    assert criteria_tests.verbose_command("npm test --silent") == "npm test --silent"


def test_the_plan_puts_a_test_task_per_requirement_first_and_repoints_dependencies():
    tasks = [
        {"title": "Store", "covers": ["REQ-1.1"], "depends_on": []},
        {"title": "Endpoint", "covers": ["REQ-1.2"], "depends_on": [0]},
        {"title": "Board", "covers": ["REQ-2.1", "REQ-1.1"], "depends_on": [1]},
    ]
    planned = acceptance.with_test_tasks(tasks, SPEC, item_limit=8)
    assert [t["title"] for t in planned] == [
        "Acceptance tests for REQ-1: List the tasks",
        "Acceptance tests for REQ-2: Move a task",
        "Store",
        "Endpoint",
        "Board",
    ]
    assert [t["kind"] for t in planned] == [
        "acceptance_tests",
        "acceptance_tests",
        None,
        None,
        None,
    ]
    assert planned[0]["assignee"] == "QA" and planned[0]["covers"] == [
        "REQ-1.1",
        "REQ-1.2",
    ]
    assert "test_req_1_1_<what it checks>" in planned[0]["description"]
    assert [t["depends_on"] for t in planned] == [[], [], [0], [0, 2], [1, 0, 3]]
    # Already planned with its tests: left as it is.
    assert acceptance.with_test_tasks(planned, SPEC, 8) == planned


def test_the_plan_stays_within_the_scope_dropping_tasks_from_the_end():
    tasks = [
        {"title": "Store", "covers": ["REQ-1.1"], "depends_on": []},
        {"title": "Board", "covers": ["REQ-2.1"], "depends_on": [0]},
    ]
    planned = acceptance.with_test_tasks(tasks, SPEC, item_limit=3)
    assert [t["title"] for t in planned] == [
        "Acceptance tests for REQ-1: List the tasks",
        "Store",
    ]
    # A task without criteria needs no test task and no budget for one.
    assert [
        t["title"] for t in acceptance.with_test_tasks([{"title": "Docs"}], SPEC, 1)
    ] == ["Docs"]


def test_a_qa_task_writing_tests_the_test_tasks_write_is_left_out():
    # Phase D's bench (issue #766): the PO planned QA test tasks over the
    # criteria its acceptance tests already covered.
    tasks = [
        {"title": "Store", "assignee": "Developer", "covers": ["REQ-1.1", "REQ-1.2"]},
        {"title": "Unit tests for store", "assignee": "QA", "covers": ["REQ-1.1"]},
        {
            "title": "Board",
            "assignee": "Developer",
            "covers": ["REQ-2.1"],
            "depends_on": [1],
        },
        {"title": "Performance check", "assignee": "QA", "covers": []},
        {"title": "Tests for the board", "assignee": "qa", "covers": ["REQ-2.1"]},
    ]
    planned = acceptance.with_test_tasks(tasks, SPEC, item_limit=10)
    titles = [t["title"] for t in planned]
    assert "Unit tests for store" not in titles
    assert "Tests for the board" not in titles
    # A QA task without criteria stays.
    assert "Performance check" in titles
    # Board waited for the duplicate: it waits for REQ-1's test task instead.
    board = planned[titles.index("Board")]
    req1_tests = titles.index("Acceptance tests for REQ-1: List the tasks")
    assert req1_tests in board["depends_on"]
    # QA alone covering a requirement: nothing implements it, so its test
    # task is not planned for it and the QA task stays.
    alone = acceptance.with_test_tasks(
        [{"title": "QA only", "assignee": "QA", "covers": ["REQ-1.1"]}], SPEC, 10
    )
    assert "QA only" in [t["title"] for t in alone]


# --- the independent check ---------------------------------------------------


def test_a_criterion_verified_without_an_observed_run_is_illegal():
    task = {"id": 1, "status": "done", "comments": []}
    legal = [
        {
            "version": 1,
            "status": "verified",
            "verified_criteria": ["REQ-1.1"],
            "observed_test_run": True,
        }
    ]
    illegal = [
        {
            "version": 1,
            "status": "verified",
            "verified_criteria": ["REQ-1.1"],
            "observed_test_run": False,
        }
    ]
    assert not [
        v
        for v in history_check.check_task(task, legal, [])
        if v.startswith("criteria_verified")
    ]
    assert history_check.check_task(task, illegal, []) == [
        "done_without_approval",
        "criteria_verified_without_run: run v1 marks REQ-1.1 as passed with no test run observed",
    ]
    facts = history_check._run_facts(
        Verification(
            company_id=1,
            agent_run_id=1,
            verdict="verified",
            checks=[
                {
                    "name": "criteria_tests",
                    "results": {"REQ-1.1": "pass", "REQ-1.2": "fail"},
                }
            ],
            evidence={"test_run": {"exit_code": 1}},
        )
    )
    assert facts == {
        "verified_criteria": ["REQ-1.1"],
        "observed_test_run": True,
        "reads_without_hash": [],
    }
    assert history_check._run_facts(None) == {
        "verified_criteria": [],
        "observed_test_run": False,
        "reads_without_hash": [],
    }
    # An exit status that travelled as text still counts; a key that is not
    # text is read as text.
    odd = history_check._run_facts(
        Verification(
            company_id=1,
            agent_run_id=1,
            verdict="verified",
            checks=[{"name": "criteria_tests", "results": {7: "pass"}}],
            evidence={"test_run": {"exit_code": "0"}},
        )
    )
    assert odd == {
        "verified_criteria": ["7"],
        "observed_test_run": True,
        "reads_without_hash": [],
    }
    assert (
        history_check._run_facts(
            Verification(
                company_id=1,
                agent_run_id=1,
                verdict="verified",
                checks=[],
                evidence={"test_run": {"exit_code": "x"}},
            )
        )["observed_test_run"]
        is False
    )


def test_pytest_xdist_verbose_lines_are_read():
    """The backend's suite runs under pytest-xdist by default (#592): its
    ``-v`` lines put the outcome before the test, behind the worker. Read
    as the short summary, or every backend criterion was ``missing``
    (self-hosting run 7, task 49, whose test passed)."""
    output = (
        "tests/test_saved_views_acceptance.py::test_req_2_1_done_tasks_agree \n"
        "[gw5] [ 45%] PASSED tests/test_saved_views_acceptance.py::test_req_2_1_done_tasks_agree \n"
        "[gw12] [100%] FAILED tests/test_req_1.py::test_req_1_2_and_group \n"
        "========== 1 failed, 7568 passed in 216.42s ==========\n"
    )
    assert criteria_tests.results_from_output(
        output, ["REQ-1.2", "REQ-2.1", "REQ-3.1"]
    ) == {
        "REQ-1.2": "fail",
        "REQ-2.1": "pass",
        "REQ-3.1": "missing",
    }
    assert criteria_tests.failing_tests(output) == [
        "tests/test_req_1.py::test_req_1_2_and_group"
    ]
