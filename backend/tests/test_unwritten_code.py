"""QA's extra tests that fail only because the code is not written yet are
excused in its acceptance-test task (``services/evidence/unwritten_code.py``).

The output is the bench's of 2026-11-22, spec 05, run 1 (issue #774): QA's
task was refuted for ``test_empty_text_returns_empty_dict``, which names no
criterion and failed like the others, on ``No module named 'confcheck'``."""

import pytest
from services.evidence import criteria_tests, unwritten_code
from services.evidence.verification import check_tests_run

FILE = "tests/test_req_1_settings_file.py"
MISSING = "ModuleNotFoundError: No module named 'confcheck'"
OUTPUT = f"""
{FILE}::test_req_1_1_parses_sections_keys_and_values FAILED [ 50%]
{FILE}::test_empty_text_returns_empty_dict FAILED [100%]
=========================== short test summary info ============================
FAILED {FILE}::test_req_1_1_parses_sections_keys_and_values - {MISSING}
FAILED {FILE}::test_empty_text_returns_empty_dict - {MISSING}
============================== 2 failed in 0.05s ==============================
"""
COVERS = ["REQ-1.1", "REQ-1.2", "REQ-1.3", "REQ-1.4"]


def _evidence(output=OUTPUT, written=(FILE,), kind="acceptance_tests", excused=None):
    evidence = {
        "workspace_writes": [{"path": p, "status": "written"} for p in written],
        "acceptance": {
            "kind": kind,
            "all": COVERS + ["REQ-2.1"],
            "covers": COVERS,
            "excused": COVERS + ["REQ-2.1"] if excused is None else excused,
        },
        "test_run": {"command": "pytest", "exit_code": 1},
    }
    criteria_tests.record_results(evidence, output)
    return evidence


def test_the_runners_reasons_are_recorded_from_its_summary():
    reasons = _evidence()["test_run"]["failure_reasons"]
    assert reasons == {
        f"{FILE}::test_req_1_1_parses_sections_keys_and_values": MISSING,
        f"{FILE}::test_empty_text_returns_empty_dict": MISSING,
    }


def test_run_1_of_spec_05_is_not_refuted_for_its_extra_test():
    check = check_tests_run(_evidence())

    assert check["outcome"] == "pass"
    assert "code they import is not written yet" in check["observation"]
    assert "test_empty_text_returns_empty_dict" in check["observation"]
    assert "REQ-1.1" in check["observation"]


def test_cannot_import_a_name_is_code_not_written_either():
    output = OUTPUT.replace(
        MISSING, "ImportError: cannot import name 'parse' from 'confcheck'"
    )
    assert check_tests_run(_evidence(output))["outcome"] == "pass"


@pytest.mark.parametrize(
    "evidence",
    [
        # A real failure: the code exists and the assertion fails.
        _evidence(
            OUTPUT.replace(
                f"test_empty_text_returns_empty_dict - {MISSING}",
                "test_empty_text_returns_empty_dict - assert {'a': 1} == {}",
            )
        ),
        # Not a file this delivery wrote.
        _evidence(written=("tests/other.py",)),
        # The implementation task: its tests are judged with no such excuse.
        _evidence(kind=None),
        # A criterion it covers is implemented already: the code exists.
        _evidence(excused=["REQ-1.2", "REQ-1.3", "REQ-1.4", "REQ-2.1"]),
        # The runner gave no reason for the extra test.
        _evidence(
            OUTPUT.replace(
                f"FAILED {FILE}::test_empty_text_returns_empty_dict - {MISSING}\n", ""
            )
        ),
    ],
)
def test_what_is_still_refuted(evidence):
    assert unwritten_code.explain(evidence)[0] is False
    assert check_tests_run(evidence)["outcome"] == "refuted"


def test_a_cut_list_of_failures_excuses_nothing():
    evidence = _evidence()
    evidence["test_run"]["failing_count"] = 80
    assert check_tests_run(evidence)["outcome"] == "refuted"
