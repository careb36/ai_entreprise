"""Recording a task's events, and reading them back.

One event per transition with its actor and the comment carrying its id, an
unknown kind or an actor signing as another refused, the per-task chain shows
an altered event; ``GET /v1/tasks/{id}/events`` is company-scoped; a thread
the events do not cover is read from its comments; a writer holding a stale
task keeps the comment committed meanwhile, an unsigned event after a signed
one is a downgrade, a person's text is kept whole, and the inbox loads the
events once.

A task's life as events (issue #324): ``services/task_events.record`` writes
every transition as an event and renders it as the board comment; the
projections over the events (``services/tasks/task_projection.py``) answer the
board's questions as the comment readers (``services/tasks/board.py``) do. The
shared rows and fakes are in ``task_events_helpers``.
"""

from __future__ import annotations

import secrets

import pytest
import task_events_helpers
from models import BacklogItem
from services.tasks import board, task_events, task_projection
from sqlalchemy import text
from sqlmodel import Session
from task_events_helpers import (
    DELIVERABLE,
    _company,
    _headers,
    _item,
    _kinds,
    _sprint,
    assert_projection_agrees,
)

# The shared fixtures.
db = task_events_helpers.db
signing_key = task_events_helpers.signing_key


def test_record_writes_one_event_and_the_comment_rendering_it(db):
    sprint = _sprint(db, _company(db))
    item = _item(db, sprint)

    event = task_events.record(
        db,
        item,
        "delivered",
        "Developer",
        author="Dev",
        text="the deliverable",
        status="in_review",
    )
    db.commit()

    assert _kinds(db, item.id) == ["delivered"]
    assert event.company_id == sprint.company_id
    assert event.actor == "Developer" and event.task_status == "in_review"
    assert item.status == "in_review"
    [comment] = item.comments
    assert comment["author"] == "Dev" and comment["text"] == "the deliverable"
    assert comment["event_id"] == event.id
    assert event.payload["author"] == "Dev"
    assert event.payload["text"] == "the deliverable"
    assert event.seq == 1 and event.prev_hash is None
    assert event.chain_hash.startswith(("unsigned:", "hmac-sha256:"))


def test_an_event_without_text_writes_no_comment(db):
    item = _item(db, _sprint(db, _company(db)))
    task_events.record(db, item, "claimed", "system", status="in_progress")
    db.commit()
    assert item.comments in (None, [])
    assert item.status == "in_progress"
    assert _kinds(db, item.id) == ["claimed"]


def test_record_refuses_an_unknown_kind_and_an_actor_signing_as_another(db):
    item = _item(db, _sprint(db, _company(db)))
    with pytest.raises(task_events.UnknownEventKind):
        task_events.record(db, item, "teleported", "system")
    with pytest.raises(ValueError):
        task_events.record(db, item, "note", "system", author="user", text="hi")
    with pytest.raises(ValueError):
        task_events.record(db, item, "reviewed", "QA", author="Verifier", text="x")


def test_the_chain_links_a_tasks_events_and_shows_an_altered_one(db):
    item = _item(db, _sprint(db, _company(db)))
    first = task_events.record(db, item, "claimed", "system", status="in_progress")
    second = task_events.record(
        db, item, "delivered", "Developer", author="Dev", text="d", status="in_review"
    )
    db.commit()
    assert second.prev_hash == first.chain_hash and second.seq == 2
    assert task_events.verify_chain(db, item.id)["status"] == "intact"

    db.exec(
        text("UPDATE task_events SET actor = 'QA' WHERE id = :i").bindparams(i=first.id)
    )
    db.commit()
    db.expire_all()
    report = task_events.verify_chain(db, item.id)
    assert report["status"] == "broken"
    assert {"seq": 1, "reason": "hash_mismatch"} in report["breaks"]


def test_the_task_events_endpoint_is_company_scoped(test_client, db, signing_key):
    company, other = _company(db), _company(db)
    item = _item(db, _sprint(db, company))
    task_events.record(db, item, "claimed", "system", status="in_progress")
    task_events.record(
        db, item, "delivered", "Developer", author="Dev", text="d", status="in_review"
    )
    db.commit()

    viewer = _headers(db, company.id, "Viewer")
    response = test_client.get(f"/v1/tasks/{item.id}/events", headers=viewer)
    assert response.status_code == 200
    body = response.json()
    assert [e["kind"] for e in body["events"]] == ["claimed", "delivered"]
    assert body["source"] == "events" and body["chain"]["status"] == "intact"

    outsider = _headers(db, other.id, "Viewer")
    assert (
        test_client.get(f"/v1/tasks/{item.id}/events", headers=outsider).status_code
        == 403
    )
    assert test_client.get(f"/v1/tasks/{item.id}/events").status_code == 401
    assert test_client.get("/v1/tasks/987654/events", headers=viewer).status_code == 404


