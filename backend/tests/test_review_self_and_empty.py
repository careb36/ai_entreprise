"""
Two holes a sprint showed (#736's follow-up, task 17): a task QA wrote was
reviewed by QA, and an approval of a delivery that wrote no file closed the
task with nothing the Verifier could check.

- The reviewer of a task assigned to QA is the company's Architect; a
  company with no Architect keeps QA (``review._reviewer_role``).
- An approved delivery that wrote no file waits for a person
  (``review_hold.person_hold``); one that declared a file closes as before.
"""

from uuid import uuid4

from models import Agent, BacklogItem, Company, Sprint
from services.loop.worker import review as worker_review
from services.loop.worker import review_hold
from test_review_gate import WITH_FILE as DELIVERABLE
from test_review_gate import _run_cycle, _state, db  # noqa: F401

NO_FILE = "I could not write the script: the e2e harness is not in my context. " * 4


def _sprint(session, *roles: str) -> Sprint:
    company = Company(company_name=f"Review {uuid4().hex[:8]}", autonomy_level=2)
    session.add(company)
    session.commit()
    session.refresh(company)
    for role in roles:
        session.add(Agent(company_id=company.id, name=f"{role} one", role=role))
    sprint = Sprint(company_id=company.id, sprint_name="S", scope_size="S")
    session.add(sprint)
    session.commit()
    session.refresh(sprint)
    return sprint


def _item(session, sprint: Sprint, assignee: str, text: str) -> BacklogItem:
    item = BacklogItem(
        sprint_id=sprint.id,
        title=f"Task {uuid4().hex[:8]}",
        status="in_review",
        assignee=assignee,
        comments=[{"author": assignee, "text": text, "created_at": ""}],
    )
    session.add(item)
    session.commit()
    session.refresh(item)
    return item


def test_a_task_qa_wrote_is_reviewed_by_the_architect(db):  # noqa: F811
    sprint = _sprint(db, "QA", "Architect")
    item = _item(db, sprint, "QA", DELIVERABLE)

    reviewer = worker_review._reviewer_of(db, sprint, item)

    assert (reviewer["role"], reviewer["name"]) == ("Architect", "Architect one")


def test_any_other_task_is_reviewed_by_qa(db):  # noqa: F811
    sprint = _sprint(db, "QA", "Architect")
    item = _item(db, sprint, "Developer", DELIVERABLE)

    assert worker_review._reviewer_of(db, sprint, item)["role"] == "QA"


def test_without_an_architect_qa_still_reviews(db):  # noqa: F811
    sprint = _sprint(db, "QA")
    item = _item(db, sprint, "QA", DELIVERABLE)

    assert worker_review._reviewer_of(db, sprint, item)["role"] == "QA"


def test_an_approved_delivery_that_wrote_no_file_waits_for_a_person(db, monkeypatch):  # noqa: F811
    sprint = _sprint(db, "QA")
    item = _item(db, sprint, "Developer", NO_FILE)

    calls = _run_cycle(monkeypatch, "APPROVED: honest escalation", db, item.id)

    status, comments = _state(db, item.id)
    assert len(calls) == 1
    assert status == "blocked"
    assert any(review_hold.NO_FILES_REASON in text for _author, text in comments)


def test_an_approved_delivery_with_a_file_closes(db, monkeypatch):  # noqa: F811
    sprint = _sprint(db, "QA")
    item = _item(db, sprint, "Developer", DELIVERABLE)

    _run_cycle(monkeypatch, "APPROVED: complete", db, item.id)

    assert _state(db, item.id)[0] == "done"
