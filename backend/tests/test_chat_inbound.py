"""Decisions from Slack and Teams (issue #512, phase 4; docs/design/CHAT_APPROVALS.md).

A click or a command decides only when the platform's signature holds (and
is recent), the interaction was never seen, the chat user is linked to an
account with the role the web asks for, and the message still matches the
record; then the web's own review function decides, the audit names the
person and the platform, and the message is updated. Every refusal changes
nothing and says why in chat.
"""

import secrets
import time
from datetime import UTC, datetime, timedelta

import pytest
from chat_fakes import (
    drain_chat_outbox,  # noqa: F401 - an autouse fixture
    slack_action,
    slack_request,
    teams_activity,
    teams_request,
    world,
)
from chat_fakes import link as link_account
from config import settings
from models import (
    AuditLog,
    ChatApprovalMessage,
    ChatIdentity,
    ChatInteraction,
    OutboxEvent,
    RunReview,
)
from models.agent_run import TASK_WORKER_PRODUCER, AgentRun
from routers.chat_interactions import LINK_FIRST, NEEDS_ROLE
from services.integrations import chat_approvals, chat_links
from services.integrations.chat_messages import APPROVE, SEND_BACK
from sqlmodel import func, select


@pytest.fixture
def w(sqlite_session, monkeypatch):
    monkeypatch.setattr(settings, "AUTH_REQUIRED", True)
    return world(sqlite_session, monkeypatch)


def _linked(session, w, role, provider="slack"):
    user, _ = w[role]
    chat_user = ("U" if provider == "slack" else "") + secrets.token_hex(5).upper()
    workspace = w["team_id"] if provider == "slack" else w["tenant"]
    link_account(session, w["company"].id, user, provider, workspace, chat_user)
    return chat_user


def _reviews(session, run):
    return session.exec(
        select(func.count(RunReview.id)).where(RunReview.agent_run_id == run.id)
    ).one()


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
    assert _reviews(session, w["run"]) == 0


def test_the_hold_was_posted_to_both_platforms_with_its_head(sqlite_session, w):
    slack, teams = w["messages"]["slack"], w["messages"]["teams"]
    assert slack.status == teams.status == "posted"
    assert slack.message_ts and slack.head_hash and teams.short_code
    assert slack.run_version == teams.run_version == w["run"].version


def test_a_linked_manager_approves_from_slack_as_on_the_web(
    sqlite_session, test_client, w
):
    chat_user = _linked(sqlite_session, w, "manager")

    response = slack_request(
        test_client,
        w["slack"],
        w["slack_secret"],
        payload=slack_action(w, chat_user, APPROVE),
    )

    assert response.status_code == 200
    sqlite_session.refresh(w["item"])
    assert w["item"].status == "done"
    review = sqlite_session.exec(
        select(RunReview).where(RunReview.agent_run_id == w["run"].id)
    ).one()
    manager, _ = w["manager"]
    assert (review.event, review.outcome, review.author_user_id) == (
        "approve",
        "accepted",
        manager.id,
    )
    audits = {
        a.action: a
        for a in sqlite_session.exec(
            select(AuditLog).where(AuditLog.company_id == w["company"].id)
        ).all()
    }
    assert audits["run_review_approve"].details["via"] == "slack"
    assert audits["run_review_approve"].details["user_id"] == manager.id
    assert audits["run_review_approve"].agent_id == "human:Manager Person"
    decided = audits["chat_approval_accepted"]
    assert (
        decided.details["via"] == "slack" and decided.details["chat_user"] == chat_user
    )
    message = sqlite_session.get(ChatApprovalMessage, w["messages"]["slack"].id)
    assert (message.status, message.decided_by_user_id) == ("decided", manager.id)
    # The same hold in Teams now says what happened instead of waiting.
    sibling = sqlite_session.get(ChatApprovalMessage, w["messages"]["teams"].id)
    assert sibling.status == "stale" and "via Slack" in sibling.outcome
    # The message is edited through the outbox: no buttons, the outcome.
    from chat_fakes import deliver_pending

    assert deliver_pending(sqlite_session, {w["slack"].id}) == []
    method, payload = w["fake"].slack[-1]
    assert method == "chat.update" and payload["ts"] == message.message_ts
    assert "Approved by Manager Person via Slack" in str(payload["blocks"])
    assert "actions" not in str(payload["blocks"])


