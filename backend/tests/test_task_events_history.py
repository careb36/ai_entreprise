"""The history checker and the migration read a task's events (issue #324).

The checker refuses a done task without an approval event, a thread that
differs from its events and a status the events did not leave, reading the
events from the database, and finds a comment naming an event the task lacks
or one deleted or moved; migration 0014 backfills a readable thread, marks
one it cannot read and reads a person's closing note as their approval.

A task's life as events (issue #324): ``services/task_events.record`` writes
every transition as an event and renders it as the board comment; the
projections over the events (``services/tasks/task_projection.py``) answer the
board's questions as the comment readers (``services/tasks/board.py``) do. The
shared rows and fakes are in ``task_events_helpers``.
"""

from __future__ import annotations

import json

import history_check
import migrations
import task_events_helpers
from models import BacklogItem, TaskEvent
from services.tasks import board, task_events, task_projection
from sqlalchemy import text
from sqlmodel import Session, create_engine, select
from task_events_helpers import _as_dicts, _company, _item, _sprint

# The shared fixtures.
db = task_events_helpers.db


def _event(id_, kind, author, text_, status=None, **payload):
    return {
        "id": id_,
        "kind": kind,
        "task_status": status,
        "payload": {"author": author, "text": text_, **payload},
    }


def test_history_check_refuses_a_done_task_without_an_approval_event():
    comments = [
        {"author": "Dev", "text": "the work", "event_id": 1},
        {"author": "QA", "text": "APPROVED: fine", "event_id": 2},
    ]
    events = [
        _event(1, "delivered", "Dev", "the work", "in_review"),
        # The thread says APPROVED, the event says it was a note.
        _event(2, "note", "QA", "APPROVED: fine", "done"),
    ]
    violations = history_check.check_task(
        {"id": 1, "status": "done", "comments": comments},
        [{"version": 1, "status": "verified"}],
        [],
        events=events,
    )
    assert "done_without_approval_event" in violations

    events[1] = _event(
        2, "reviewed", "QA", "APPROVED: fine", "done", verdict="approved"
    )
    assert (
        history_check.check_task(
            {"id": 1, "status": "done", "comments": comments},
            [{"version": 1, "status": "verified"}],
            [],
            events=events,
        )
        == []
    )


def test_history_check_refuses_a_thread_that_differs_from_its_events():
    comments = [
        {"author": "Dev", "text": "the work", "event_id": 1},
        {"author": "QA", "text": "APPROVED: fine", "event_id": 7},
    ]
    events = [_event(1, "delivered", "Dev", "other work", "in_review")]
    violations = history_check.check_events(comments, "in_review", events)
    assert any(
        v.startswith("comment_differs_from_event: comment 0") for v in violations
    )
    assert any(v.startswith("comment_without_event: comment 1") for v in violations)


def test_history_check_refuses_a_status_the_events_did_not_leave():
    comments = [{"author": "Dev", "text": "w", "event_id": 1}]
    events = [_event(1, "delivered", "Dev", "w", "in_review")]
    assert history_check.check_events(comments, "done", events)[-1].startswith(
        "status_differs_from_events"
    )
    # A legacy marker: the events do not render the whole thread; only the
    # comments they name are compared.
    events.append({"id": 2, "kind": "legacy", "task_status": None, "payload": {}})
    assert history_check.check_events(comments, "done", events) == []


def test_history_check_reads_the_events_from_the_database(db, monkeypatch):
    import database

    monkeypatch.setattr(database, "engine", db.get_bind())
    item = _item(db, _sprint(db, _company(db)))
    task_events.record(
        db, item, "delivered", "Developer", author="Dev", text="w", status="in_review"
    )
    db.commit()
    item.status = "done"  # behind the events' back
    db.add(item)
    db.commit()
    found = [v for v in history_check.check_database() if f"task {item.id} " in v]
    assert any("done_without_approval_event" in v for v in found)
    assert any("status_differs_from_events" in v for v in found)


