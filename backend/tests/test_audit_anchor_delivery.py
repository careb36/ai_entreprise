"""Taking audit anchors, sending them out, and the routes (issue #542).

No network: the webhook's POST and the authority's answer are faked, the
authority's key and the customer's key are made at test time.
"""

import asyncio
import base64
import json
import secrets
from datetime import UTC, datetime, timedelta

import audit_signing
import crypto
import pytest
import tsa_fake
from cryptography.fernet import Fernet
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey
from cryptography.hazmat.primitives.serialization import Encoding, PublicFormat
from egress_fakes import FakeResolver
from models import AuditAnchor, OutboxEvent, Sprint
from services.integrations import anchor_settings, anchoring, webhooks
from services.platform import audit_anchors, egress
from sqlmodel import select
from test_audit_anchors import company, write


@pytest.fixture(autouse=True)
def no_anchor_events_left(sqlite_session):
    """Other suites read every outbox row: the anchors' events leave with the test."""
    yield
    sqlite_session.rollback()
    for row in sqlite_session.exec(
        select(OutboxEvent).where(OutboxEvent.aggregate_type == "audit_anchor")
    ).all():
        sqlite_session.delete(row)
    sqlite_session.commit()


@pytest.fixture(autouse=True)
def _setup(monkeypatch, tmp_path):
    fernet = Fernet.generate_key().decode()
    monkeypatch.setattr(crypto, "_configured_key", lambda: fernet)
    # example.com may not resolve in the sandbox: a fake DNS answers a public
    # address, as in test_webhooks.py; the posts are pinned to it (#541).
    monkeypatch.setattr(egress, "RESOLVER", FakeResolver())
    audit_key = secrets.token_hex(32)
    monkeypatch.setattr(audit_signing, "_configured_key", lambda: audit_key)
    monkeypatch.setenv("AUDIT_LOG_PATH", str(tmp_path))


@pytest.fixture(scope="module")
def tsa():
    return tsa_fake.make_tsa()


def public_pem(private) -> str:
    return (
        private.public_key()
        .public_bytes(Encoding.PEM, PublicFormat.SubjectPublicKeyInfo)
        .decode()
    )


def configure(session, cid, tsa=None, webhook=True, **extra):
    if webhook:
        webhooks.save(
            session,
            cid,
            url="https://hooks.example.com/anchor",
            secret=secrets.token_urlsafe(24),
        )
    changes = {"enabled": True, "publish_webhook": webhook, **extra}
    if tsa is not None:
        changes.update(tsa_url="https://tsa.example.com/", tsa_cert_pem=tsa.cert_pem)
    return anchor_settings.save(session, cid, changes, updated_by="human:owner")


def queued(session, anchor_ids):
    return session.exec(
        select(OutboxEvent)
        .where(OutboxEvent.aggregate_type == "audit_anchor")
        .where(OutboxEvent.aggregate_id.in_([str(i) for i in anchor_ids]))  # type: ignore[union-attr]
    ).all()


def fake_tsa_request(tsa):
    async def request(url, body, timeout):
        return tsa_fake.response(
            tsa, tsa_fake.digest_of(body), nonce=tsa_fake.nonce_of(body)
        )

    return request


def test_take_queues_one_anchor_per_configured_kind(sqlite_session, tsa):
    cid = company()
    write(sqlite_session, cid, 2)
    configure(sqlite_session, cid, tsa)
    created = anchoring.take(sqlite_session, cid, "on_demand", requested_by="human:m")
    sqlite_session.commit()
    assert sorted(a.kind for a in created) == ["tsa", "webhook"]
    assert all(a.chain_seq == 2 and a.status == "pending" for a in created)
    events = queued(sqlite_session, [a.id for a in created])
    assert sorted(e.event_type for e in events) == [
        anchoring.EVENT_TSA,
        anchoring.EVENT_WEBHOOK,
    ]
    # The same head is not anchored twice by a kind.
    assert anchoring.take(sqlite_session, cid, "cadence") == []


def test_a_webhook_anchor_is_signed_and_its_receipt_kept_as_a_claim(sqlite_session):
    cid = company()
    write(sqlite_session, cid)
    configure(sqlite_session, cid)
    (anchor,) = anchoring.take(sqlite_session, cid, "on_demand")
    sqlite_session.commit()
    sent = {}

    async def post(url, body, headers, timeout, max_bytes):
        sent.update(url=url, body=body, headers=headers)
        return 200, b'{"received": true}'

    asyncio.run(
        anchoring.deliver_webhook(
            sqlite_session, {"anchor_id": anchor.id}, 41, post=post
        )
    )
    hook = webhooks.get(sqlite_session, cid)
    secret = crypto.decrypt_value(hook.encrypted_secret)
    assert sent["headers"][webhooks.SIGNATURE_HEADER] == webhooks.sign(
        secret, sent["body"]
    )
    body = json.loads(sent["body"])
    assert body["statement"].encode() == audit_anchors.anchor_statement(anchor)
    assert (anchor.status, anchor.external_ref) == ("delivered", "delivery:41")
    assert anchor.receipt["body"] == '{"received": true}'
    state = audit_anchors.coverage(sqlite_session, cid)["anchors"][0]["state"]
    assert state == "claim"