def test_a_bad_or_missing_signature_is_refused_and_changes_nothing(
    sqlite_session, test_client, w
):
    chat_user = _linked(sqlite_session, w, "manager")
    action = slack_action(w, chat_user, APPROVE)

    forged = slack_request(
        test_client, w["slack"], secrets.token_hex(16), payload=action
    )
    missing = test_client.post(
        f"/v1/integrations/slack/{w['slack'].public_id}/interactions",
        content="payload=%7B%7D",
        headers={"Content-Type": "application/x-www-form-urlencoded"},
    )
    control = slack_request(test_client, w["slack"], w["slack_secret"], payload=action)
    assert control.status_code == 200  # the right key works

    assert forged.status_code == 401 and missing.status_code == 401
    assert (
        test_client.post(
            "/v1/integrations/slack/nope/interactions", content="x"
        ).status_code
        == 404
    )


def test_a_forged_signature_leaves_the_task_and_the_replay_store_alone(
    sqlite_session, test_client, w
):
    chat_user = _linked(sqlite_session, w, "manager")
    before = sqlite_session.exec(select(func.count(ChatInteraction.id))).one()

    forged = slack_request(
        test_client,
        w["slack"],
        w["slack_secret"],
        payload=slack_action(w, chat_user, APPROVE),
        signature="v0=" + "0" * 64,
    )

    assert forged.status_code == 401
    _unchanged(sqlite_session, w)
    assert sqlite_session.exec(select(func.count(ChatInteraction.id))).one() == before


def test_a_stale_timestamp_is_refused(sqlite_session, test_client, w):
    chat_user = _linked(sqlite_session, w, "manager")
    old = str(int(time.time()) - 301)

    response = slack_request(
        test_client,
        w["slack"],
        w["slack_secret"],
        payload=slack_action(w, chat_user, APPROVE),
        ts=old,
    )

    assert response.status_code == 401
    _unchanged(sqlite_session, w)


def test_a_replayed_interaction_acts_once(sqlite_session, test_client, w):
    chat_user = _linked(sqlite_session, w, "manager")
    action = slack_action(w, chat_user, SEND_BACK)
    ts = str(int(time.time()))

    first = slack_request(
        test_client, w["slack"], w["slack_secret"], payload=action, ts=ts
    )
    again = slack_request(
        test_client, w["slack"], w["slack_secret"], payload=action, ts=ts
    )

    assert first.status_code == again.status_code == 200
    assert _reviews(sqlite_session, w["run"]) == 1
    sqlite_session.refresh(w["item"])
    assert w["item"].status == "todo"


def test_an_unlinked_chat_user_is_told_to_link_and_nothing_changes(
    sqlite_session, test_client, w
):
    response = slack_request(
        test_client,
        w["slack"],
        w["slack_secret"],
        payload=slack_action(w, "U" + secrets.token_hex(5).upper(), APPROVE),
    )

    assert response.status_code == 200
    _unchanged(sqlite_session, w)
    assert LINK_FIRST in _ephemerals(sqlite_session, w)
    refused = sqlite_session.exec(
        select(AuditLog).where(
            AuditLog.company_id == w["company"].id,
            AuditLog.action == "chat_approval_refused",
        )
    ).one()
    assert refused.result["reason"] == LINK_FIRST


def test_a_viewer_is_refused_as_on_the_web(sqlite_session, test_client, w):
    chat_user = _linked(sqlite_session, w, "viewer")

    slack_request(
        test_client,
        w["slack"],
        w["slack_secret"],
        payload=slack_action(w, chat_user, APPROVE),
    )

    _unchanged(sqlite_session, w)
    assert NEEDS_ROLE in _ephemerals(sqlite_session, w)
    message = sqlite_session.get(ChatApprovalMessage, w["messages"]["slack"].id)
    assert message.status == "posted"


def test_a_role_or_user_in_the_payload_is_ignored(sqlite_session, test_client, w):
    viewer_chat = _linked(sqlite_session, w, "viewer")
    manager, _ = w["manager"]
    action = slack_action(w, viewer_chat, APPROVE)
    action["user"].update({"role": "Owner", "fabrique_user_id": manager.id})

    slack_request(test_client, w["slack"], w["slack_secret"], payload=action)

    _unchanged(sqlite_session, w)


