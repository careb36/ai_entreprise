"""A task's description is never cut short of what its agent must read
(``services/contracts/task_text.py``), and QA is never told to fix the code
(``loop/worker/review._code_instruction``). Both from the bench of 2026-11-22,
spec 05 (issue #774)."""

import pytest
from services.contracts.task_text import DESCRIPTION_MAX, clip_description
from services.evidence import acceptance, delivery_gate
from services.loop import task_edit
from services.loop.worker import review

# Spec 05's REQ-1: four criteria, whose names pushed the template past 500.
REQ_1 = {
    "id": "REQ-1",
    "title": "Parse a settings file",
    "criteria": [{"id": f"REQ-1.{n}"} for n in range(1, 5)],
}


def test_qa_reads_its_whole_task_up_to_do_not_implement():
    description = acceptance.test_task(REQ_1)["description"]

    assert len(description) > 500
    assert description.endswith("do not implement the feature here.")
    assert "REQ-1.4 → test_req_1_4_<what it checks>" in description


def test_one_limit_holds_for_planners_and_a_managers_edit():
    assert task_edit.DESCRIPTION_MAX == DESCRIPTION_MAX == 10_000
    assert clip_description("x" * 800) == "x" * 800
    assert len(clip_description("x" * 20_000)) == DESCRIPTION_MAX
    assert clip_description(None) == ""


@pytest.mark.parametrize("problem", sorted(delivery_gate.CODE_PROBLEMS))
def test_qa_fixes_its_tests_and_a_developer_fixes_the_code(problem):
    qa_task = {"kind": acceptance.ACCEPTANCE_KIND}

    assert review._code_instruction(qa_task, problem) == review.TESTS_FIRST_INSTRUCTION
    assert "Fix the code" not in review._code_instruction(qa_task, problem)
    assert review._code_instruction({"kind": None}, problem).startswith("Fix the code")


def test_a_problem_that_is_not_the_codes_asks_nothing_of_either():
    assert (
        review._code_instruction({"kind": acceptance.ACCEPTANCE_KIND}, "not written")
        == ""
    )
