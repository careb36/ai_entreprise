"""Sending back a task blocked for triage from Slack or Teams (issue #512).

A task blocked until a person acts, without a risk hold, is posted to each
enabled chat connection with one action: send it back to work. A linked chat
user with the role the board asks for (Manager) sends it back exactly as the
board's "Send back to work" does: the same task event, written by the same
function (``services/loop/board_moves.send_back``), and the task queued
again. The request must carry a valid, recent signature and be seen once;
the account link and the role come from the record, never the payload. Every
refusal changes nothing and says why in chat.
"""

import secrets

import pytest
from chat_fakes import (
    TRIAGE_NOTE,
    deliver_pending,
    drain_chat_outbox,  # noqa: F401 - an autouse fixture
    slack_action,
    slack_request,
    teams_activity,
    teams_request,
    triage_task,
    world,
)
from chat_fakes import link as link_account
from config import settings
from models import (
    AuditLog,
    ChatApprovalMessage,
    ChatInteraction,
    OutboxEvent,
    RunReview,
    TaskEvent,
)
from routers.chat_interactions import LINK_FIRST, NEEDS_ROLE, ONLY_SEND_BACK
from services.integrations import chat_approvals
from services.integrations.chat_messages import APPROVE, SEND_BACK
from services.tasks import task_events
from services.tasks.board import SEND_BACK_MARKER
from sqlmodel import func, select


@pytest.fixture
def w(sqlite_session, monkeypatch):
    monkeypatch.setattr(settings, "AUTH_REQUIRED", True)
    return world(sqlite_session, monkeypatch, make_task=triage_task)


def _linked(session, w, role, provider="slack"):
    user, _ = w[role]
    chat_user = ("U" if provider == "slack" else "") + secrets.token_hex(5).upper()
    workspace = w["team_id"] if provider == "slack" else w["tenant"]
    link_account(session, w["company"].id, user, provider, workspace, chat_user)
    return chat_user


def _events(session, w, kind=None):
    query = select(TaskEvent).where(TaskEvent.task_id == w["item"].id)
    if kind:
        query = query.where(TaskEvent.event_type == kind)
    return session.exec(query.order_by(TaskEvent.id)).all()


def _assignments(session, w):
    rows = session.exec(
        select(OutboxEvent).where(OutboxEvent.event_type == "nats_publish")
    ).all()
    return [
        r
        for r in rows
        if r.payload.get("message", {}).get("backlog_item_id") == w["item"].id
    ]


def _ephemerals(session, w):
    rows = session.exec(
        select(OutboxEvent).where(OutboxEvent.event_type == chat_approvals.EPHEMERAL)
    ).all()
    return [
        r.payload["text"] for r in rows if r.payload["connection_id"] == w["slack"].id
    ]


def _unchanged(session, w):
    session.refresh(w["item"])
    assert w["item"].status == "blocked"
    assert _events(session, w, "sent_back") == []
    assert _assignments(session, w) == []


def _command(test_client, w, chat_user, text):
    return slack_request(
        test_client,
        w["slack"],
        w["slack_secret"],
        form={
            "command": "/fabrique",
            "text": text,
            "team_id": w["team_id"],
            "user_id": chat_user,
            "user_name": "ana",
            "trigger_id": secrets.token_hex(8),
        },
    )


# --- What is posted -------------------------------------------------------------


def test_a_task_blocked_for_triage_is_posted_with_only_send_back(sqlite_session, w):
    slack, teams = w["messages"]["slack"], w["messages"]["teams"]
    assert slack.status == teams.status == "posted"
    block = _events(sqlite_session, w, "blocked")[-1]
    assert slack.hold_event_id == teams.hold_event_id == block.id
    assert slack.head_hash == block.chain_hash

    posted = [p for m, p in w["fake"].slack if m == "chat.postMessage"][-1]
    blocks = str(posted["blocks"])
    assert SEND_BACK in blocks and APPROVE not in blocks
    assert "manual triage needed" in blocks and "verified" in blocks
    assert f"/fabrique sendback {slack.short_code}" in blocks
    assert "the agent says" not in blocks  # never the run's claims
    card = str(w["fake"].teams[-1])
    assert f"sendback {teams.short_code}" in card and "approve" not in card
    assert "the agent says" not in card


def test_a_blocked_task_the_worker_still_moves_is_not_posted(
    sqlite_session, monkeypatch
):
    monkeypatch.setattr(settings, "AUTH_REQUIRED", True)

    def retried(session, company_id):
        return triage_task(
            session, company_id, text="Agent error: the model timed out."
        )

    w = world(sqlite_session, monkeypatch, make_task=retried)

    assert w["messages"] == {}


