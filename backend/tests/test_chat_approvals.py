"""Posting approvals to Slack and Teams, and their settings (issue #512, phase 4).

A risk hold queues one post per enabled connection in its own transaction;
the post shows the Verifier's verdict and the checks that did not pass, never
the agent's claims; a failed post is recorded and raised for the outbox to
retry, and the retry posts once. The connection is an Owner's: credentials
encrypted, never returned, every change audited; each member links only
their own account.
"""

import base64
import secrets
import uuid

import pytest
from chat_fakes import (
    AGENT_CLAIM,
    deliver_pending,
    drain_chat_outbox,  # noqa: F401 - an autouse fixture
    held_task,
    link,
    member,
    setup,
    world,
)
from config import settings
from models import (
    AuditLog,
    ChatApprovalMessage,
    ChatConnection,
    Company,
    OutboxEvent,
)
from services.integrations import chat_approvals, chat_connections
from services.integrations.chat_signatures import (
    SignatureRefused,
    check_activity_time,
    slack_signature,
    teams_signature,
    verify_slack,
    verify_teams,
)
from sqlmodel import select


@pytest.fixture
def auth(monkeypatch):
    monkeypatch.setattr(settings, "AUTH_REQUIRED", True)


# ---------------------------------------------------------------------------
# Signatures (pure)
# ---------------------------------------------------------------------------


def test_slack_signature_valid_wrong_stale_and_missing():
    secret, body, now = secrets.token_hex(16), b"payload=%7B%7D", 1_800_000_000
    ts = str(now)
    good = slack_signature(secret, ts, body)

    verify_slack(secret, ts, good, body, now=now)
    for args, reason in (
        ((secrets.token_hex(16), ts, good, body), "mismatch"),
        ((secret, ts, good, body + b"x"), "mismatch"),
        (
            (
                secret,
                str(now - 301),
                slack_signature(secret, str(now - 301), body),
                body,
            ),
            "stale",
        ),
        (
            (
                secret,
                str(now + 301),
                slack_signature(secret, str(now + 301), body),
                body,
            ),
            "stale",
        ),
        ((secret, None, good, body), "missing"),
        ((None, ts, good, body), "no signing secret"),
    ):
        with pytest.raises(SignatureRefused, match=reason):
            verify_slack(*args, now=now)


def test_teams_signature_valid_wrong_and_malformed():
    key = base64.b64encode(secrets.token_bytes(32)).decode()
    body = b'{"type":"message"}'
    good = teams_signature(key, body)

    verify_teams(key, f"HMAC {good}", body)
    other = base64.b64encode(secrets.token_bytes(32)).decode()
    for header in (
        f"HMAC {teams_signature(other, body)}",
        f"Bearer {good}",
        "",
        "HMAC ",
    ):
        with pytest.raises(SignatureRefused):
            verify_teams(key, header, body)
    with pytest.raises(SignatureRefused, match="stale"):
        check_activity_time("2020-01-01T00:00:00.0000000Z")
    with pytest.raises(SignatureRefused):
        check_activity_time(None)


# ---------------------------------------------------------------------------
# Outbound
# ---------------------------------------------------------------------------


def test_a_hold_posts_the_verdict_and_failing_checks_never_the_claims(
    sqlite_session, monkeypatch
):
    w = world(sqlite_session, monkeypatch)

    _method, payload = next(c for c in w["fake"].slack if c[0] == "chat.postMessage")
    text = str(payload["blocks"])
    assert payload["channel"] == w["slack"].channel
    assert "verified" in text and "standards: refuted" in text
    assert "tests: pass" not in text  # only what did not pass
    assert AGENT_CLAIM not in text
    assert f"/evidence/{w['run'].id}" in text
    card = str(w["fake"].teams[-1])
    assert "standards: refuted" in card and AGENT_CLAIM not in card
    assert w["messages"]["teams"].short_code in card


def test_only_enabled_connections_of_the_company_are_queued(
    sqlite_session, monkeypatch
):
    w = world(sqlite_session, monkeypatch)
    w["teams"].enabled = False
    sqlite_session.add(w["teams"])
    sqlite_session.commit()
    other = Company(company_name="Elsewhere")
    sqlite_session.add(other)
    sqlite_session.commit()

    held_task(sqlite_session, w["company"].id)
    held_task(sqlite_session, other.id)

    queued = [
        r.payload["connection_id"]
        for r in sqlite_session.exec(
            select(OutboxEvent).where(
                OutboxEvent.event_type == chat_approvals.POST,
                OutboxEvent.status == "pending",
            )
        ).all()
        if r.payload["connection_id"] in (w["slack"].id, w["teams"].id)
    ]
    assert queued == [w["slack"].id]


