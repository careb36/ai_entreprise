"""The company's notification webhook (issue #357, part 4).

The events are queued in the transaction of the change they report (a closed
or blocked task, a closed sprint, a refuted run), only those the company asked
for, and only while the webhook is on; a delivery is signed with HMAC-SHA256
and a failure is recorded and raised for the outbox to retry. The URL must be
https and public; the secret is encrypted and never returned; a Manager
configures it and a Viewer reads it.
"""

import asyncio
import hashlib
import hmac
import json
import secrets

import crypto
import pytest
from cryptography.fernet import Fernet
from models import (
    AgentRun,
    BacklogItem,
    Company,
    CompanyWebhook,
    OutboxEvent,
    Sprint,
    Verification,
)
from services.integrations import webhooks
from sqlmodel import select
from test_git_workflow import _member, enforcement  # noqa: F401

KEY = Fernet.generate_key().decode()
SECRET = "a-webhook-secret-of-some-length"


@pytest.fixture(autouse=True)
def _setup(monkeypatch):
    monkeypatch.setattr(crypto, "_configured_key", lambda: KEY)
    # Tests use example.com, which the sandbox may not resolve; the public
    # check has its own tests below.
    monkeypatch.setattr(webhooks, "_public_address", lambda host: True)
    webhooks.install()


def _company(session):
    company = Company(company_name=f"C {secrets.token_hex(3)}", autonomy_level=2)
    session.add(company)
    session.commit()
    return company


def _hook(session, company, events=None, enabled=True):
    return webhooks.save(
        session,
        company.id,
        url="https://hooks.example.com/fabrique",
        secret=SECRET,
        events=events,
        enabled=enabled,
    )


def _queued(session, hook):
    return session.exec(
        select(OutboxEvent)
        .where(OutboxEvent.event_type == webhooks.EVENT_TYPE)
        .where(OutboxEvent.aggregate_id == str(hook.id))
        .order_by(OutboxEvent.id)
    ).all()


def _sprint_with_task(session, company):
    sprint = Sprint(company_id=company.id, sprint_name="S1", status="active")
    session.add(sprint)
    session.commit()
    item = BacklogItem(sprint_id=sprint.id, title="Login page", status="in_review")
    session.add(item)
    session.commit()
    return sprint, item


def test_a_closed_task_and_sprint_are_queued_with_the_change(sqlite_session):
    company = _company(sqlite_session)
    hook = _hook(sqlite_session, company)
    sprint, item = _sprint_with_task(sqlite_session, company)
    assert _queued(sqlite_session, hook) == []

    item.status = "done"
    sqlite_session.add(item)
    sqlite_session.commit()
    sprint.status = "completed"
    sqlite_session.add(sprint)
    sqlite_session.commit()

    bodies = [row.payload["body"] for row in _queued(sqlite_session, hook)]
    assert [b["event"] for b in bodies] == ["task_done", "sprint_closed"]
    assert bodies[0]["task_id"] == item.id and bodies[0]["sprint_id"] == sprint.id
    assert bodies[1]["name"] == "S1" and all(
        b["company_id"] == company.id for b in bodies
    )
    assert all(
        row.payload["webhook_id"] == hook.id for row in _queued(sqlite_session, hook)
    )


def test_a_blocked_task_says_why_and_whether_it_waits_for_a_person(sqlite_session):
    company = _company(sqlite_session)
    hook = _hook(sqlite_session, company)
    _, item = _sprint_with_task(sqlite_session, company)
    item.status = "blocked"
    item.comments = [{"author": "System", "text": "Blocked: the daemon is absent."}]
    sqlite_session.add(item)
    sqlite_session.commit()
    [row] = _queued(sqlite_session, hook)
    body = row.payload["body"]
    assert body["event"] == "task_blocked" and "waits_for_a_person" in body
    assert isinstance(body["block_reason"], str)