def test_a_frozen_task_is_not_posted(sqlite_session, monkeypatch):
    monkeypatch.setattr(settings, "AUTH_REQUIRED", True)

    def frozen(session, company_id):
        return triage_task(
            session, company_id, text="Freeze after manual triage: an operator."
        )

    w = world(sqlite_session, monkeypatch, make_task=frozen)

    assert w["messages"] == {}


# --- Deciding ---------------------------------------------------------------------


def test_a_linked_manager_sends_it_back_from_slack_as_from_the_board(
    sqlite_session, test_client, w
):
    chat_user = _linked(sqlite_session, w, "manager")

    response = slack_request(
        test_client,
        w["slack"],
        w["slack_secret"],
        payload=slack_action(w, chat_user, SEND_BACK),
    )

    assert response.status_code == 200
    sqlite_session.refresh(w["item"])
    assert w["item"].status == "todo"
    (event,) = _events(sqlite_session, w, "sent_back")
    # The board's event: a person's send-back, the text starting with the
    # marker that restarts the rework budgets.
    assert (event.actor, event.task_status) == (task_events.PERSON, "todo")
    assert event.payload["author"] == "user"
    assert event.payload["text"].startswith(SEND_BACK_MARKER)
    assert "via Slack" in event.payload["text"]
    assert len(_assignments(sqlite_session, w)) == 1  # queued again at once
    # No run review: a triage block is not a delivery waiting for acceptance.
    assert (
        sqlite_session.exec(
            select(func.count(RunReview.id)).where(
                RunReview.agent_run_id == w["run"].id
            )
        ).one()
        == 0
    )
    manager, _ = w["manager"]
    audit = sqlite_session.exec(
        select(AuditLog).where(
            AuditLog.company_id == w["company"].id,
            AuditLog.action == "chat_approval_sent_back",
        )
    ).one()
    assert audit.agent_id == "human:Manager Person"
    assert audit.details["via"] == "slack"
    assert audit.details["chat_user"] == chat_user
    assert audit.details["user_id"] == manager.id
    assert audit.details["task_event_id"] == event.id
    message = sqlite_session.get(ChatApprovalMessage, w["messages"]["slack"].id)
    assert (message.status, message.decision) == ("decided", "send_back")
    sibling = sqlite_session.get(ChatApprovalMessage, w["messages"]["teams"].id)
    assert sibling.status == "stale" and "via Slack" in sibling.outcome

    assert deliver_pending(sqlite_session, {w["slack"].id}) == []
    method, payload = w["fake"].slack[-1]
    assert method == "chat.update" and payload["ts"] == message.message_ts
    assert "Sent back to work by Manager Person via Slack" in str(payload["blocks"])


def test_the_slack_command_sends_it_back_with_a_reason(sqlite_session, test_client, w):
    chat_user = _linked(sqlite_session, w, "manager")
    code = w["messages"]["slack"].short_code

    response = _command(
        test_client, w, chat_user, f"sendback {code} split the migration first"
    )

    assert response.json()["text"] == "Sent back to work by Manager Person via Slack."
    sqlite_session.refresh(w["item"])
    assert w["item"].status == "todo"
    (event,) = _events(sqlite_session, w, "sent_back")
    assert event.payload["text"].startswith(SEND_BACK_MARKER)
    assert event.payload["text"].endswith("split the migration first")


def test_teams_sendback_carries_the_reason(sqlite_session, test_client, w):
    chat_user = _linked(sqlite_session, w, "manager", "teams")
    code = w["messages"]["teams"].short_code

    response = teams_request(
        test_client,
        w["teams"],
        w["teams_key"],
        teams_activity(w, chat_user, f"sendback {code} retry with the new schema"),
    )

    assert response.json()["text"] == "Sent back to work by Manager Person via Teams."
    sqlite_session.refresh(w["item"])
    assert w["item"].status == "todo"
    assert "retry with the new schema" in w["item"].comments[-1]["text"]
    audit = sqlite_session.exec(
        select(AuditLog).where(AuditLog.action == "chat_approval_sent_back")
    ).all()[-1]
    assert audit.details["via"] == "teams"


def test_approve_is_refused_on_a_triage_block(sqlite_session, test_client, w):
    teams_user = _linked(sqlite_session, w, "manager", "teams")
    slack_user = _linked(sqlite_session, w, "manager")

    teams = teams_request(
        test_client,
        w["teams"],
        w["teams_key"],
        teams_activity(w, teams_user, f"approve {w['messages']['teams'].short_code}"),
    )
    slack_request(
        test_client,
        w["slack"],
        w["slack_secret"],
        payload=slack_action(w, slack_user, APPROVE),
    )

    assert teams.json()["text"] == ONLY_SEND_BACK
    assert ONLY_SEND_BACK in _ephemerals(sqlite_session, w)
    _unchanged(sqlite_session, w)
    message = sqlite_session.get(ChatApprovalMessage, w["messages"]["slack"].id)
    assert message.status == "posted"


