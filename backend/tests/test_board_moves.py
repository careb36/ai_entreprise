"""A card a person moves on the board (``services/loop/board_moves.py``).

- Moving a task to To do starts it: the move is a send-back (the rework
  budgets restart), a ``completed`` or ``cancelled`` sprint is reopened and
  audited, a ``planned`` or ``paused`` one is not started and the answer says
  why, an archived one is refused, and the task is queued at once when its
  dependencies are done; a duplicate assignment is claimed only once.
- A reopened sprint closes again without a second follow-up, retrospective
  or procedure round.
- "Move to sprint…" carries a blocked (or To do) task into another open
  sprint or a new one, once.
- Every history these paths write passes ``history_check``.
"""

import asyncio
import secrets
from unittest.mock import patch

import history_check
import pytest
import worker_stages
from models import (
    AgentMessage,
    AuditLog,
    BacklogItem,
    Company,
    OutboxEvent,
    Specification,
    Sprint,
    TaskEvent,
)
from models.company import CompanyMember, User
from services import (
    acceptance,
    board_moves,
    carry_over,
    locks,
    replicas,
    sprint_close,
)
from services.board import is_send_back, rework_requests_by
from services.loop.worker import close as worker_close
from services.loop.worker import procedures_round
from sqlmodel import Session, select
from test_review_gate import _FakeNATS

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


def _reopen_rows(session, sprint_id):
    return session.exec(
        select(AuditLog).where(
            AuditLog.interaction_id == f"sprint:{sprint_id}:admin",
            AuditLog.action == board_moves.REOPEN_ACTION,
        )
    ).all()


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


def _move(client, item, **body):
    return client.patch(f"/v1/backlog/{item.id}", json={"status": "todo", **body})


# ── Moving a card to To do starts it ─────────────────────────────────────────


def test_a_card_moved_to_todo_in_a_completed_sprint_reopens_it_and_is_queued(
    test_client, db
):
    sprint = _sprint(db, _company(db))
    item = _item(db, sprint)

    response = _move(test_client, item)

    assert response.status_code == 200, response.text
    body = response.json()
    assert (body["status"], body["queued"], body["reopened"]) == ("todo", True, True)
    assert (body["sprint_status"], body["reason"]) == ("active", None)
    db.expire_all()
    assert db.get(Sprint, sprint.id).status == "active"
    item = db.get(BacklogItem, item.id)
    # A send-back: the spent rework budget restarts.
    assert item.comments[-1]["author"] == "user"
    assert item.comments[-1]["text"] == board_moves.SENT_BACK_ON_BOARD
    assert is_send_back(item.comments[-1])
    assert rework_requests_by(item.comments, "Quality gate") == 0
    kinds = [
        e.event_type
        for e in db.exec(select(TaskEvent).where(TaskEvent.task_id == item.id))
    ]
    assert kinds[-1] == "sent_back"
    (assign,) = _assignments(db, item.id)
    assert assign.payload["message"]["sprint_id"] == sprint.id
    (row,) = _reopen_rows(db, sprint.id)
    assert row.result["from_status"] == "completed"
    assert row.result["backlog_item_id"] == item.id
    assert board_moves.closed_before(db, sprint.id)
    assert _history(db, item) == []


def test_a_persons_own_comment_is_kept_instead_of_the_send_back_note(test_client, db):
    sprint = _sprint(db, _company(db), status="active")
    item = _item(db, sprint)

    body = _move(test_client, item, comment="Try a smaller change").json()

    assert (body["queued"], body["reopened"], body["sprint_status"]) == (
        True,
        False,
        "active",
    )
    db.expire_all()
    assert db.get(BacklogItem, item.id).comments[-1]["text"] == "Try a smaller change"
    assert _reopen_rows(db, sprint.id) == []


def test_a_cancelled_sprint_is_reopened_by_a_move_to_todo(test_client, db):
    sprint = _sprint(db, _company(db), status="cancelled")
    item = _item(
        db,
        sprint,
        comments=[
            {
                "author": "System",
                "text": "Stopped: a Manager stopped the sprint; nothing more runs on it.",
            }
        ],
    )

    body = _move(test_client, item).json()

    assert (body["queued"], body["reopened"], body["sprint_status"]) == (
        True,
        True,
        "active",
    )
    db.expire_all()
    assert db.get(Sprint, sprint.id).status == "active"
    (row,) = _reopen_rows(db, sprint.id)
    assert row.result["from_status"] == "cancelled"
    # It never closed: its first close is a full one.
    assert not board_moves.closed_before(db, sprint.id)


