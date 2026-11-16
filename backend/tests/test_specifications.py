"""The specification, checked deterministically: numbered requirements with
testable criteria, what blocks approval and what only warns, the PO's draft
read from the fixed shape and the prompts built from it
(``services.planning.spec_check``, ``spec_author``). The API is in
``test_specifications_api.py``, the planning from a specification in
``test_specifications_planning.py``; the shared rows in
``specification_helpers``.
"""

import json

import pytest
from services.planning import spec_author, spec_check, spec_planning
from specification_helpers import GOOD

# --- the check ---------------------------------------------------------------


def test_normalize_numbers_requirements_criteria_and_quality_in_order():
    spec = spec_check.normalize(
        {
            **GOOD,
            "requirements": [
                {"id": "REQ-9", **GOOD["requirements"][0]},
                GOOD["requirements"][1],
            ],
        }
    )
    assert [r["id"] for r in spec["requirements"]] == ["REQ-1", "REQ-2"]
    assert [c["id"] for c in spec["requirements"][0]["criteria"]] == [
        "REQ-1.1",
        "REQ-1.2",
    ]
    assert spec["quality"][0]["id"] == "NFR-1"
    assert spec_check.criterion_ids(spec) == ["REQ-1.1", "REQ-1.2", "REQ-2.1"]
    result = spec_check.check(spec)
    # Nothing blocks; the standards (STD-PO-1) warn on the quality areas the
    # specification says nothing about (it names performance only).
    assert (result["ok"], result["blocking"], result["warnings"]) == (True, 0, 3)
    assert [f["message"] for f in result["findings"]] == [
        f"No quality attribute says what is expected of {area}."
        for area in ("security", "reliability", "accessibility")
    ]
    assert {f["code"] for f in result["findings"]} == {"quality_area_missing"}


def test_the_check_blocks_what_cannot_be_tested_and_warns_about_promises():
    spec = spec_check.normalize(
        {
            "title": "Board",
            "requirements": [
                {
                    "title": "Be fast",
                    "description": "It must be fast and easy.",
                    "criteria": [],
                },
                {
                    "title": "Sort",
                    "description": "Columns sort by priority, highest first.",
                    "criteria": [
                        {"given": "", "when": "sorted", "then": "it is intuitive"}
                    ],
                },
            ],
            "quality": [{"attribute": "availability", "target": "always up"}],
            "questions": [
                "Which columns?",
                {"question": "Who signs in?", "answer": "the company's members"},
            ],
        }
    )
    result = spec_check.check(spec)
    codes = {(f["where"], f["code"], f["severity"]) for f in result["findings"]}
    assert ("REQ-1", "no_criteria", "blocking") in codes
    assert ("REQ-1", "vague", "warning") in codes
    assert ("REQ-2.1", "empty_step", "blocking") in codes
    assert ("NFR-1", "no_number", "blocking") in codes
    assert ("Q1", "open_question", "blocking") in codes
    assert ("Q2", "open_question", "blocking") not in codes
    assert result["ok"] is False and result["blocking"] == 4
    # A number beside the word is a measure, not a promise.
    assert spec_check.check(
        spec_check.normalize(
            {
                **GOOD,
                "requirements": [
                    {
                        **GOOD["requirements"][0],
                        "description": "The board is fast: it renders 500 tasks in under 300 ms.",
                    }
                ],
            }
        )
    )["ok"]


def test_the_prompt_section_carries_only_the_covered_criteria():
    spec = spec_check.normalize(GOOD)
    section = spec_planning.requirements_section(spec, ["REQ-2.1"])
    assert "## Requirements this task covers" in section
    assert (
        "REQ-2: Move a task" in section and "REQ-2.1: given a task in todo" in section
    )
    assert "REQ-1" not in section
    assert "NFR-1: performance" in section and "TypeScript and React" in section
    # A task covering no criterion still gets what holds for every task.
    rules = spec_planning.requirements_section(spec, [])
    assert "## Specification rules for every task" in rules
    assert "NFR-1: performance" in rules and "REQ-" not in rules


# --- the PO's draft ----------------------------------------------------------


def test_the_po_draft_is_read_from_the_fixed_shape_and_nothing_else():
    text = (
        "```json\n"
        + json.dumps({**GOOD, "questions": ["Which columns does the board have?"]})
        + "\n```"
    )
    spec = spec_author.parse_draft(text)
    assert spec["requirements"][0]["id"] == "REQ-1"
    assert spec["questions"] == [
        {"question": "Which columns does the board have?", "answer": None}
    ]
    assert spec_author.parse_draft("Sure! Here is a plan: build a board.") is None
    assert (
        spec_author.parse_draft(json.dumps({"title": "x", "requirements": []})) is None
    )


def test_a_revision_prompt_carries_the_answers_and_the_stack_stored_as_text():
    existing = {
        **spec_check.normalize(GOOD),
        "questions": [
            {"question": "Which columns?", "answer": "todo, doing, done"},
            {"question": "Open?", "answer": None},
        ],
    }
    prompt = spec_author.draft_prompt(
        "Pia", "a board", json.dumps({"language": "TypeScript"}), existing
    )
    assert "Primary language: TypeScript" in prompt
    assert "Q: Which columns?\n  A: todo, doing, done" in prompt
    assert "Q: Open?" not in prompt
    assert spec_author.merge_questions(
        existing["questions"],
        [
            {"question": "which columns?", "answer": None},
            {"question": "New one?", "answer": None},
        ],
    ) == [
        {"question": "Which columns?", "answer": "todo, doing, done"},
        {"question": "New one?", "answer": None},
    ]


@pytest.mark.parametrize("word", ["fast", "rápido", "etc", "user-friendly"])
def test_vague_words_are_found_whole_and_in_both_languages(word):
    assert spec_check._vague(f"it must be {word} for everyone") == word
    assert spec_check._vague("fastened in 2 steps") is None