def test_a_receipt_signed_by_the_customer_key_is_verified(sqlite_session):
    cid = company()
    write(sqlite_session, cid)
    private = Ed25519PrivateKey.generate()
    configure(sqlite_session, cid, add_public_key=public_pem(private))
    (anchor,) = anchoring.take(sqlite_session, cid, "cadence")
    sqlite_session.commit()

    async def post(url, body, headers, timeout, max_bytes):
        statement = json.loads(body)["statement"].encode()
        return 202, json.dumps(
            {"signature": base64.b64encode(private.sign(statement)).decode()}
        ).encode()

    asyncio.run(
        anchoring.deliver_webhook(
            sqlite_session, {"anchor_id": anchor.id}, 1, post=post
        )
    )
    assert anchor.status == "verified"
    report = audit_anchors.coverage(sqlite_session, cid)
    assert report["verified_through"] == report["head_seq"] == 1


def test_a_failing_webhook_is_retried_then_marked_failed(sqlite_session):
    cid = company()
    write(sqlite_session, cid)
    configure(sqlite_session, cid)
    (anchor,) = anchoring.take(sqlite_session, cid, "cadence")
    sqlite_session.commit()

    async def post(url, body, headers, timeout, max_bytes):
        return 500, b""

    with pytest.raises(RuntimeError, match="HTTP 500"):
        asyncio.run(
            anchoring.deliver_webhook(
                sqlite_session, {"anchor_id": anchor.id}, 1, 0, post=post
            )
        )
    assert anchor.status == "pending" and anchor.error.endswith("HTTP 500")
    with pytest.raises(RuntimeError):
        asyncio.run(
            anchoring.deliver_webhook(
                sqlite_session,
                {"anchor_id": anchor.id},
                1,
                anchoring.LAST_ATTEMPT,
                post=post,
            )
        )
    assert anchor.status == "failed"
    assert audit_anchors.coverage(sqlite_session, cid)["status"] == "no_anchors"


def test_a_tsa_anchor_keeps_a_token_that_verifies(sqlite_session, tsa):
    cid = company()
    write(sqlite_session, cid, 3)
    configure(sqlite_session, cid, tsa, webhook=False)
    (anchor,) = anchoring.take(sqlite_session, cid, "cadence")
    sqlite_session.commit()
    asyncio.run(
        anchoring.deliver_tsa(
            sqlite_session, {"anchor_id": anchor.id}, request=fake_tsa_request(tsa)
        )
    )
    assert anchor.status == "verified" and anchor.external_ref == "tsa-serial:7"
    assert anchor.receipt["tsa_cert_pem"] == tsa.cert_pem.strip()
    report = audit_anchors.coverage(sqlite_session, cid)
    assert report["status"] == "covered" and report["verified_through"] == 3


def test_a_token_from_another_authority_is_not_kept(sqlite_session, tsa):
    cid = company()
    write(sqlite_session, cid)
    configure(sqlite_session, cid, tsa, webhook=False)
    (anchor,) = anchoring.take(sqlite_session, cid, "cadence")
    sqlite_session.commit()
    with pytest.raises(Exception, match="certificate"):
        asyncio.run(
            anchoring.deliver_tsa(
                sqlite_session,
                {"anchor_id": anchor.id},
                request=fake_tsa_request(tsa_fake.make_tsa()),
            )
        )
    assert anchor.status == "pending" and anchor.receipt is None


