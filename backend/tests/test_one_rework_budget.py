"""One rework budget per task, QA shown the Verifier's checks, and reworks that
ask for edits (issue #766, ``docs/design/target/05-acceptance.md``).

Sprint 3's task 18 was sent back nine times: the quality gate, the delivery
gate and QA each counted their own two requests. QA never saw the tests'
outcome, and every rework asked for every file in full, so each attempt
rewrote what the branch already held.
"""

from __future__ import annotations

import pytest
from models import BacklogItem
from services.loop.worker import qa_review, review_evidence, review_prompt
from services.tasks import task_events, task_projection
from services.tasks.board import DELIVERY_GATE_AUTHOR
from test_task_event_decisions import (
    DELIVERABLE,
    _company,
    _item,
    _review,
    _sprint,
    _verified_run,
    db,  # noqa: F401 - the fixture
)


def _record(session, item):
    return task_projection.record_of(session, item)


def test_the_budget_counts_every_cause_and_a_person_resets_it(db):  # noqa: F811
    item = _item(db, _sprint(db, _company(db)))
    for actor, author in (
        ("gate", DELIVERY_GATE_AUTHOR),
        ("gate", "Quality gate"),
        ("QA", "QA"),
    ):
        task_events.record(
            db, item, "reviewed", actor, author=author, text="NEEDS WORK: again"
        )
    db.commit()
    db.refresh(item)
    assert _record(db, item).reworks() == 3

    task_events.record(db, item, "sent_back", task_events.PERSON, text="Try again")
    db.commit()
    db.refresh(item)
    assert _record(db, item).reworks() == 0


def test_the_third_cause_s_request_ends_the_budget(db, monkeypatch):  # noqa: F811
    """Task 18's sequence: the gate, then QA, then the gate again, then QA
    says needs work: with three reworks spent the task is blocked."""
    monkeypatch.setattr(qa_review, "MAX_REWORKS", 3)
    company = _company(db)
    item = _item(db, _sprint(db, company))
    for actor, author in (
        ("gate", DELIVERY_GATE_AUTHOR),
        ("QA", "QA"),
        ("gate", DELIVERY_GATE_AUTHOR),
    ):
        task_events.record(
            db, item, "reviewed", actor, author=author, text="NEEDS WORK: tests fail"
        )
    task_events.record(
        db,
        item,
        "delivered",
        "Developer",
        author="Dev",
        text=DELIVERABLE,
        status="in_review",
    )
    db.commit()
    _verified_run(db, company, item)

    _review(monkeypatch, db, item.id, "NEEDS WORK: still failing")

    db.expire_all()
    assert db.get(BacklogItem, item.id).status == "blocked"


def test_max_reworks_is_read_and_the_old_name_still_works_for_a_release(monkeypatch):
    monkeypatch.delenv("MAX_REWORKS", raising=False)
    monkeypatch.delenv("AUTOPILOT_MAX_REWORK_CYCLES", raising=False)
    assert qa_review._max_reworks() == qa_review.DEFAULT_MAX_REWORKS == 3
    monkeypatch.setenv("AUTOPILOT_MAX_REWORK_CYCLES", "5")
    assert qa_review._max_reworks() == 5
    monkeypatch.setenv("MAX_REWORKS", "2")
    assert qa_review._max_reworks() == 2


def test_qa_is_shown_the_checks_and_the_end_of_the_test_output(db):  # noqa: F811
    company = _company(db)
    item = _item(db, _sprint(db, company))
    _verified_run(db, company, item)
    verification = review_evidence.latest(db, company.id, item.id)
    verification.verdict = "refuted"
    verification.checks = [
        {"name": "tests_run", "outcome": "refuted", "observation": "`pytest` exited 1"}
    ]
    verification.evidence = {
        "test_run": {
            "command": "pytest",
            "exit_code": 1,
            "output_tail": "FAILED tests/test_a.py::test_x - assert 1 == 2",
        },
        "command_runs": [
            {
                "kind": "suite",
                "folder": "frontend",
                "command": "npm test",
                "exit_code": 0,
                "output_tail": "12 passed",
            }
        ],
    }
    db.add(verification)
    db.commit()

    text = review_evidence.section(db, company.id, item.id)

    assert review_evidence.HEADER in text and "Verdict: refuted" in text
    assert "- tests_run: refuted — `pytest` exited 1" in text
    assert "FAILED tests/test_a.py::test_x" in text
    assert "Suite in frontend/ `npm test` exited 0" in text and "12 passed" in text
    assert "not settled by your review" in text


def test_the_review_prompt_carries_the_observations(db, monkeypatch):  # noqa: F811
    company = _company(db)
    sprint = _sprint(db, company)
    item = _item(db, sprint)
    _verified_run(db, company, item)
    prompt = review_prompt.build(
        db, item, sprint, {"name": "QA", "role": "QA"}, DELIVERABLE, 0
    )
    assert review_evidence.HEADER in prompt
    assert "- files_written: pass" in prompt
    assert prompt.index(review_evidence.HEADER) < prompt.index("APPROVED:")


def test_no_verification_no_section(db):  # noqa: F811
    company = _company(db)
    item = _item(db, _sprint(db, company))
    assert review_evidence.section(db, company.id, item.id) == ""


@pytest.mark.parametrize("module", ["quality_gate", "review", "qa_review"])
def test_rework_instructions_ask_for_edits_not_whole_files(module):
    import importlib
    import inspect

    source = inspect.getsource(
        importlib.import_module(f"services.loop.worker.{module}")
    )
    assert "every file in full" not in source
    assert "changed file in full" not in source
    assert "change only what this problem needs" in qa_review.EDIT_INSTRUCTION