def test_a_refuted_run_is_queued_and_a_verified_one_is_not(sqlite_session):
    company = _company(sqlite_session)
    hook = _hook(sqlite_session, company)
    run = AgentRun(
        company_id=company.id,
        producer="outside",
        external_ref=f"x-{secrets.token_hex(3)}",
        artifact_digest="0" * 64,
        artifact_size=0,
    )
    sqlite_session.add(run)
    sqlite_session.commit()
    for verdict in ("verified", "refuted"):
        sqlite_session.add(
            Verification(company_id=company.id, agent_run_id=run.id, verdict=verdict)
        )
        sqlite_session.commit()
    [row] = _queued(sqlite_session, hook)
    assert row.payload["body"]["event"] == "run_refuted"
    assert row.payload["body"]["run_id"] == run.id


def test_only_the_events_asked_for_while_on_and_only_for_its_company(sqlite_session):
    company, other = _company(sqlite_session), _company(sqlite_session)
    hook = _hook(sqlite_session, company, events=["sprint_closed"])
    _, item = _sprint_with_task(sqlite_session, company)
    _, theirs = _sprint_with_task(sqlite_session, other)
    item.status = "done"
    theirs.status = "done"
    sqlite_session.add_all([item, theirs])
    sqlite_session.commit()
    assert _queued(sqlite_session, hook) == []

    hook.events, hook.enabled = list(webhooks.EVENTS), False
    sqlite_session.add(hook)
    sqlite_session.commit()
    item.status = "blocked"
    sqlite_session.add(item)
    sqlite_session.commit()
    assert _queued(sqlite_session, hook) == []


def test_a_rolled_back_change_queues_nothing(sqlite_session):
    company = _company(sqlite_session)
    hook = _hook(sqlite_session, company)
    _, item = _sprint_with_task(sqlite_session, company)
    item.status = "done"
    sqlite_session.add(item)
    sqlite_session.flush()
    sqlite_session.rollback()
    assert _queued(sqlite_session, hook) == []


def test_the_delivery_is_signed_and_recorded(sqlite_session):
    company = _company(sqlite_session)
    hook = _hook(sqlite_session, company)
    webhooks.enqueue(sqlite_session, hook, webhooks.PING, {})
    sqlite_session.commit()
    [row] = _queued(sqlite_session, hook)
    sent = {}

    async def post(url, body, headers):
        sent.update(url=url, body=body, headers=headers)
        return 204

    asyncio.run(webhooks.deliver(sqlite_session, row.payload, row.id, post=post))
    expected = (
        "sha256=" + hmac.new(SECRET.encode(), sent["body"], hashlib.sha256).hexdigest()
    )
    assert sent["headers"][webhooks.SIGNATURE_HEADER] == expected
    assert sent["headers"]["X-Fabrique-Event"] == "ping"
    assert sent["headers"]["X-Fabrique-Delivery"] == str(row.id)
    assert json.loads(sent["body"])["company_id"] == company.id
    assert SECRET.encode() not in sent["body"]
    assert hook.last_status == "204" and hook.last_error is None


def test_a_failed_delivery_is_recorded_and_raised_for_the_retry(sqlite_session):
    company = _company(sqlite_session)
    hook = _hook(sqlite_session, company)
    webhooks.enqueue(sqlite_session, hook, webhooks.PING, {})
    sqlite_session.commit()
    [row] = _queued(sqlite_session, hook)

    async def answers_500(url, body, headers):
        return 500

    async def unreachable(url, body, headers):
        raise ConnectionError("refused")

    with pytest.raises(RuntimeError, match="500"):
        asyncio.run(
            webhooks.deliver(sqlite_session, row.payload, row.id, post=answers_500)
        )
    assert hook.last_status == "500"
    with pytest.raises(ConnectionError):
        asyncio.run(
            webhooks.deliver(sqlite_session, row.payload, row.id, post=unreachable)
        )
    assert hook.last_status == "error" and "refused" in hook.last_error


def test_a_webhook_removed_or_off_since_sends_nothing(sqlite_session):
    company = _company(sqlite_session)
    hook = _hook(sqlite_session, company)
    payload = {"webhook_id": hook.id, "event": "ping", "body": {}}
    hook.enabled = False
    sqlite_session.add(hook)
    sqlite_session.commit()

    async def must_not_post(url, body, headers):
        raise AssertionError("posted")

    asyncio.run(webhooks.deliver(sqlite_session, payload, 1, post=must_not_post))