def test_the_migration_backfills_events_and_marks_what_it_cannot_read(
    tmp_path, monkeypatch
):
    from alembic import command

    url = f"sqlite:///{tmp_path / 'backfill.sqlite'}"
    monkeypatch.setenv("ALEMBIC_DATABASE_URL", url)
    engine = create_engine(url)
    config = migrations.alembic_config()
    with engine.begin() as connection:
        config.attributes["connection"] = connection
        command.upgrade(config, "0013")
    with engine.begin() as connection:
        connection.execute(
            text(
                "INSERT INTO companies (id, company_name, autonomy_level, "
                "default_scope_size, git_workflow) VALUES (1, 'Acme', 2, 'M', 0)"
            )
        )
        connection.execute(
            text(
                "INSERT INTO sprints (id, company_id, sprint_name, scope_size, status) "
                "VALUES (1, 1, 'S', 'S', 'completed')"
            )
        )
        readable = (
            '[{"author": "Dev", "text": "the work", "created_at": "2026-01-01T10:00:00"},'
            ' {"author": "Verifier", "text": "NEEDS WORK: delivery refuted — x"},'
            ' {"author": "Dev", "text": "the work again"},'
            ' {"author": "QA", "text": "APPROVED: fine"}]'
        )
        unreadable = (
            '[{"author": "Dev", "text": "w"},'
            ' {"author": "System", "text": "Something this revision never wrote"}]'
        )
        # A person's note longer than the platform's cap is backfilled whole.
        long_note = "Good enough, closing it. " + "detail " * 900
        by_a_person = json.dumps(
            [
                {"author": "Dev", "text": "w"},
                {"author": "QA", "text": "NEEDS WORK: tests"},
                {"author": "user", "text": long_note},
            ]
        )
        for item_id, status, comments in (
            (1, "done", readable),
            (2, "blocked", unreadable),
            (3, "done", by_a_person),
        ):
            connection.execute(
                text(
                    "INSERT INTO backlog_items (id, sprint_id, title, description, status, "
                    "priority, comments) VALUES (:i, 1, 'T', '', :s, 'medium', :c)"
                ).bindparams(i=item_id, s=status, c=comments)
            )
    migrations.upgrade_to_head(engine)

    with Session(engine) as session:
        done = session.get(BacklogItem, 1)
        events = task_events.for_task(session, 1)
        assert [e.event_type for e in events] == [
            "delivered",
            "reviewed",
            "delivered",
            "reviewed",
        ]
        assert all(e.legacy and e.seq is None for e in events)
        assert [e.actor for e in events] == ["Dev", "gate", "Dev", "QA"]
        assert events[-1].payload["verdict"] == "approved"
        assert task_projection.covers(done, events)
        assert task_projection.pending_rework_from_events(
            events
        ) == board.pending_rework_feedback(done.comments)
        assert (
            history_check.check_events(done.comments, "done", _as_dicts(events)) == []
        )

        blocked = session.get(BacklogItem, 2)
        events = task_events.for_task(session, 2)
        assert [e.event_type for e in events] == ["delivered", "legacy"]
        assert events[-1].payload == {"unread_comments": 1}
        assert not task_projection.covers(blocked, events)
        assert "event_id" not in blocked.comments[1]
        closed = session.get(BacklogItem, 3)
        events = task_events.for_task(session, 3)
        assert [e.event_type for e in events][-1] == "approved_by_person"
        assert events[-1].task_status == "done"
        assert events[-1].payload["text"] == long_note.strip()
        assert len(long_note) > task_events.TEXT_LIMIT
        assert (
            history_check.check_events(closed.comments, "done", _as_dicts(events)) == []
        )
        # A live event after the backfill starts the task's chain.
        live = task_events.record(
            session, blocked, "note", "person", text="looked at it"
        )
        session.commit()
        assert live.seq == 1
        assert session.exec(select(TaskEvent).where(TaskEvent.task_id == 2)).all()


def test_history_check_finds_comments_naming_events_the_task_lacks():
    comments = [{"author": "Dev", "text": "w", "event_id": 4}]
    [violation] = history_check.check_events(comments, "in_review", [])
    assert violation.startswith("comment_without_event: comment 0")
    # A thread from before events existed is not compared.
    assert history_check.check_events([{"author": "Dev", "text": "w"}], "x", []) == []


def test_history_check_finds_a_deleted_or_moved_comment():
    events = [
        _event(1, "delivered", "Dev", "w", "in_review"),
        # An escalation carries no text and no comment, on purpose.
        {"id": 2, "kind": "escalated", "task_status": None, "payload": {}},
        _event(3, "blocked", "System", "QA quality gate failed", "blocked"),
    ]
    whole = [
        {"author": "Dev", "text": "w", "event_id": 1},
        {"author": "System", "text": "QA quality gate failed", "event_id": 3},
    ]
    assert history_check.check_events(whole, "blocked", events) == []

    [violation] = history_check.check_events(whole[:1], "blocked", events)
    assert violation.startswith("event_without_comment: event 3 (blocked)")

    moved = list(reversed(whole))
    assert any(
        v.startswith("comments_out_of_order")
        for v in history_check.check_events(moved, "blocked", events)
    )
