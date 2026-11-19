"""Fake Slack and Teams for the chat approval tests: no network, keys made at test time.

``FakeChat`` replaces the HTTP adapter (``services/integrations/chat_client``)
and records every call; ``world`` builds a company with Owner, Manager and
Viewer accounts, both connections, and a delivery held for a person's
acceptance with its run and the Verifier's verdict. ``slack_request`` and
``teams_request`` sign a body the way each platform does.
"""

from __future__ import annotations

import asyncio
import base64
import hashlib
import json
import secrets
import string
import time
import uuid
from datetime import UTC, datetime
from urllib.parse import urlencode

import auth_tokens
import crypto
import pytest
from cryptography.fernet import Fernet
from models import (
    AgentRun,
    BacklogItem,
    ChatApprovalMessage,
    Company,
    CompanyMember,
    OutboxEvent,
    Sprint,
    User,
    Verification,
)
from models.agent_run import TASK_WORKER_PRODUCER
from services.integrations import (
    chat_approvals,
    chat_client,
    chat_connections,
    chat_links,
    webhooks,
)
from services.integrations.chat_signatures import slack_signature, teams_signature
from services.tasks import task_events
from services.tasks.board import RISK_HOLD_PREFIX
from sqlalchemy import delete
from sqlmodel import select

AGENT_CLAIM = "the agent says every test passed"


@pytest.fixture(autouse=True)
def drain_chat_outbox(sqlite_session):
    """Remove the chat rows these tests leave in the shared engine's outbox.

    Other modules read every outbox row and expect NATS payloads.
    """
    yield
    sqlite_session.rollback()
    sqlite_session.exec(
        delete(OutboxEvent).where(
            OutboxEvent.event_type.in_(chat_approvals.EVENT_TYPES)  # type: ignore[union-attr]
        )
    )
    sqlite_session.commit()


def _ident(prefix: str) -> str:
    return prefix + "".join(
        secrets.choice(string.ascii_uppercase + string.digits) for _ in range(9)
    )


class FakeChat:
    """Stands in for Slack's Web API and a Teams incoming webhook."""

    def __init__(self, team_id: str):
        self.team_id = team_id
        self.slack: list[tuple[str, dict]] = []
        self.teams: list[dict] = []
        self.fail_next = 0
        self.teams_status = 200

    async def slack_call(self, method, token, payload, timeout):
        if self.fail_next:
            self.fail_next -= 1
            raise chat_client.ChatPostError("ConnectError: no route")
        self.slack.append((method, payload))
        if method == "auth.test":
            return {"ok": True, "team_id": self.team_id, "user_id": "UBOT"}
        return {
            "ok": True,
            "ts": f"{time.time():.6f}",
            "channel": payload.get("channel"),
        }

    async def teams_post(self, url, card, timeout):
        if self.fail_next:
            self.fail_next -= 1
            raise chat_client.ChatPostError("ConnectError posting to Teams")
        self.teams.append(card)
        return self.teams_status


def setup(monkeypatch, team_id: str) -> FakeChat:
    key = Fernet.generate_key().decode()
    monkeypatch.setattr(crypto, "_configured_key", lambda: key)
    monkeypatch.setattr(webhooks, "_public_address", lambda host: True)
    jwt_key = secrets.token_urlsafe(48)
    monkeypatch.setattr(auth_tokens, "_configured_key", lambda: jwt_key)
    fake = FakeChat(team_id)
    monkeypatch.setattr(chat_client, "slack_call", fake.slack_call)
    monkeypatch.setattr(chat_client, "teams_post", fake.teams_post)
    chat_approvals.install()
    return fake


def member(session, company_id, role):
    user = User(
        email=f"{role.lower()}-{secrets.token_hex(6)}@example.com",
        full_name=f"{role} Person",
        password_hash="unused",
    )
    session.add(user)
    session.commit()
    session.add(CompanyMember(company_id=company_id, user_id=user.id, human_role=role))
    session.commit()
    token = auth_tokens.create_access_token(user_id=user.id, email=user.email)
    return user, {"Authorization": f"Bearer {token}"}


def link(session, company_id, user, provider, workspace_id, chat_user):
    code = chat_links.issue_code(session, company_id, user.id, provider)["code"]
    chat_links.claim(
        session,
        company_id=company_id,
        provider=provider,
        workspace_id=workspace_id,
        external_user_id=chat_user,
        display_name=None,
        code=code,
    )
    session.commit()


def _task_with_run(session, company_id):
    """A task in progress with its in-house run and the Verifier's verdict."""
    sprint = Sprint(company_id=company_id, sprint_name="Sprint 1", status="active")
    session.add(sprint)
    session.commit()
    item = BacklogItem(
        sprint_id=sprint.id,
        title="Encrypt stored credentials",
        status="in_progress",
        assignee="Developer",
        comments=[],
    )
    session.add(item)
    session.commit()
    artifact = "def encrypt(v):\n    return v\n"
    run = AgentRun(
        company_id=company_id,
        external_ref=f"task-{item.id}",
        producer=TASK_WORKER_PRODUCER,
        subject={"sprint_id": sprint.id, "backlog_item_id": item.id},
        claims={"summary": AGENT_CLAIM},
        artifact_text=artifact,
        artifact_digest=hashlib.sha256(artifact.encode()).hexdigest(),
        artifact_size=len(artifact),
    )
    session.add(run)
    session.commit()
    session.add(
        Verification(
            company_id=company_id,
            agent_run_id=run.id,
            verdict="verified",
            checks=[
                {"name": "tests", "outcome": "pass"},
                {"name": "standards", "outcome": "refuted"},
            ],
        )
    )
    return item, run