def test_an_archived_sprint_refuses_the_move_and_records_nothing(test_client, db):
    from datetime import UTC, datetime

    sprint = _sprint(db, _company(db))
    sprint.archived_at = datetime.now(UTC).replace(tzinfo=None)
    db.add(sprint)
    db.commit()
    item = _item(db, sprint)
    before = list(item.comments)

    response = _move(test_client, item)

    assert response.status_code == 409
    assert "archived" in response.json()["detail"]
    db.expire_all()
    assert db.get(BacklogItem, item.id).status == "blocked"
    assert db.get(BacklogItem, item.id).comments == before
    assert db.get(Sprint, sprint.id).status == "completed"
    assert _assignments(db, item.id) == []


@pytest.mark.parametrize(
    ("status", "reason"),
    [
        ("paused", "the sprint is paused: resume it to run its tasks"),
        ("planned", "the sprint is planned: start it to run its tasks"),
    ],
)
def test_a_halted_sprint_is_not_started_and_the_answer_says_why(
    test_client, db, status, reason
):
    sprint = _sprint(db, _company(db), status=status)
    item = _item(db, sprint)

    body = _move(test_client, item).json()

    assert (body["status"], body["queued"], body["reopened"]) == ("todo", False, False)
    assert (body["sprint_status"], body["reason"]) == (status, reason)
    db.expire_all()
    assert db.get(Sprint, sprint.id).status == status
    assert _assignments(db, item.id) == []
    # Still a send-back: the task restarts its budgets once the sprint runs.
    assert is_send_back(db.get(BacklogItem, item.id).comments[-1])


def test_a_task_waiting_for_another_is_not_queued(test_client, db):
    sprint = _sprint(db, _company(db))
    first = _item(db, sprint, title="Schema")
    second = _item(db, sprint, title="API", depends_on=[first.id])

    body = _move(test_client, second).json()

    assert (body["queued"], body["reopened"]) == (False, True)
    assert body["reason"] == "it waits for other tasks of the sprint to be done"
    assert _assignments(db, second.id) == []


def test_a_card_already_in_todo_is_left_alone(test_client, db):
    sprint = _sprint(db, _company(db), status="completed")
    item = _item(db, sprint, status="todo", comments=[])

    body = _move(test_client, item).json()

    assert "queued" not in body
    db.expire_all()
    assert db.get(Sprint, sprint.id).status == "completed"


def test_a_duplicate_assignment_from_the_review_cycle_is_claimed_once(test_client, db):
    sprint = _sprint(db, _company(db))
    item = _item(db, sprint)
    _move(test_client, item)

    engine = db.get_bind()
    assert replicas.claim_todo(engine, item.id) is True
    assert replicas.claim_todo(engine, item.id) is False


# ── A reopened sprint closes again, once ─────────────────────────────────────


def test_a_reopened_sprint_closes_again_without_a_second_follow_up(
    test_client, db, monkeypatch
):
    from memory import engram_processor

    def _no_graph():
        raise RuntimeError("no Neo4j in tests")

    monkeypatch.setattr(engram_processor, "get_engram_processor", _no_graph)
    company = _company(db, autonomy=3)
    sprint = _sprint(db, company)
    item = _item(db, sprint)
    _item(db, sprint, status="done", comments=[], title="Done one")
    assert _move(test_client, item).json()["reopened"] is True
    # The second attempt blocks again for a person: the sprint settles.
    db.expire_all()
    item = db.get(BacklogItem, item.id)
    item.status = "blocked"
    item.comments = [*item.comments, {"author": "Quality gate", "text": TRIAGE}]
    db.add(item)
    db.commit()
    proposals, evaluations = [], []
    monkeypatch.setattr(
        procedures_round, "_propose_procedures", lambda *a: proposals.append(a)
    )
    monkeypatch.setattr(
        procedures_round, "_evaluate_procedures", lambda *a: evaluations.append(a)
    )
    sprints_before = len(
        db.exec(select(Sprint).where(Sprint.company_id == company.id)).all()
    )

    with (
        patch("services.loop.worker.close.AUTO_SELF_IMPROVEMENT_ENABLED", True),
        patch(
            "services.loop.worker.close.plan_self_improvement_sprint",
            side_effect=AssertionError("no second follow-up"),
        ),
    ):
        asyncio.run(worker_close._finalize_sprint_if_ready(_FakeNATS(), sprint.id))
        db.expire_all()
        assert db.get(Sprint, sprint.id).status == "completed"
        # And the close itself says so, called again by hand on a reopened sprint.
        reopened = db.get(Sprint, sprint.id)
        reopened.status = "active"
        db.add(reopened)
        db.commit()
        result = worker_close._complete_sprint_and_plan_follow_up(sprint.id)

    assert (result["completed"], result["auto_opened"]) == (True, False)
    assert result["reason"] == sprint_close.RECLOSED
    assert sprint_close.describe_follow_up_reason(result).startswith(
        "Sprint closed again"
    )
    assert len(
        db.exec(select(Sprint).where(Sprint.company_id == company.id)).all()
    ) == (sprints_before)
    assert (proposals, evaluations) == ([], [])
    retrospectives = db.exec(
        select(AgentMessage).where(
            AgentMessage.sprint_id == sprint.id, AgentMessage.kind == "retrospective"
        )
    ).all()
    assert retrospectives == []
    assert _history(db, db.get(BacklogItem, item.id)) == []