def test_the_cadence_covers_the_whole_chain_up_to_its_last_run(sqlite_session, tsa):
    cid = company()
    write(sqlite_session, cid, 4)
    configure(sqlite_session, cid, tsa, webhook=False, cadence_minutes=60)
    engine = sqlite_session.get_bind()
    now = datetime.now(UTC)

    def run_and_deliver(at):
        anchoring.run_cadence(engine, now=at)
        sqlite_session.expire_all()
        for anchor in sqlite_session.exec(
            select(AuditAnchor)
            .where(AuditAnchor.company_id == cid)
            .where(AuditAnchor.status == "pending")
        ).all():
            asyncio.run(
                anchoring.deliver_tsa(
                    sqlite_session,
                    {"anchor_id": anchor.id},
                    request=fake_tsa_request(tsa),
                )
            )
        sqlite_session.commit()

    run_and_deliver(now)
    report = audit_anchors.coverage(sqlite_session, cid)
    assert report["covered_through"] == report["head_seq"] == 4

    write(sqlite_session, cid, 2)
    assert cid not in anchoring.due(sqlite_session, now + timedelta(minutes=30))
    assert audit_anchors.coverage(sqlite_session, cid)["uncovered"] == {
        "from": 5,
        "to": 6,
    }
    run_and_deliver(now + timedelta(minutes=61))
    report = audit_anchors.coverage(sqlite_session, cid)
    assert report["status"] == "covered" and report["verified_through"] == 6


def test_a_closed_sprint_queues_an_anchor_for_an_enabled_company(sqlite_session):
    anchoring.install()
    cid = company()
    configure(sqlite_session, cid, webhook=False)
    other = company()
    for company_id in (cid, other):
        sqlite_session.add(
            Sprint(company_id=company_id, sprint_name="S", status="active")
        )
    sqlite_session.commit()
    for sprint in sqlite_session.exec(
        select(Sprint).where(Sprint.company_id.in_([cid, other]))
    ):  # type: ignore[union-attr]
        sprint.status = "completed"
        sqlite_session.add(sprint)
    sqlite_session.commit()
    takes = sqlite_session.exec(
        select(OutboxEvent).where(OutboxEvent.event_type == anchoring.EVENT_TAKE)
    ).all()
    companies = [e.payload["company_id"] for e in takes]
    assert cid in companies and other not in companies
    assert {"company_id": cid, "reason": "sprint_close"} in [e.payload for e in takes]


def test_settings_are_checked_before_they_are_stored(sqlite_session, tsa):
    cid = company()

    def save(**changes):
        return anchor_settings.save(sqlite_session, cid, changes, updated_by="human:o")

    with pytest.raises(anchor_settings.InvalidAnchorSettings, match="cadence"):
        save(cadence_minutes=5)
    with pytest.raises(
        anchor_settings.InvalidAnchorSettings, match="certificate is needed"
    ):
        save(tsa_url="https://tsa.example.com/")
    with pytest.raises(anchor_settings.InvalidAnchorSettings, match="tsa_url"):
        save(tsa_url="ftp://tsa.example.com/", tsa_cert_pem=tsa.cert_pem)
    with pytest.raises(anchor_settings.InvalidAnchorSettings, match="time-stamping"):
        save(
            tsa_url="http://tsa.example.com/",
            tsa_cert_pem=tsa_fake.make_tsa(timestamping=False).cert_pem,
        )
    row = save(tsa_url="http://tsa.example.com/", tsa_cert_pem=tsa.cert_pem)
    assert row.tsa_url == "http://tsa.example.com/"
    with pytest.raises(anchor_settings.InvalidAnchorSettings, match="Ed25519"):
        save(add_public_key="not a key")
    pem = public_pem(Ed25519PrivateKey.generate())
    save(add_public_key=pem)
    with pytest.raises(
        anchor_settings.InvalidAnchorSettings, match="already registered"
    ):
        save(add_public_key=pem)
    fingerprint = anchor_settings.describe(sqlite_session, cid)["customer_keys"][0][
        "fingerprint"
    ]
    save(retire_fingerprint=fingerprint)
    described = anchor_settings.describe(sqlite_session, cid)
    assert described["customer_keys"][0]["retired_at"]
    assert described["tsa_cert_sha256"]