def test_a_failed_post_is_recorded_raised_and_posted_once_on_retry(
    sqlite_session, monkeypatch
):
    w = world(sqlite_session, monkeypatch)
    item, _ = held_task(sqlite_session, w["company"].id)
    w["fake"].fail_next = 1
    ids = {w["slack"].id}

    errors = deliver_pending(sqlite_session, ids)

    assert len(errors) == 1
    sqlite_session.refresh(w["slack"])
    assert w["slack"].last_status == "error" and "ConnectError" in w["slack"].last_error
    queued = sqlite_session.exec(
        select(ChatApprovalMessage).where(
            ChatApprovalMessage.task_id == item.id,
            ChatApprovalMessage.connection_id == w["slack"].id,
        )
    ).one()
    assert queued.status == "queued"

    # The processor's retry: the row is pending again, and it posts once.
    row = sqlite_session.exec(
        select(OutboxEvent).where(OutboxEvent.status == "pending")
    ).all()
    assert any(r.payload.get("task_id") == item.id for r in row)
    assert deliver_pending(sqlite_session, ids) == []
    assert deliver_pending(sqlite_session, ids) == []
    posts = [
        c
        for c in w["fake"].slack
        if c[0] == "chat.postMessage" and item.title in c[1]["text"]
    ]
    assert len(posts) == 2  # the world's first task, and this one once
    sqlite_session.refresh(queued)
    assert queued.status == "posted"
    sqlite_session.refresh(w["slack"])
    assert w["slack"].last_status == "ok"


def test_a_hold_decided_before_the_post_posts_nothing(sqlite_session, monkeypatch):
    w = world(sqlite_session, monkeypatch)
    item, _ = held_task(sqlite_session, w["company"].id)
    item.status = "done"
    sqlite_session.add(item)
    sqlite_session.commit()
    before = len(w["fake"].slack)

    assert deliver_pending(sqlite_session, {w["slack"].id}) == []
    assert len(w["fake"].slack) == before


def test_a_teams_url_off_microsoft_hosts_is_refused():
    for url in (
        "http://fabrique-test.webhook.office.com/x",
        "https://hooks.example.com/x",
        "https://user:pw@fabrique-test.webhook.office.com/x",
    ):
        with pytest.raises(chat_connections.InvalidConnection):
            chat_connections.check_teams_url(url, resolve=False)
    assert chat_connections.check_teams_url(
        "https://fabrique-test.webhook.office.com/webhookb2/x", resolve=False
    )


# ---------------------------------------------------------------------------
# Settings and links
# ---------------------------------------------------------------------------


def _slack_body(team_id, **extra):
    return {
        "workspace_id": team_id,
        "channel": "C" + secrets.token_hex(4).upper(),
        "post_credential": "xoxb-" + secrets.token_hex(12),
        "signing_secret": secrets.token_hex(16),
        "enabled": True,
        **extra,
    }


def test_an_owner_configures_and_no_response_or_audit_carries_a_secret(
    sqlite_session, test_client, monkeypatch, auth
):
    team_id = "T" + secrets.token_hex(4).upper()
    setup(monkeypatch, team_id)
    company = Company(company_name="Settings")
    sqlite_session.add(company)
    sqlite_session.commit()
    _, owner = member(sqlite_session, company.id, "Owner")
    body = _slack_body(team_id)
    url = f"/v1/companies/{company.id}/chat"

    saved = test_client.put(f"{url}/slack", json=body, headers=owner)
    read = test_client.get(url, headers=owner)
    kept = test_client.put(
        f"{url}/slack",
        json={**body, "post_credential": None, "signing_secret": None},
        headers=owner,
    )

    assert saved.status_code == read.status_code == kept.status_code == 200
    for response in (saved, read, kept):
        assert body["post_credential"] not in response.text
        assert body["signing_secret"] not in response.text
    connection = read.json()["connections"]["slack"]
    assert connection["has_post_credential"] and connection["has_signing_secret"]
    assert connection["inbound_url"].endswith(
        f"/slack/{connection['inbound_url'].split('/')[-2]}/interactions"
    )
    row = sqlite_session.exec(
        select(ChatConnection).where(ChatConnection.company_id == company.id)
    ).one()
    assert row.post_credential.startswith("fernet:")
    assert row.signing_secret.startswith("fernet:")
    audits = sqlite_session.exec(
        select(AuditLog).where(
            AuditLog.company_id == company.id,
            AuditLog.action == "chat_connection_saved",
        )
    ).all()
    assert "signing_secret" in audits[0].result["fields_changed"]
    assert all(body["signing_secret"] not in str(a.result) for a in audits)
    assert all(body["post_credential"] not in str(a.result) for a in audits)


