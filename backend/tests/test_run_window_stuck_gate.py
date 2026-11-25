"""
The run-window gate refuses to dispatch a sprint's new tasks when one of its
prior tasks is an async run stuck in a non-terminal state beyond the
configured threshold (``RUN_WINDOW_STUCK_TASK_SECONDS``).
"""

import secrets
from datetime import UTC, datetime, timedelta

import pytest
from config import settings
from models import BacklogItem, Company, OutboxEvent, Sprint
from services.loop import run_window_gate as gate
from sqlmodel import select


@pytest.fixture
def clock(monkeypatch):
    now_at = {"at": datetime(2026, 11, 24, 22, 30, tzinfo=UTC)}
    monkeypatch.setattr(gate, "utc_now", lambda: now_at["at"])
    return now_at


def _company(session):
    company = Company(company_name=f"Stuck {secrets.token_hex(3)}", run_window=None)
    session.add(company)
    session.commit()
    session.refresh(company)
    return company


def _sprint(session, company_id):
    sprint = Sprint(
        company_id=company_id,
        sprint_name="Stuck sprint",
        status="active",
        scope_size="S",
    )
    session.add(sprint)
    session.commit()
    session.refresh(sprint)
    return sprint


def _item(session, sprint_id, status, updated_at):
    item = BacklogItem(
        sprint_id=sprint_id,
        title=f"Task {status}",
        status=status,
        created_at=updated_at,
        updated_at=updated_at,
    )
    session.add(item)
    session.commit()
    session.refresh(item)
    return item


def test_new_dispatch_refused_while_an_older_run_is_stuck(
    sqlite_session, clock, monkeypatch
):
    threshold = 1  # very small: the older task is definitely stuck
    monkeypatch.setattr(settings, "RUN_WINDOW_STUCK_TASK_SECONDS", threshold)

    company = _company(sqlite_session)
    sprint = _sprint(sqlite_session, company.id)
    now = clock["at"]

    older = _item(
        sqlite_session,
        sprint.id,
        "in_progress",
        now - timedelta(seconds=threshold + 5),
    )
    waiting_todo = _item(sqlite_session, sprint.id, "todo", now)

    # The gate returns a blocking reason naming the stuck task.
    reason = gate.stuck_block_reason(sqlite_session, sprint, now)
    assert reason is not None
    assert f"task {older.id}" in reason
    assert "in_progress" in reason

    # The dispatch path uses the same reason and refuses to queue the backlog.
    refused = gate.dispatch_or_defer(sqlite_session, sprint, now)
    assert refused["dispatched"] == 0
    assert refused.get("stuck_block_reason") == reason
    sqlite_session.refresh(waiting_todo)
    assert waiting_todo.status == "todo"

    existing_outbox = len(sqlite_session.exec(select(OutboxEvent)).all())
    dispatched = gate.release_sprint(sqlite_session, sprint, "window_open", now)

    assert dispatched == 0
    assert len(sqlite_session.exec(select(OutboxEvent)).all()) == existing_outbox
    sqlite_session.refresh(waiting_todo)
    assert waiting_todo.status == "todo"


def test_stuck_gate_disabled_when_threshold_is_zero(sqlite_session, clock, monkeypatch):
    monkeypatch.setattr(settings, "RUN_WINDOW_STUCK_TASK_SECONDS", 0)
    company = _company(sqlite_session)
    sprint = _sprint(sqlite_session, company.id)
    now = clock["at"]
    _item(
        sqlite_session,
        sprint.id,
        "in_progress",
        now - timedelta(days=10),
    )
    assert gate.stuck_block_reason(sqlite_session, sprint, now) is None