def test_a_first_close_still_plans_the_follow_up(db):
    company = _company(db, autonomy=3)
    sprint = _sprint(db, company, status="active")
    _item(db, sprint, status="done", comments=[], title="Done")
    plan = {"sprint_name": "Next", "goal": "g", "tasks": [{"title": "t"}]}

    with (
        patch("services.loop.worker.close.AUTO_SELF_IMPROVEMENT_ENABLED", True),
        patch(
            "services.loop.worker.close.plan_self_improvement_sprint", return_value=plan
        ),
        patch(
            "services.loop.worker.close.build_self_improvement_objective",
            return_value="g",
        ),
    ):
        result = worker_close._complete_sprint_and_plan_follow_up(sprint.id)

    assert (result["completed"], result["auto_opened"]) == (True, True)


# ── Move to sprint… ──────────────────────────────────────────────────────────


def test_a_blocked_task_is_carried_into_an_open_sprint_and_queued_there(
    test_client, db
):
    company = _company(db)
    source = _sprint(db, company)
    target = _sprint(db, company, status="active", name="Sprint 1")
    _sprint(db, company, status="completed", name="Old")
    item = _item(db, source)

    targets = test_client.get(f"/v1/backlog/{item.id}/carry_targets").json()["sprints"]
    assert [t["id"] for t in targets] == [target.id]

    response = test_client.post(
        f"/v1/backlog/{item.id}/carry_over", json={"target_sprint_id": target.id}
    )

    assert response.status_code == 200, response.text
    body = response.json()
    assert (body["sprint_id"], body["queued"], body["created_sprint"]) == (
        target.id,
        True,
        False,
    )
    db.expire_all()
    clone = db.get(BacklogItem, body["backlog_item_id"])
    assert (clone.sprint_id, clone.status, clone.title) == (target.id, "todo", "Login")
    assert clone.comments[0]["text"].startswith(f"Carried over from task {item.id}")
    assert len(_assignments(db, clone.id)) == 1
    original = db.get(BacklogItem, item.id)
    assert carry_over.carried_over(original)
    assert _history(db, original) == [] and _history(db, clone) == []

    again = test_client.post(
        f"/v1/backlog/{item.id}/carry_over", json={"target_sprint_id": target.id}
    )
    assert again.status_code == 409
    assert "already carried" in again.json()["detail"]


def test_a_task_carried_into_a_planned_sprint_waits_for_it_to_start(test_client, db):
    company = _company(db)
    source = _sprint(db, company)
    target = _sprint(db, company, status="planned", name="Later")
    item = _item(db, source)

    body = test_client.post(
        f"/v1/backlog/{item.id}/carry_over", json={"target_sprint_id": target.id}
    ).json()

    assert (body["queued"], body["sprint_status"]) == (False, "planned")
    assert body["reason"] == "the sprint is planned: start it to run its tasks"


def test_without_a_target_the_task_gets_a_new_sprint(test_client, db):
    company = _company(db)
    source = _sprint(db, company, name="Hardening")
    item = _item(db, source)

    body = test_client.post(f"/v1/backlog/{item.id}/carry_over", json={}).json()

    assert (body["created_sprint"], body["queued"], body["sprint_status"]) == (
        True,
        True,
        "active",
    )
    db.expire_all()
    new = db.get(Sprint, body["sprint_id"])
    assert (new.company_id, new.sprint_name) == (company.id, "Carried from Hardening")


