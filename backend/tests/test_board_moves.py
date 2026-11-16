"""A card a person moves to To do on the board (``services/loop/board_moves.py``).

- Moving a task to To do starts it: the move is a send-back (the rework
  budgets restart), a ``completed`` or ``cancelled`` sprint is reopened and
  audited, a ``planned`` or ``paused`` one is not started and the answer says
  why, an archived one is refused, and the task is queued at once when its
  dependencies are done; a duplicate assignment is claimed only once.
- A reopened sprint closes again without a second follow-up, retrospective
  or procedure round.
- A Viewer neither moves nor carries, and a Manager's move is audited.
- Every history these paths write passes ``history_check``.

"Move to sprint…" is in ``test_board_moves_carry.py``; the shared rows are in
``board_moves_helpers``.
"""

import asyncio
import secrets
from unittest.mock import patch

import pytest
from models import (
    AgentMessage,
    AuditLog,
    BacklogItem,
    Sprint,
    TaskEvent,
)
from models.company import CompanyMember, User
from services.loop import board_moves, replicas, sprint_close
from services.loop.worker import close as worker_close
from services.loop.worker import procedures_round
from services.tasks.board import is_send_back, rework_requests_by
from sqlmodel import select

import board_moves_helpers
from board_moves_helpers import (
    NEEDS_WORK,  # noqa: F401 - imported from here by other tests
    TRIAGE,
    _assignments,
    _company,
    _history,
    _item,
    _sprint,
)
from test_review_gate import _FakeNATS

# The shared fixtures.
db = board_moves_helpers.db


def _reopen_rows(session, sprint_id):
    return session.exec(
        select(AuditLog).where(
            AuditLog.interaction_id == f"sprint:{sprint_id}:admin",
            AuditLog.action == board_moves.REOPEN_ACTION,
        )
    ).all()


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
    from services.platform import engram_processor

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