# --- a legacy thread is read from its comments ------------------------------


def test_a_thread_the_events_do_not_cover_is_read_from_its_comments(db):
    sprint = _sprint(db, _company(db))
    item = _item(
        db,
        sprint,
        status="blocked",
        comments=[
            {"author": "Dev", "text": DELIVERABLE},
            {"author": "QA", "text": "NEEDS WORK: tests"},
        ],
    )
    # One routed event after a legacy thread does not make the events answer.
    task_events.record(
        db,
        item,
        "blocked",
        "system",
        text="QA quality gate failed — task blocked pending manual triage.",
    )
    db.commit()
    assert task_projection.source(db, item) == "comments"
    assert task_projection.pending_rework_feedback(
        db, item
    ) == board.pending_rework_feedback(item.comments)
    assert task_projection.block_reason(db, item) == board.block_reason(item.comments)


def test_a_writer_holding_a_stale_task_keeps_the_comment_committed_meanwhile(db):
    item = _item(db, _sprint(db, _company(db)))
    with Session(db.get_bind()) as other:
        stale = other.get(BacklogItem, item.id)
        assert not stale.comments
        task_events.record(db, item, "note", "system", text="first writer")
        db.commit()
        task_events.record(other, stale, "note", "person", text="second writer")
        other.commit()
    db.expire_all()
    fresh = db.get(BacklogItem, item.id)
    assert [c["text"] for c in fresh.comments] == ["first writer", "second writer"]
    assert_projection_agrees(db, item.id)


def test_an_unsigned_event_after_a_signed_one_is_a_downgrade(db, monkeypatch):
    import audit_signing

    key = secrets.token_hex(32)
    monkeypatch.setattr(audit_signing, "_configured_key", lambda: key)
    item = _item(db, _sprint(db, _company(db)))
    task_events.record(db, item, "claimed", "system", status="in_progress")
    # Written without the key, as someone rewriting the tail would.
    monkeypatch.setattr(audit_signing, "_configured_key", lambda: None)
    task_events.record(db, item, "note", "system", text="rewritten")
    db.commit()
    monkeypatch.setattr(audit_signing, "_configured_key", lambda: key)
    report = task_events.verify_chain(db, item.id)
    assert report["status"] == "broken"
    assert report["breaks"] == [{"seq": 2, "reason": "downgrade"}]


def test_a_persons_text_is_kept_whole_and_the_platforms_is_capped(db):
    item = _item(db, _sprint(db, _company(db)))
    feedback = "NEEDS WORK: review by Ana: " + "rename it. " * 900
    person = task_events.record(db, item, "reviewed", "person", text=feedback)
    worker = task_events.record(db, item, "note", "system", text="x" * 9000)
    db.commit()
    assert person.payload["text"] == feedback
    assert item.comments[-2]["text"] == feedback
    assert len(worker.payload["text"]) == task_events.TEXT_LIMIT
    assert_projection_agrees(db, item.id)


def test_the_inbox_loads_the_events_once_for_every_task(
    test_client, db, signing_key, monkeypatch
):
    company = _company(db)
    sprint = _sprint(db, company)
    for _ in range(3):
        item = _item(db, sprint)
        task_events.record(
            db, item, "delivered", "Developer", author="Dev", text=DELIVERABLE
        )
        task_events.record(
            db,
            item,
            "blocked",
            "system",
            status="blocked",
            text="QA quality gate failed — task blocked pending manual triage.",
        )
    db.commit()
    calls = {"many": 0}
    real = task_events.for_tasks

    def counted(session, ids):
        calls["many"] += 1
        return real(session, ids)

    def refused(*_a, **_k):
        raise AssertionError("one query per task")

    monkeypatch.setattr(task_events, "for_tasks", counted)
    monkeypatch.setattr(task_events, "for_task", refused)
    response = test_client.get(
        f"/v1/inbox?company_id={company.id}",
        headers=_headers(db, company.id, "Viewer"),
    )
    assert response.status_code == 200, response.text
    assert calls["many"] == 1
    tasks = response.json()["blocked_tasks"]
    assert len(tasks) == 3
    assert all(t["reason"].startswith("QA quality gate failed") for t in tasks)