def test_another_workspace_is_refused_even_with_a_linked_id(
    sqlite_session, test_client, w
):
    chat_user = _linked(sqlite_session, w, "manager")

    slack_request(
        test_client,
        w["slack"],
        w["slack_secret"],
        payload=slack_action(w, chat_user, APPROVE, team="T" + "9" * 9),
    )

    _unchanged(sqlite_session, w)


def test_a_new_delivery_since_the_message_makes_it_stale(
    sqlite_session, test_client, w
):
    chat_user = _linked(sqlite_session, w, "manager")
    run = w["run"]
    sqlite_session.add(
        AgentRun(
            company_id=run.company_id,
            external_ref=run.external_ref,
            version=run.version + 1,
            producer=TASK_WORKER_PRODUCER,
            subject=run.subject,
            artifact_digest=run.artifact_digest,
            artifact_size=run.artifact_size,
        )
    )
    sqlite_session.commit()

    slack_request(
        test_client,
        w["slack"],
        w["slack_secret"],
        payload=slack_action(w, chat_user, APPROVE),
    )

    _unchanged(sqlite_session, w)
    message = sqlite_session.get(ChatApprovalMessage, w["messages"]["slack"].id)
    assert message.status == "stale"
    assert any(
        "changed since this message" in t for t in _ephemerals(sqlite_session, w)
    )


def test_a_button_whose_head_does_not_match_is_refused(sqlite_session, test_client, w):
    chat_user = _linked(sqlite_session, w, "manager")
    message = w["messages"]["slack"]
    tampered = f"{message.id}:{'0' * 16}:{message.run_version}"

    slack_request(
        test_client,
        w["slack"],
        w["slack_secret"],
        payload=slack_action(w, chat_user, APPROVE, value=tampered),
    )

    _unchanged(sqlite_session, w)


def test_a_decision_taken_on_the_web_first_leaves_the_chat_button_stale(
    sqlite_session, test_client, w
):
    chat_user = _linked(sqlite_session, w, "manager")
    _, manager_h = w["manager"]
    web = test_client.post(
        f"/v1/agent_runs/{w['run'].id}/reviews",
        json={"company_id": w["company"].id, "event": "approve"},
        headers=manager_h,
    )
    assert web.status_code == 201

    slack_request(
        test_client,
        w["slack"],
        w["slack_secret"],
        payload=slack_action(w, chat_user, SEND_BACK),
    )

    sqlite_session.refresh(w["item"])
    assert w["item"].status == "done"
    assert _reviews(sqlite_session, w["run"]) == 1


def test_slack_link_codes_are_single_use_and_expire(sqlite_session, test_client, w):
    manager, _ = w["manager"]
    code = chat_links.issue_code(sqlite_session, w["company"].id, manager.id, "slack")[
        "code"
    ]

    def command(text, user):
        return slack_request(
            test_client,
            w["slack"],
            w["slack_secret"],
            form={
                "command": "/fabrique",
                "text": text,
                "team_id": w["team_id"],
                "user_id": user,
                "user_name": "ana",
                "trigger_id": secrets.token_hex(8),
            },
        )

    linked = command(f"link {code}", "UFIRST0001")
    reused = command(f"link {code}", "USECOND002")

    assert linked.json()["text"].startswith("Linked")
    assert "not valid" in reused.json()["text"]
    identity = sqlite_session.exec(
        select(ChatIdentity).where(ChatIdentity.user_id == manager.id)
    ).one()
    assert identity.external_user_id == "UFIRST0001"

    expired = chat_links.issue_code(
        sqlite_session, w["company"].id, manager.id, "slack"
    )
    from models import ChatLinkCode

    row = sqlite_session.exec(select(ChatLinkCode)).one()
    row.expires_at = datetime.now(UTC).replace(tzinfo=None) - timedelta(minutes=1)
    sqlite_session.add(row)
    sqlite_session.commit()
    assert "expired" in command(f"link {expired['code']}", "UFIRST0001").json()["text"]


def test_a_linked_manager_approves_from_teams_with_the_short_code(
    sqlite_session, test_client, w
):
    chat_user = _linked(sqlite_session, w, "manager", "teams")
    code = w["messages"]["teams"].short_code

    response = teams_request(
        test_client,
        w["teams"],
        w["teams_key"],
        teams_activity(w, chat_user, f"approve {code}"),
    )

    assert response.status_code == 200
    assert response.json()["text"] == "Approved by Manager Person via Teams."
    sqlite_session.refresh(w["item"])
    assert w["item"].status == "done"
    audit = sqlite_session.exec(
        select(AuditLog).where(AuditLog.action == "run_review_approve")
    ).all()[-1]
    assert audit.details["via"] == "teams"


