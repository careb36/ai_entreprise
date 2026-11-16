"""What the tests of a card a person moves on the board share: the database
fixture, a company, a sprint and a blocked task with its triage comments, the
history each path writes (checked by ``history_check``) and the move itself.
The tests are ``test_board_moves.py`` and ``test_board_moves_carry.py``;
``test_sprint_team.py`` imports ``TRIAGE`` and ``NEEDS_WORK`` from
``test_board_moves``, which re-exports them.
"""

import secrets

import history_check
import pytest
from models import (
    AgentMessage,
    BacklogItem,
    Company,
    OutboxEvent,
    Sprint,
    TaskEvent,
)
from sqlmodel import select

import worker_stages

TRIAGE = "Pre-review quality gate failed after 2 rework(s) — blocked for manual triage: too short"


NEEDS_WORK = {"author": "Quality gate", "text": "NEEDS WORK: too short"}


@pytest.fixture()
def db(monkeypatch, sqlite_session):
    worker_stages.use_engine(monkeypatch, sqlite_session.get_bind())
    return sqlite_session


def _company(session, autonomy=2):
    company = Company(
        company_name=f"Move {secrets.token_hex(4)}", autonomy_level=autonomy
    )
    session.add(company)
    session.commit()
    session.refresh(company)
    return company


def _sprint(session, company, status="completed", name="Sprint 0"):
    sprint = Sprint(
        company_id=company.id, sprint_name=name, status=status, scope_size="S"
    )
    session.add(sprint)
    session.commit()
    session.refresh(sprint)
    return sprint


def _item(session, sprint, status="blocked", comments=None, title="Login", **extra):
    item = BacklogItem(
        sprint_id=sprint.id,
        title=title,
        description="d",
        assignee="Developer",
        status=status,
        comments=comments
        if comments is not None
        else [NEEDS_WORK, NEEDS_WORK, {"author": "Quality gate", "text": TRIAGE}],
        **extra,
    )
    session.add(item)
    session.commit()
    session.refresh(item)
    return item


def _assignments(session, item_id):
    return [
        e
        for e in session.exec(
            select(OutboxEvent).where(
                OutboxEvent.aggregate_type == "backlog_item",
                OutboxEvent.aggregate_id == str(item_id),
            )
        ).all()
        if e.payload.get("subject") == "task.assign"
    ]


def _history(session, item):
    """``history_check.check_task`` over what the database holds for *item*."""
    events = [
        {
            "id": e.id,
            "kind": e.event_type,
            "task_status": e.task_status,
            "payload": e.payload or {},
        }
        for e in session.exec(
            select(TaskEvent).where(TaskEvent.task_id == item.id).order_by(TaskEvent.id)
        ).all()
    ]
    messages = [
        {"id": m.id, "kind": m.kind, "body": m.body}
        for m in session.exec(
            select(AgentMessage).where(AgentMessage.backlog_item_id == item.id)
        ).all()
    ]
    task = {
        "id": item.id,
        "status": item.status,
        "comments": item.comments,
        "depends_on": item.depends_on or [],
    }
    return history_check.check_task(task, [], messages, {}, events)