def test_only_a_signed_in_owner_reaches_the_connection(
    sqlite_session, test_client, monkeypatch, auth
):
    team_id = "T" + secrets.token_hex(4).upper()
    setup(monkeypatch, team_id)
    company = Company(company_name="Roles")
    sqlite_session.add(company)
    sqlite_session.commit()
    _, manager = member(sqlite_session, company.id, "Manager")
    url = f"/v1/companies/{company.id}/chat"

    assert test_client.get(url).status_code == 401
    assert test_client.get(url, headers=manager).status_code == 403
    assert (
        test_client.put(
            f"{url}/slack", json=_slack_body(team_id), headers=manager
        ).status_code
        == 403
    )


def test_the_test_button_records_what_slack_answered(
    sqlite_session, test_client, monkeypatch, auth
):
    team_id = "T" + secrets.token_hex(4).upper()
    fake = setup(monkeypatch, team_id)
    company = Company(company_name="Test button")
    sqlite_session.add(company)
    sqlite_session.commit()
    _, owner = member(sqlite_session, company.id, "Owner")
    url = f"/v1/companies/{company.id}/chat/slack"
    test_client.put(url, json=_slack_body(team_id), headers=owner)

    ok = test_client.post(f"{url}/test", headers=owner).json()
    fake.team_id = "TELSEWHERE"
    wrong = test_client.post(f"{url}/test", headers=owner).json()

    assert ok["ok"] is True and ok["observed"]["team_id"] == team_id
    assert wrong["ok"] is False and "another Slack workspace" in wrong["detail"]
    assert wrong["connection"]["test_ok"] is False


def test_invalid_fields_are_refused_with_the_reason(
    sqlite_session, test_client, monkeypatch, auth
):
    setup(monkeypatch, "TABC")
    company = Company(company_name="Fields")
    sqlite_session.add(company)
    sqlite_session.commit()
    _, owner = member(sqlite_session, company.id, "Owner")
    url = f"/v1/companies/{company.id}/chat"

    bad_team = test_client.put(f"{url}/slack", json=_slack_body("nope"), headers=owner)
    bad_teams = test_client.put(
        f"{url}/teams",
        json={
            "workspace_id": str(uuid.uuid4()),
            "post_credential": "https://hooks.example.com/x",
            "signing_secret": base64.b64encode(secrets.token_bytes(32)).decode(),
            "enabled": True,
        },
        headers=owner,
    )
    unknown = test_client.put(f"{url}/discord", json=_slack_body("TABC"), headers=owner)

    assert bad_team.status_code == 422 and "workspace_id" in bad_team.json()["detail"]
    assert bad_teams.status_code == 422 and "Teams" in bad_teams.json()["detail"]
    assert unknown.status_code == 404


def test_each_member_links_and_unlinks_only_their_own_account(
    sqlite_session, test_client, monkeypatch, auth
):
    w = world(sqlite_session, monkeypatch)
    viewer, viewer_h = w["viewer"]
    base = f"/v1/companies/{w['company'].id}/chat/links"

    issued = test_client.post(f"{base}/slack/code", headers=viewer_h)
    assert issued.status_code == 200
    code = issued.json()["code"]
    assert len(code) == 10 and code in issued.json()["instruction"]

    link(sqlite_session, w["company"].id, viewer, "slack", w["team_id"], "UVIEWER001")
    listed = test_client.get(base, headers=viewer_h).json()
    assert listed["connected"] == {"slack": True, "teams": True}
    assert [entry["external_user_id"] for entry in listed["links"]] == ["UVIEWER001"]
    _, manager_h = w["manager"]
    assert test_client.get(base, headers=manager_h).json()["links"] == []

    removed = test_client.delete(f"{base}/slack", headers=viewer_h).json()
    assert removed["unlinked"] is True
    assert test_client.get(base, headers=viewer_h).json()["links"] == []