def test_teams_sendback_carries_the_reason_to_the_developer(
    sqlite_session, test_client, w
):
    chat_user = _linked(sqlite_session, w, "manager", "teams")
    code = w["messages"]["teams"].short_code

    teams_request(
        test_client,
        w["teams"],
        w["teams_key"],
        teams_activity(w, chat_user, f"sendback {code} rotate the key before merging"),
    )

    sqlite_session.refresh(w["item"])
    assert w["item"].status == "todo"
    assert "rotate the key before merging" in w["item"].comments[-1]["text"]


def test_teams_refuses_a_bad_hmac_a_stale_activity_and_a_replay(
    sqlite_session, test_client, w
):
    chat_user = _linked(sqlite_session, w, "viewer", "teams")
    code = w["messages"]["teams"].short_code
    activity = teams_activity(w, chat_user, f"approve {code}")

    forged = teams_request(
        test_client, w["teams"], w["teams_key"], activity, signature="AAAA"
    )
    stale = teams_request(
        test_client,
        w["teams"],
        w["teams_key"],
        teams_activity(
            w,
            chat_user,
            f"approve {code}",
            when=datetime.now(UTC) - timedelta(minutes=6),
        ),
    )
    first = teams_request(test_client, w["teams"], w["teams_key"], activity)
    replay = teams_request(test_client, w["teams"], w["teams_key"], activity)

    assert forged.status_code == stale.status_code == 401
    assert first.json()["text"] == NEEDS_ROLE  # a Viewer, refused
    assert replay.json()["text"] == "Already handled."
    _unchanged(sqlite_session, w)


def test_teams_link_and_another_tenant(sqlite_session, test_client, w):
    viewer, _ = w["viewer"]
    code = chat_links.issue_code(sqlite_session, w["company"].id, viewer.id, "teams")[
        "code"
    ]
    chat_user = secrets.token_hex(6)

    other = teams_request(
        test_client,
        w["teams"],
        w["teams_key"],
        teams_activity(
            w, chat_user, f"link {code}", tenant="00000000-0000-0000-0000-000000000000"
        ),
    )
    linked = teams_request(
        test_client,
        w["teams"],
        w["teams_key"],
        teams_activity(w, chat_user, f"link {code}"),
    )

    assert "not the one connected" in other.json()["text"]
    assert linked.json()["text"].startswith("Linked")


def test_a_claim_left_unfinished_is_taken_again_after_the_timeout(
    sqlite_session, test_client, w
):
    chat_user = _linked(sqlite_session, w, "manager")
    message = sqlite_session.get(ChatApprovalMessage, w["messages"]["slack"].id)
    message.status = "deciding"
    message.decided_at = datetime.now(UTC).replace(tzinfo=None)
    sqlite_session.add(message)
    sqlite_session.commit()

    slack_request(
        test_client,
        w["slack"],
        w["slack_secret"],
        payload=slack_action(w, chat_user, APPROVE),
    )
    _unchanged(sqlite_session, w)  # another claim is still fresh

    message.decided_at = datetime.now(UTC).replace(tzinfo=None) - timedelta(minutes=3)
    sqlite_session.add(message)
    sqlite_session.commit()
    slack_request(
        test_client,
        w["slack"],
        w["slack_secret"],
        payload=slack_action(w, chat_user, APPROVE),
    )

    sqlite_session.refresh(w["item"])
    assert w["item"].status == "done"


def test_replay_keys_older_than_any_valid_request_are_dropped(
    sqlite_session, test_client, w
):
    old = ChatInteraction(
        provider="slack",
        interaction_key=f"action:{secrets.token_hex(8)}",
        received_at=datetime.now(UTC).replace(tzinfo=None) - timedelta(hours=2),
    )
    sqlite_session.add(old)
    sqlite_session.commit()
    old_key = old.interaction_key

    slack_request(
        test_client,
        w["slack"],
        w["slack_secret"],
        payload=slack_action(w, "U" + secrets.token_hex(5).upper(), APPROVE),
    )

    sqlite_session.expire_all()
    kept = sqlite_session.exec(
        select(ChatInteraction).where(ChatInteraction.interaction_key == old_key)
    ).first()
    assert kept is None  # (SQLite may reuse its id, so look it up by key)