# --- Refusals ---------------------------------------------------------------------


def test_a_forged_signature_is_refused_and_changes_nothing(
    sqlite_session, test_client, w
):
    slack_user = _linked(sqlite_session, w, "manager")
    teams_user = _linked(sqlite_session, w, "manager", "teams")
    before = sqlite_session.exec(select(func.count(ChatInteraction.id))).one()

    slack = slack_request(
        test_client,
        w["slack"],
        secrets.token_hex(16),  # another key
        payload=slack_action(w, slack_user, SEND_BACK),
    )
    command = slack_request(
        test_client,
        w["slack"],
        w["slack_secret"],
        form={
            "command": "/fabrique",
            "text": f"sendback {w['messages']['slack'].short_code} now",
            "team_id": w["team_id"],
            "user_id": slack_user,
            "trigger_id": secrets.token_hex(8),
        },
        signature="v0=" + "0" * 64,
    )
    teams = teams_request(
        test_client,
        w["teams"],
        w["teams_key"],
        teams_activity(
            w, teams_user, f"sendback {w['messages']['teams'].short_code} now"
        ),
        signature="AAAA",
    )

    assert slack.status_code == command.status_code == teams.status_code == 401
    _unchanged(sqlite_session, w)
    assert sqlite_session.exec(select(func.count(ChatInteraction.id))).one() == before


def test_a_replayed_send_back_acts_once(sqlite_session, test_client, w):
    chat_user = _linked(sqlite_session, w, "manager", "teams")
    activity = teams_activity(
        w, chat_user, f"sendback {w['messages']['teams'].short_code} once"
    )

    first = teams_request(test_client, w["teams"], w["teams_key"], activity)
    replay = teams_request(test_client, w["teams"], w["teams_key"], activity)

    assert first.json()["text"].startswith("Sent back to work")
    assert replay.json()["text"] == "Already handled."
    assert len(_events(sqlite_session, w, "sent_back")) == 1
    assert len(_assignments(sqlite_session, w)) == 1


def test_an_unlinked_chat_user_is_told_to_link_and_nothing_changes(
    sqlite_session, test_client, w
):
    response = _command(
        test_client,
        w,
        "U" + secrets.token_hex(5).upper(),
        f"sendback {w['messages']['slack'].short_code} please",
    )

    assert response.json()["text"] == LINK_FIRST
    _unchanged(sqlite_session, w)
    refused = sqlite_session.exec(
        select(AuditLog).where(
            AuditLog.company_id == w["company"].id,
            AuditLog.action == "chat_approval_refused",
        )
    ).one()
    assert refused.result["reason"] == LINK_FIRST


def test_a_viewer_is_refused_as_on_the_board(sqlite_session, test_client, w):
    chat_user = _linked(sqlite_session, w, "viewer")
    _, viewer_h = w["viewer"]

    board = test_client.patch(
        f"/v1/backlog/{w['item'].id}",
        json={"status": "todo", "comment": "Sent back to work by a person."},
        headers=viewer_h,
    )
    slack_request(
        test_client,
        w["slack"],
        w["slack_secret"],
        payload=slack_action(w, chat_user, SEND_BACK),
    )

    assert board.status_code == 403  # the board's rule, the same in chat
    _unchanged(sqlite_session, w)
    assert NEEDS_ROLE in _ephemerals(sqlite_session, w)
    message = sqlite_session.get(ChatApprovalMessage, w["messages"]["slack"].id)
    assert message.status == "posted"


def test_a_send_back_on_the_board_first_leaves_the_chat_message_stale(
    sqlite_session, test_client, w
):
    chat_user = _linked(sqlite_session, w, "manager")
    _, manager_h = w["manager"]
    board = test_client.patch(
        f"/v1/backlog/{w['item'].id}",
        json={"status": "todo", "comment": "Sent back to work by a person."},
        headers=manager_h,
    )
    assert board.status_code == 200
    # The board wrote the same kind of event the chat would.
    (event,) = _events(sqlite_session, w, "sent_back")
    assert event.actor == task_events.PERSON

    slack_request(
        test_client,
        w["slack"],
        w["slack_secret"],
        payload=slack_action(w, chat_user, SEND_BACK),
    )

    assert len(_events(sqlite_session, w, "sent_back")) == 1
    message = sqlite_session.get(ChatApprovalMessage, w["messages"]["slack"].id)
    assert message.status == "stale"
    assert any(
        "changed since this message" in t for t in _ephemerals(sqlite_session, w)
    )


def test_the_record_note_is_the_block_reason(sqlite_session, w):
    item = w["item"]
    point = chat_approvals.current_point(sqlite_session, item, w["company"].id)
    assert point["kind"] == chat_approvals.TRIAGE
    assert point["hold_note"] == TRIAGE_NOTE