def held_task(session, company_id):
    """A delivery QA approved that waits for a person, with its run and verdict."""
    item, run = _task_with_run(session, company_id)
    task_events.record(
        session,
        item,
        "reviewed",
        "QA",
        author="QA",
        status="blocked",
        text="APPROVED: good",
        payload={"verdict": "approved"},
    )
    task_events.record(
        session,
        item,
        "risk_hold",
        "system",
        text=f"{RISK_HOLD_PREFIX} (path app/auth.py). Mark it done to accept it, "
        "or send it back to work.",
    )
    session.commit()
    return item, run


def deliver_pending(session, connection_ids, kinds=chat_approvals.EVENT_TYPES):
    """Run the outbox's chat rows for *connection_ids* once, as the processor would.

    The errors raised are returned. Rows of other tests' connections (the
    engine is shared) are left alone.
    """
    errors = []
    rows = session.exec(
        select(OutboxEvent).where(
            OutboxEvent.event_type.in_(kinds),  # type: ignore[union-attr]
            OutboxEvent.status == "pending",
        )
    ).all()
    for row in rows:
        if row.payload.get("connection_id") not in connection_ids:
            continue
        try:
            asyncio.run(
                chat_approvals.deliver(session, row.event_type, row.payload, row.id)
            )
            row.status = "completed"
        except Exception as exc:  # noqa: BLE001 - Returned for the test to inspect.
            errors.append(exc)
            row.retry_count = (row.retry_count or 0) + 1
        session.add(row)
    session.commit()
    return errors


TRIAGE_NOTE = "Blocked: the gate refused the delivery twice; manual triage needed."


def triage_task(session, company_id, text=TRIAGE_NOTE):
    """A task blocked for a person's triage, without a risk hold."""
    item, run = _task_with_run(session, company_id)
    task_events.record(
        session, item, "blocked", task_events.SYSTEM, status="blocked", text=text
    )
    session.commit()
    return item, run


def world(session, monkeypatch, make_task=held_task):
    team_id = _ident("T")
    fake = setup(monkeypatch, team_id)
    company = Company(company_name=f"Chat {secrets.token_hex(3)}")
    session.add(company)
    session.commit()
    owner, owner_h = member(session, company.id, "Owner")
    manager, manager_h = member(session, company.id, "Manager")
    viewer, viewer_h = member(session, company.id, "Viewer")
    slack_secret = secrets.token_hex(16)
    teams_key = base64.b64encode(secrets.token_bytes(32)).decode()
    tenant = str(uuid.uuid4())
    slack, _ = chat_connections.save(
        session,
        company.id,
        "slack",
        workspace_id=team_id,
        channel=_ident("C"),
        post_credential="xoxb-" + secrets.token_hex(12),
        signing_secret=slack_secret,
        enabled=True,
        actor=owner.email,
    )
    teams, _ = chat_connections.save(
        session,
        company.id,
        "teams",
        workspace_id=tenant,
        channel=None,
        post_credential=f"https://fabrique-test.webhook.office.com/webhookb2/{secrets.token_hex(8)}",
        signing_secret=teams_key,
        enabled=True,
        actor=owner.email,
    )
    item, run = make_task(session, company.id)
    errors = deliver_pending(session, {slack.id, teams.id})
    assert errors == [], errors
    messages = {
        m.provider: m
        for m in session.exec(
            select(ChatApprovalMessage).where(ChatApprovalMessage.task_id == item.id)
        ).all()
    }
    return {
        "fake": fake,
        "company": company,
        "owner": (owner, owner_h),
        "manager": (manager, manager_h),
        "viewer": (viewer, viewer_h),
        "slack": slack,
        "teams": teams,
        "slack_secret": slack_secret,
        "teams_key": teams_key,
        "team_id": team_id,
        "tenant": tenant,
        "item": item,
        "run": run,
        "messages": messages,
    }


def slack_request(
    client, row, secret, *, payload=None, form=None, ts=None, signature=None
):
    body = urlencode({"payload": json.dumps(payload)} if payload is not None else form)
    ts = ts or str(int(time.time()))
    signature = signature or slack_signature(secret, ts, body.encode())
    return client.post(
        f"/v1/integrations/slack/{row.public_id}/interactions",
        content=body,
        headers={
            "Content-Type": "application/x-www-form-urlencoded",
            "X-Slack-Request-Timestamp": ts,
            "X-Slack-Signature": signature,
        },
    )


def slack_action(w, chat_user, action_id, value=None, team=None):
    message = w["messages"]["slack"]
    from services.integrations.chat_messages import button_value

    return {
        "type": "block_actions",
        "trigger_id": secrets.token_hex(8),
        "team": {"id": team or w["team_id"]},
        "user": {"id": chat_user},
        "channel": {"id": message.channel},
        "actions": [
            {
                "action_id": action_id,
                "value": value
                or button_value(message.id, message.head_hash, message.run_version),
            }
        ],
    }


def teams_activity(w, chat_user, text, *, when=None, tenant=None):
    return {
        "type": "message",
        "id": secrets.token_hex(8),
        "timestamp": (when or datetime.now(UTC)).isoformat().replace("+00:00", "Z"),
        "text": f"<at>Fabrique</at> {text}",
        "from": {
            "id": "29:" + secrets.token_hex(4),
            "aadObjectId": chat_user,
            "name": "A person",
        },
        "channelData": {"tenant": {"id": tenant or w["tenant"]}},
    }


def teams_request(client, row, key, activity, *, signature=None):
    body = json.dumps(activity).encode()
    signature = signature or teams_signature(key, body)
    return client.post(
        f"/v1/integrations/teams/{row.public_id}/messages",
        content=body,
        headers={
            "Content-Type": "application/json",
            "Authorization": f"HMAC {signature}",
        },
    )