def test_a_todo_task_is_blocked_here_and_carried(test_client, db):
    company = _company(db)
    source = _sprint(db, company, status="paused")
    target = _sprint(db, company, status="active", name="Sprint 1")
    item = _item(
        db, source, status="todo", comments=[{"author": "Developer", "text": "draft"}]
    )

    body = test_client.post(
        f"/v1/backlog/{item.id}/carry_over", json={"target_sprint_id": target.id}
    ).json()

    assert body["queued"] is True
    db.expire_all()
    original = db.get(BacklogItem, item.id)
    assert original.status == "blocked" and carry_over.carried_over(original)
    # It stays for a person here even with a deliverable on its thread.
    assert carry_over.waits_for_a_person(original)
    assert _history(db, original) == []


@pytest.mark.parametrize(
    ("status", "target_status", "detail"),
    [
        ("done", "active", "Only a blocked or To do task"),
        ("blocked", "completed", "pick an open sprint"),
    ],
)
def test_carry_refusals(test_client, db, status, target_status, detail):
    company = _company(db)
    source = _sprint(db, company)
    target = _sprint(db, company, status=target_status, name="T")
    item = _item(db, source, status=status)

    response = test_client.post(
        f"/v1/backlog/{item.id}/carry_over", json={"target_sprint_id": target.id}
    )

    assert response.status_code == 409 and detail in response.json()["detail"]


def test_another_companys_sprint_is_not_a_target(test_client, db):
    source = _sprint(db, _company(db))
    foreign = _sprint(db, _company(db), status="active")
    item = _item(db, source)

    response = test_client.post(
        f"/v1/backlog/{item.id}/carry_over", json={"target_sprint_id": foreign.id}
    )

    assert response.status_code == 404


def _clones(session, target):
    session.expire_all()
    return session.exec(
        select(BacklogItem).where(BacklogItem.sprint_id == target.id)
    ).all()


def test_a_task_its_sprints_acceptance_tests_check_is_not_carried(test_client, db):
    company = _company(db)
    spec = Specification(company_id=company.id, title="Greeting", status="approved")
    db.add(spec)
    db.commit()
    source = _sprint(db, company)
    source.specification_id = spec.id
    db.add(source)
    db.commit()
    target = _sprint(db, company, status="active", name="Sprint 1")
    _item(
        db,
        source,
        status="done",
        comments=[],
        title="Acceptance tests: REQ-1",
        kind=acceptance.ACCEPTANCE_KIND,
        covers=["REQ-1.1"],
    )
    item = _item(db, source, covers=["REQ-1.1"])

    response = test_client.post(
        f"/v1/backlog/{item.id}/carry_over", json={"target_sprint_id": target.id}
    )

    assert response.status_code == 409, response.text
    assert "acceptance tests" in response.json()["detail"]
    assert _clones(db, target) == []
    assert not carry_over.carried_over(db.get(BacklogItem, item.id))


def test_a_todo_task_waiting_for_unfinished_tasks_is_not_carried(test_client, db):
    company = _company(db)
    source = _sprint(db, company, status="active")
    target = _sprint(db, company, status="active", name="Sprint 1")
    first = _item(db, source, status="in_progress", comments=[], title="Schema")
    item = _item(
        db, source, status="todo", comments=[], title="API", depends_on=[first.id]
    )

    response = test_client.post(
        f"/v1/backlog/{item.id}/carry_over", json={"target_sprint_id": target.id}
    )

    assert response.status_code == 409, response.text
    assert "not done" in response.json()["detail"]
    assert _clones(db, target) == []
    assert db.get(BacklogItem, item.id).status == "todo"


def test_a_carry_rereads_the_task_another_request_carried_meanwhile(db):
    company = _company(db)
    source = _sprint(db, company)
    target = _sprint(db, company, status="active", name="Sprint 1")
    item = _item(db, source)
    engine = db.get_bind()
    with Session(engine) as first, Session(engine) as second:
        # Both requests loaded the task before either carried it.
        stale = (second.get(BacklogItem, item.id), second.get(Sprint, source.id))
        board_moves.carry_to_sprint(
            first,
            first.get(BacklogItem, item.id),
            first.get(Sprint, source.id),
            target.id,
            "a",
        )
        with pytest.raises(board_moves.BoardMoveError) as refused:
            board_moves.carry_to_sprint(second, *stale, target.id, "b")
    assert refused.value.status == 409 and "already carried" in refused.value.detail
    assert len(_clones(db, target)) == 1