def test_the_routes_respect_roles_and_report_coverage(
    test_client, sqlite_session, monkeypatch
):
    import auth_tokens
    from config import settings
    from models import Company, CompanyMember, User

    monkeypatch.setattr(settings, "AUTH_REQUIRED", True)
    signing = secrets.token_urlsafe(48)
    monkeypatch.setattr(auth_tokens, "_configured_key", lambda: signing)
    owner = User(
        email=f"o-{secrets.token_hex(4)}@example.com", full_name="O", password_hash="x"
    )
    viewer = User(
        email=f"v-{secrets.token_hex(4)}@example.com", full_name="V", password_hash="x"
    )
    firm = Company(company_name="Anchors")
    sqlite_session.add_all([owner, viewer, firm])
    sqlite_session.commit()
    sqlite_session.add_all([
        CompanyMember(company_id=firm.id, user_id=owner.id, human_role="Owner"),
        CompanyMember(company_id=firm.id, user_id=viewer.id, human_role="Viewer"),
    ])  # fmt: skip
    sqlite_session.commit()

    def headers(user):
        return {
            "Authorization": "Bearer "
            + auth_tokens.create_access_token(user_id=user.id, email=user.email)
        }

    params = {"company_id": firm.id}
    write(sqlite_session, firm.id, 2)
    private = Ed25519PrivateKey.generate()
    put = test_client.put(
        "/v1/audit/anchors/settings",
        params=params,
        headers=headers(viewer),
        json={"enabled": True},
    )
    assert put.status_code == 403
    put = test_client.put(
        "/v1/audit/anchors/settings",
        params=params,
        headers=headers(owner),
        json={"enabled": True, "add_public_key": public_pem(private)},
    )
    assert put.status_code == 200, put.text
    assert put.json()["customer_keys"][0]["fingerprint"].startswith("ed25519:")
    bad = test_client.put(
        "/v1/audit/anchors/settings",
        params=params,
        headers=headers(owner),
        json={"cadence_minutes": 1},
    )
    assert bad.status_code == 422
    assert (
        test_client.post(
            "/v1/audit/anchors", params=params, headers=headers(viewer)
        ).status_code
        == 403
    )

    statement = test_client.get(
        "/v1/audit/anchors/statement", params=params, headers=headers(viewer)
    ).json()
    signature = base64.b64encode(private.sign(statement["statement"].encode())).decode()
    signed = test_client.post(
        "/v1/audit/anchors/signature",
        params=params,
        headers=headers(viewer),
        json={k: statement[k] for k in ("seq", "chain_hash", "anchored_at")}
        | {"signature": signature},
    )
    assert signed.status_code == 200, signed.text
    forged = test_client.post(
        "/v1/audit/anchors/signature",
        params=params,
        headers=headers(viewer),
        json={k: statement[k] for k in ("seq", "chain_hash", "anchored_at")}
        | {
            "signature": base64.b64encode(
                Ed25519PrivateKey.generate().sign(b"x")
            ).decode()
        },
    )
    assert forged.status_code == 422

    chain = test_client.get(
        "/v1/audit/chain", params=params, headers=headers(viewer)
    ).json()
    assert chain["status"] == "intact"
    # The settings change and the signature were audited after the anchored head.
    assert chain["anchors"]["status"] == "uncovered_tail"
    assert chain["anchors"]["verified_through"] == statement["seq"]
    exported = test_client.get(
        f"/v1/audit/anchors/{signed.json()['id']}/export",
        params=params,
        headers=headers(viewer),
    ).json()
    assert exported["statement"] == statement["statement"]
    assert exported["public_key"] == public_pem(private)
    stranger = test_client.get(
        "/v1/audit/anchors",
        params={"company_id": firm.id + 10_000},
        headers=headers(viewer),
    )
    assert stranger.status_code == 403


def test_a_tsa_url_that_resolves_to_a_private_address_is_refused(
    sqlite_session, tsa, monkeypatch
):
    """The TSA URL is a company's setting, checked when saved and resolved
    once and pinned when sent, like a webhook's (#541, INJT-19): a name that
    answers a private address at send time is refused before anything is sent."""
    cid = company()
    write(sqlite_session, cid)
    configure(sqlite_session, cid, tsa, webhook=False)
    (anchor,) = anchoring.take(sqlite_session, cid, "cadence")
    sqlite_session.commit()
    # Public when saved, private when sent: a rebinding DNS answer.
    monkeypatch.setattr(
        egress, "RESOLVER", FakeResolver({"tsa.example.com": ["10.0.0.5"]})
    )
    sent = []

    async def request(target, body, timeout):
        sent.append(target)
        return b""

    with pytest.raises((egress.EgressRefused, webhooks.InvalidWebhook)):
        asyncio.run(
            anchoring.deliver_tsa(
                sqlite_session, {"anchor_id": anchor.id}, request=request
            )
        )
    assert sent == [] and anchor.status == "pending"


def test_the_tsa_request_goes_to_the_pinned_address(sqlite_session, tsa):
    cid = company()
    write(sqlite_session, cid)
    configure(sqlite_session, cid, tsa, webhook=False)
    (anchor,) = anchoring.take(sqlite_session, cid, "cadence")
    sqlite_session.commit()
    sent = []
    answer = fake_tsa_request(tsa)

    async def request(target, body, timeout):
        sent.append(target)
        return await answer(target, body, timeout)

    asyncio.run(
        anchoring.deliver_tsa(sqlite_session, {"anchor_id": anchor.id}, request=request)
    )
    (target,) = sent
    assert "93.184.215.14" in target.connect_url
    assert target.headers() == {"Host": "tsa.example.com"}