@pytest.mark.parametrize(
    "url, reason",
    [
        ("http://hooks.example.com/x", "https"),
        ("https://user:pw@hooks.example.com/x", "credentials"),
        ("not a url", "not a URL"),
        ("ftp://hooks.example.com", "https"),
    ],
)
def test_a_url_that_is_not_https_or_carries_credentials_is_refused(url, reason):
    with pytest.raises(webhooks.InvalidWebhook, match=reason):
        webhooks.check_url(url)


def test_a_private_address_is_refused_unless_allowed(monkeypatch):
    monkeypatch.undo()
    monkeypatch.setattr(crypto, "_configured_key", lambda: KEY)
    for host in ("127.0.0.1", "10.0.0.5", "169.254.169.254", "localhost"):
        with pytest.raises(webhooks.InvalidWebhook, match="private"):
            webhooks.check_url(f"https://{host}/hook")
    monkeypatch.setenv("WEBHOOK_ALLOW_PRIVATE_HOSTS", "true")
    assert webhooks.check_url("https://127.0.0.1/hook") == "https://127.0.0.1/hook"


def test_the_secret_is_encrypted_kept_when_omitted_and_needs_a_key(
    sqlite_session, monkeypatch
):
    company = _company(sqlite_session)
    hook = _hook(sqlite_session, company)
    stored = hook.encrypted_secret
    assert stored.startswith(crypto.FERNET_PREFIX) and SECRET not in stored
    webhooks.save(
        sqlite_session, company.id, url="https://hooks.example.com/new", secret=None
    )
    assert sqlite_session.get(CompanyWebhook, hook.id).encrypted_secret == stored
    with pytest.raises(webhooks.InvalidWebhook, match="16"):
        webhooks.save(sqlite_session, company.id, url=hook.url, secret="short")
    with pytest.raises(webhooks.InvalidWebhook, match="unknown"):
        webhooks.save(
            sqlite_session, company.id, url=hook.url, secret=None, events=["deploy"]
        )
    monkeypatch.setattr(crypto, "_configured_key", lambda: None)
    with pytest.raises(webhooks.InvalidWebhook, match="FERNET_SECRET_KEY"):
        webhooks.save(sqlite_session, company.id, url=hook.url, secret=SECRET)


def test_a_manager_configures_a_viewer_reads_and_nobody_sees_the_secret(
    sqlite_session,
    test_client,
    enforcement,  # noqa: F811
):
    company, other = _company(sqlite_session), _company(sqlite_session)
    manager = _member(sqlite_session, company.id, "Manager")
    viewer = _member(sqlite_session, company.id, "Viewer")
    outsider = _member(sqlite_session, other.id, "Manager")
    url = f"/v1/companies/{company.id}/webhook"
    body = {
        "url": "https://hooks.example.com/f",
        "secret": SECRET,
        "events": ["task_done"],
    }

    assert test_client.put(url, json=body, headers=viewer).status_code == 403
    assert test_client.put(url, json=body, headers=outsider).status_code == 403
    saved = test_client.put(url, json=body, headers=manager)
    assert saved.status_code == 200 and SECRET not in saved.text
    read = test_client.get(url, headers=viewer)
    assert read.status_code == 200 and SECRET not in read.text
    assert read.json()["webhook"]["events"] == ["task_done"]
    assert read.json()["webhook"]["has_secret"] is True
    assert test_client.get(url, headers=outsider).status_code == 403
    bad = test_client.put(url, json={**body, "url": "http://x"}, headers=manager)
    assert bad.status_code == 422 and "https" in bad.text

    assert test_client.post(f"{url}/ping", headers=viewer).status_code == 403
    assert test_client.post(f"{url}/ping", headers=manager).json()["queued"] == "ping"
    assert test_client.delete(url, headers=viewer).status_code == 403
    assert test_client.delete(url, headers=manager).json()["webhook"] is None
    assert test_client.get(url, headers=viewer).json()["webhook"] is None
