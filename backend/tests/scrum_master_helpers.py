"""What the tests of the ScrumMaster's triage share.

An escalation's text and a usable answer, a company whose ScrumMaster is
asked, a task blocked for triage, and a decision recorded for it. The tests
are ``test_scrum_master*.py``.
"""

import secrets

import pytest
from models import BacklogItem, Company, Sprint
from services.learning import scrum_master

TRIAGE = (
    "Pre-review quality gate failed after 2 rework(s) — blocked for manual triage: "
    "Too short (41 words, minimum 180)."
)
RETRY_ANSWER = (
    "DECISION: retry\n"
    "BRIEF: Write the full strategy document (at least 180 words) with sections for "
    "unit, integration and end-to-end tests, as a path= block.\n"
    "WHY: the attempts were cut short, not wrong.\n"
    "END"
)


@pytest.fixture()
def company(sqlite_session):
    company = Company(company_name=f"SM {secrets.token_hex(3)}", autonomy_level=3)
    sqlite_session.add(company)
    sqlite_session.commit()
    sqlite_session.refresh(company)
    sprint = Sprint(company_id=company.id, sprint_name="Sprint 0", status="active")
    sqlite_session.add(sprint)
    sqlite_session.commit()
    sqlite_session.refresh(sprint)
    return company, sprint


def _blocked(session, sprint, comments=None, **fields):
    item = BacklogItem(
        sprint_id=sprint.id,
        title=fields.pop("title", "Create testing strategy document"),
        description="Define the testing pyramid.",
        priority="high",
        assignee="QA",
        status=fields.pop("status", "blocked"),
        comments=comments
        or [
            {"author": "QA Quinn", "text": "NEEDS WORK: cover integration tests too"},
            {"author": "Quality gate", "text": "NEEDS WORK: Too short (12 words)."},
            {"author": "Quality gate", "text": TRIAGE},
        ],
        **fields,
    )
    session.add(item)
    session.commit()
    session.refresh(item)
    return item


def _decide(monkeypatch, session, company, sprint, item, answer):
    monkeypatch.setattr(scrum_master, "ask_triage", lambda *a: answer)
    scrum_master.triage(session, company.id, sprint.id, item, TRIAGE)
    session.commit()
