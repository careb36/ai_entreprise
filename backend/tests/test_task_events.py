"""A task's life as events (issue #324): ``services/task_events.record`` writes
every transition as an event and renders it as the board comment; the
projections over the events (``services/task_projection.py``) answer the
board's questions as the comment readers (``services/board.py``) do; the
history checker refuses a thread and events that disagree; the migration
backfills events from a legacy thread; ``GET /v1/tasks/{id}/events`` is
company-scoped."""

from __future__ import annotations

import asyncio
import json
import secrets
from uuid import uuid4

import auth_tokens
import history_check
import migrations
import pytest
import worker_stages
from auth_tokens import create_access_token
from config import settings
from models import (
    AgentRun,
    BacklogItem,
    Company,
    CompanyMember,
    Sprint,
    TaskEvent,
    User,
    Verification,
)
from services import (
    board,
    carry_over,
    dependencies,
    run_review,
    scrum_master,
    sprint_control,
    task_events,
    task_projection,
    task_worker,
)
from sqlalchemy import text
from sqlmodel import Session, create_engine, select

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
    return [e.event_type for e in task_events.for_task(session, item_id)]


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


# --- record ----------------------------------------------------------------


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


# --- each transition records its event --------------------------------------


def _delivered_item(session, autonomy=2, verdict="verified", history=()):
    """A task whose whole thread was written through events, waiting for review."""
    company = _company(session, autonomy)
    sprint = _sprint(session, company)
    item = _item(session, sprint)
    for kind, actor, author, line in history:
        task_events.record(session, item, kind, actor, author=author, text=line)
    task_events.record(
        session,
        item,
        "delivered",
        "Developer",
        author="Dev",
        text=DELIVERABLE,
        status="in_review",
    )
    session.commit()
    if verdict is not None:
        run = AgentRun(
            company_id=company.id,
            external_ref=f"task-{item.id}",
            producer=task_worker.TASK_WORKER_PRODUCER,
            claims={"files": ["src/a.py"]},
            artifact_digest="d",
            artifact_size=1,
            status=verdict,
        )
        session.add(run)
        session.commit()
        session.add(
            Verification(
                company_id=company.id,
                agent_run_id=run.id,
                verdict=verdict,
                checks=[
                    {"name": "files_written", "outcome": verdict, "observation": "x"}
                ],
            )
        )
        session.commit()
    return item.id


def _review(monkeypatch, session, item_id, reply):
    title = session.get(BacklogItem, item_id).title

    def _answer(agent, prompt, **kwargs):
        if title not in prompt:
            return "APPROVED: not this test's task"
        if reply is None:
            raise RuntimeError("model down")
        return reply

    monkeypatch.setattr(task_worker, "_call_llm_for_review", _answer)
    asyncio.run(task_worker._review_cycle(_FakeNATS()))


GATE_LIMIT = task_worker.AUTOPILOT_MAX_REWORK_CYCLES
_GATE_REWORK = ("reviewed", "gate", "Verifier", "NEEDS WORK: delivery refuted — x")
_QA_REWORK = ("reviewed", "QA", "QA", "NEEDS WORK: add tests")

SCENARIOS = {
    "approved": (2, "verified", (), "APPROVED: complete", ["reviewed"], "done"),
    "risk_hold_at_autonomy_1": (
        1,
        "verified",
        (),
        "APPROVED: complete",
        ["reviewed", "risk_hold"],
        "blocked",
    ),
    "qa_rework": (
        2,
        "verified",
        (),
        "NEEDS WORK: add tests",
        ["reviewed", "sent_back"],
        "todo",
    ),
    "qa_rework_exhausted": (
        2,
        "verified",
        (_QA_REWORK,) * GATE_LIMIT,
        "NEEDS WORK: still no tests",
        ["reviewed", "blocked", "escalated"],
        "blocked",
    ),
    "gate_rework": (2, "refuted", (), "APPROVED: x", ["reviewed"], "todo"),
    "gate_exhausted": (
        2,
        "refuted",
        (_GATE_REWORK,) * GATE_LIMIT,
        "APPROVED: x",
        ["blocked", "escalated"],
        "blocked",
    ),
    "review_failed": (2, "verified", (), None, ["review_failed"], "in_review"),
}


