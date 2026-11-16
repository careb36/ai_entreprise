"""What the tests of a task's events share (issue #324).

A database of their own, a company, a sprint and a task, the kinds a task's
events have, the check that the projections answer as the comment readers do,
a NATS fake, a signing key and members' bearer headers. The tests are
``test_task_events*.py``.
"""

from __future__ import annotations

import secrets
from uuid import uuid4

import auth_tokens
import history_check
import pytest
import worker_stages
from auth_tokens import create_access_token
from config import settings
from models import BacklogItem, Company, CompanyMember, Sprint, User
from services.learning import scrum_master
from services.tasks import board, task_events, task_projection
from sqlalchemy import text
from sqlmodel import Session

DELIVERABLE = "# Artifact: src/a.py\n\nprint('a')\n" + "word " * 120


class _FakeNATS:
    jetstream_enabled = False

    async def publish(self, subject, payload, msg_id=None):
        return None

    async def request(self, subject, payload, timeout=5.0):
        raise TimeoutError


@pytest.fixture()
def db(monkeypatch, sqlite_session):
    worker_stages.use_engine(monkeypatch, sqlite_session.get_bind())
    # The ScrumMaster's triage answers nothing usable: no decision recorded.
    monkeypatch.setattr(scrum_master, "ask_triage", lambda *a, **k: "")
    yield sqlite_session
    # The database is shared: leave nothing of ours for another test's worker.
    sqlite_session.rollback()
    sqlite_session.exec(
        text(
            "UPDATE sprints SET status = 'cancelled' WHERE company_id IN "
            "(SELECT id FROM companies WHERE company_name LIKE 'Ev %')"
        )
    )
    sqlite_session.commit()


def _company(session: Session, autonomy: int = 2) -> Company:
    company = Company(company_name=f"Ev {uuid4().hex[:6]}", autonomy_level=autonomy)
    session.add(company)
    session.commit()
    session.refresh(company)
    return company


def _sprint(session: Session, company: Company, status: str = "active") -> Sprint:
    sprint = Sprint(
        company_id=company.id, sprint_name="S", scope_size="S", status=status
    )
    session.add(sprint)
    session.commit()
    session.refresh(sprint)
    return sprint


def _item(session: Session, sprint: Sprint, **fields) -> BacklogItem:
    item = BacklogItem(
        sprint_id=sprint.id,
        title=fields.pop("title", f"Task {uuid4().hex[:8]}"),
        assignee=fields.pop("assignee", "Developer"),
        **fields,
    )
    session.add(item)
    session.commit()
    session.refresh(item)
    return item


def _kinds(session: Session, item_id: int) -> list[str]:
    """The task's transitions; the effort observed at sprint close (issue
    #534, ``test_calibration.py``) is not one."""
    return [
        e.event_type
        for e in task_events.for_task(session, item_id)
        if e.event_type != "effort_observed"
    ]


def assert_projection_agrees(session: Session, item_id: int) -> None:
    """The events render the whole thread and answer as the thread does."""
    session.expire_all()
    item = session.get(BacklogItem, item_id)
    events = task_events.for_task(session, item_id)
    assert task_projection.covers(item, events)
    assert task_projection.source(session, item) == "events"
    comments = item.comments
    assert task_projection.pending_rework_from_events(
        events
    ) == board.pending_rework_feedback(comments)
    assert task_projection.block_reason_from_events(events) == board.block_reason(
        comments
    )
    assert task_projection.awaits_risk_acceptance_from_events(
        events
    ) == board.awaits_risk_acceptance(comments)
    assert task_projection.next_step_from_events(
        events
    ) == board.blocked_item_next_step(comments, item.assignee)
    assert task_projection.latest_deliverable_from_events(
        events
    ) == board.extract_latest_reviewable_comment(comments, item.assignee)
    assert task_projection.status_from_events(events) == item.status
    assert task_events.verify_chain(session, item_id)["status"] == "intact"
    assert history_check.check_events(comments, item.status, _as_dicts(events)) == []


def _as_dicts(events):
    return [
        {
            "id": e.id,
            "kind": e.event_type,
            "task_status": e.task_status,
            "payload": e.payload,
        }
        for e in events
    ]


@pytest.fixture()
def signing_key(monkeypatch):
    key = secrets.token_urlsafe(48)
    monkeypatch.setattr(auth_tokens, "_configured_key", lambda: key)
    monkeypatch.setattr(settings, "AUTH_REQUIRED", True)
    return key


def _headers(session, company_id, role):
    user = User(
        email=f"ev-{secrets.token_hex(4)}@example.com",
        full_name="u",
        password_hash="x",
    )
    session.add(user)
    session.commit()
    session.add(CompanyMember(company_id=company_id, user_id=user.id, human_role=role))
    session.commit()
    token = create_access_token(user_id=user.id, email=user.email)
    return {"Authorization": f"Bearer {token}"}