def test_a_carry_while_the_task_or_the_company_is_locked_is_refused(test_client, db):
    company = _company(db)
    source = _sprint(db, company)
    target = _sprint(db, company, status="active", name="Sprint 1")
    item = _item(db, source)
    engine = db.get_bind()
    url = f"/v1/backlog/{item.id}/carry_over"

    for name, ident in ((locks.TASK, item.id), (locks.SPRINT_CLOSE, company.id)):
        with locks.held(engine, name, ident) as mine:
            assert mine
            response = test_client.post(url, json={"target_sprint_id": target.id})
        assert response.status_code == 409, response.text
        assert "try again" in response.json()["detail"]
    assert _clones(db, target) == []

    assert (
        test_client.post(url, json={"target_sprint_id": target.id}).status_code == 200
    )


def test_the_target_is_read_again_under_the_companys_lock(db):
    company = _company(db)
    source = _sprint(db, company)
    target = _sprint(db, company, status="active", name="Sprint 1")
    item = _item(db, source)
    engine = db.get_bind()
    with Session(engine) as request, Session(engine) as worker:
        loaded = request.get(Sprint, target.id)  # the request saw it open
        assert loaded.status == "active"
        worker.get(Sprint, target.id).status = "completed"  # the worker closed it
        worker.commit()
        with pytest.raises(board_moves.BoardMoveError) as refused:
            board_moves.carry_to_sprint(
                request,
                request.get(BacklogItem, item.id),
                request.get(Sprint, source.id),
                target.id,
                "a",
            )
    assert refused.value.status == 409 and "completed" in refused.value.detail
    assert _clones(db, target) == []


# ── Who may move ─────────────────────────────────────────────────────────────


@pytest.fixture()
def auth_on(monkeypatch):
    import auth_tokens
    from config import settings

    monkeypatch.setattr(settings, "AUTH_REQUIRED", True)
    key = secrets.token_urlsafe(48)
    monkeypatch.setattr(auth_tokens, "_configured_key", lambda: key)
    return auth_tokens


def _member(session, auth_tokens, company_id, role):
    user = User(
        email=f"{role.lower()}-{secrets.token_hex(5)}@example.com",
        full_name=role,
        password_hash="x",
    )
    session.add(user)
    session.commit()
    session.add(CompanyMember(company_id=company_id, user_id=user.id, human_role=role))
    session.commit()
    token = auth_tokens.create_access_token(user_id=user.id, email=user.email)
    return {"Authorization": f"Bearer {token}"}


def test_a_viewer_neither_moves_nor_carries_and_a_manager_does(
    test_client, db, auth_on
):
    company = _company(db)
    source = _sprint(db, company)
    target = _sprint(db, company, status="active", name="Sprint 1")
    item = _item(db, source)
    viewer = _member(db, auth_on, company.id, "Viewer")
    manager = _member(db, auth_on, company.id, "Manager")
    url = f"/v1/backlog/{item.id}/carry_over"

    assert _move_as(test_client, item, viewer).status_code == 403
    assert (
        test_client.post(
            url, json={"target_sprint_id": target.id}, headers=viewer
        ).status_code
        == 403
    )
    assert (
        test_client.get(
            f"/v1/backlog/{item.id}/carry_targets", headers=viewer
        ).status_code
        == 200
    )
    db.expire_all()
    assert db.get(Sprint, source.id).status == "completed"

    response = test_client.post(
        url, json={"target_sprint_id": target.id}, headers=manager
    )
    assert response.status_code == 200, response.text


def test_a_managers_move_is_audited_with_their_email(test_client, db, auth_on):
    company = _company(db)
    sprint = _sprint(db, company)
    item = _item(db, sprint)
    manager = _member(db, auth_on, company.id, "Manager")

    assert _move_as(test_client, item, manager).json()["reopened"] is True
    (row,) = _reopen_rows(db, sprint.id)
    assert row.agent_id.startswith("human:manager-")


def _move_as(client, item, headers):
    return client.patch(
        f"/v1/backlog/{item.id}", json={"status": "todo"}, headers=headers
    )