@pytest.mark.parametrize("name", sorted(SCENARIOS))
def test_each_review_transition_records_its_events_and_the_projection_agrees(
    db, monkeypatch, name
):
    autonomy, verdict, history, reply, kinds, status = SCENARIOS[name]
    item_id = _delivered_item(db, autonomy, verdict, history)
    before = len(_kinds(db, item_id))

    _review(monkeypatch, db, item_id, reply)

    assert _kinds(db, item_id)[before:] == kinds
    db.expire_all()
    assert db.get(BacklogItem, item_id).status == status
    assert_projection_agrees(db, item_id)


def test_the_worker_helpers_record_the_kind_they_are_given(db):
    item = _item(db, _sprint(db, _company(db)))
    task_worker._update_item_status(item.id, "in_progress", kind="claimed")
    task_worker._update_item_status(
        item.id,
        "in_review",
        "deliverable",
        "Dev",
        kind="delivered",
        actor="Developer",
    )
    task_worker._update_item_status(
        item.id, "blocked", "Agent error: boom", "System", kind="blocked"
    )
    assert _kinds(db, item.id) == ["claimed", "delivered", "blocked"]
    [delivered] = [
        e for e in task_events.for_task(db, item.id) if e.event_type == "delivered"
    ]
    assert delivered.actor == "Developer"
    assert_projection_agrees(db, item.id)
    db.expire_all()
    # An agent error is retried once, then waits for a person.
    assert board.blocked_item_next_step(
        db.get(BacklogItem, item.id).comments, "Developer"
    ) in ("review", "retry")


def test_a_dependency_hold_and_its_release_are_events(db):
    sprint = _sprint(db, _company(db))
    stuck = _item(db, sprint, status="blocked")
    task_events.record(
        db,
        stuck,
        "blocked",
        "system",
        status="blocked",
        text="QA quality gate failed — task blocked pending manual triage.",
    )
    waiting = _item(db, sprint, status="todo", depends_on=[stuck.id])
    db.commit()

    dependencies.settle(db, sprint.id)
    db.commit()
    assert _kinds(db, waiting.id) == ["blocked"]
    assert waiting.status == "blocked"
    assert_projection_agrees(db, waiting.id)

    stuck = db.get(BacklogItem, stuck.id)
    task_events.record(
        db, stuck, "approved_by_person", "person", text="done by hand", status="done"
    )
    db.commit()
    dependencies.settle(db, sprint.id)
    db.commit()
    assert _kinds(db, waiting.id) == ["blocked", "released"]
    assert_projection_agrees(db, waiting.id)


def test_a_carry_over_is_an_event_on_both_tasks(db):
    company = _company(db)
    sprint = _sprint(db, company, status="completed")
    item = _item(db, sprint, status="blocked")
    task_events.record(
        db,
        item,
        "blocked",
        "system",
        status="blocked",
        text="QA quality gate failed — task blocked pending manual triage.",
    )
    db.commit()
    nxt = _sprint(db, company)

    [clone] = carry_over.carry_over(db, company.id, [item.id], nxt, "ana", "Manager")
    db.commit()

    assert _kinds(db, item.id) == ["blocked", "carried_over"]
    assert _kinds(db, clone.id) == ["carried_over"]
    directions = [
        e.payload["direction"]
        for e in task_events.for_task(db, item.id) + task_events.for_task(db, clone.id)
        if e.event_type == "carried_over"
    ]
    assert directions == ["to", "from"]
    assert_projection_agrees(db, item.id)
    assert_projection_agrees(db, clone.id)


def test_stopping_a_sprint_and_a_credit_pause_are_events(db, monkeypatch):
    company = _company(db)
    sprint = _sprint(db, company)
    item = _item(db, sprint, status="todo")
    other = _item(db, sprint, status="in_progress")

    assert sprint_control.stop(db, sprint) == 2
    db.commit()
    assert _kinds(db, item.id) == ["blocked"]
    assert _kinds(db, other.id) == ["blocked"]
    assert_projection_agrees(db, item.id)

    paused_sprint = _sprint(db, company)
    paused = _item(db, paused_sprint, status="in_progress")
    sprint_control.pause_task_for_credit(
        db.get_bind(), paused.id, paused_sprint.id, "deepseek"
    )
    assert _kinds(db, paused.id) == ["paused"]
    assert_projection_agrees(db, paused.id)


def _task_run(session, item, company_id):
    run = AgentRun(
        company_id=company_id,
        external_ref=f"task-{item.id}",
        producer=task_worker.TASK_WORKER_PRODUCER,
        subject={"backlog_item_id": item.id},
        claims={},
        artifact_digest="d",
        artifact_size=1,
        status="verified",
    )
    session.add(run)
    session.commit()
    session.refresh(run)
    return run


def test_a_persons_review_records_its_events(db):
    company = _company(db, autonomy=1)
    sprint = _sprint(db, company)
    item = _item(db, sprint)
    task_events.record(
        db, item, "delivered", "Developer", author="Dev", text=DELIVERABLE
    )
    task_events.record(
        db, item, "reviewed", "QA", author="QA", text="APPROVED: ok", status="blocked"
    )
    task_events.record(
        db,
        item,
        "risk_hold",
        "system",
        text=f"{board.RISK_HOLD_PREFIX} (human-led). Mark it done to accept it.",
    )
    db.commit()
    run = _task_run(db, item, company.id)
    assert task_projection.awaits_risk_acceptance(db, item)

    review = run_review.submit_review(
        db,
        run,
        event="approve",
        body="fine",
        author_name="Ana",
        author_user_id=None,
    )
    db.commit()
    assert review.outcome == "accepted"
    assert _kinds(db, item.id)[-1] == "approved_by_person"
    assert_projection_agrees(db, item.id)

    # Changes requested on a task that waits for a person.
    held = _item(db, sprint)
    task_events.record(
        db, held, "delivered", "Developer", author="Dev", text=DELIVERABLE
    )
    task_events.record(
        db,
        held,
        "blocked",
        "system",
        status="blocked",
        text="QA quality gate failed — task blocked pending manual triage.",
    )
    db.commit()
    held_run = _task_run(db, held, company.id)
    review = run_review.submit_review(
        db,
        held_run,
        event="request_changes",
        body="rename it",
        author_name="Ana",
        author_user_id=None,
    )
    db.commit()
    assert review.outcome == "sent_back"
    assert _kinds(db, held.id)[-2:] == ["sent_back", "reviewed"]
    db.expire_all()
    assert task_projection.pending_rework_feedback(
        db, db.get(BacklogItem, held.id)
    ) == ["user: NEEDS WORK: review by Ana: rename it"]
    assert_projection_agrees(db, held.id)


def test_the_scrum_masters_decision_is_an_event_without_a_comment(db):
    company = _company(db)
    sprint = _sprint(db, company)
    item = _item(db, sprint, status="blocked")
    message = scrum_master.record_decision(
        db,
        company.id,
        sprint.id,
        item.id,
        item.assignee,
        {"decision": scrum_master.DECISION_RETRY, "reason": "r", "brief": "b"},
        answers=None,
    )
    db.commit()
    [event] = task_events.for_task(db, item.id)
    assert event.event_type == "decided" and event.actor == "ScrumMaster"
    assert event.payload["message_id"] == message.id
    assert not item.comments


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


def test_a_persons_change_on_the_board_is_an_event(test_client, db, signing_key):
    company = _company(db)
    item = _item(db, _sprint(db, company), status="blocked")
    headers = _headers(db, company.id, "Manager")

    response = test_client.patch(
        f"/v1/backlog/{item.id}",
        json={"status": "todo", "comment": f"{board.SEND_BACK_MARKER}: retry it"},
        headers=headers,
    )
    assert response.status_code == 200, response.text
    db.expire_all()
    [event] = task_events.for_task(db, item.id)
    assert event.event_type == "sent_back" and event.actor == "person"
    assert response.json()["comments"][-1]["event_id"] == event.id
    assert_projection_agrees(db, item.id)


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


# --- the history checker reads the events beside the thread -----------------


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


# --- the migration backfills events from a legacy thread --------------------


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


# --- review findings: concurrency, signing, text, the checker, the inbox -----


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
