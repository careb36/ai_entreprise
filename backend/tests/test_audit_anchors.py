"""Anchors of the audit chain outside the factory, and their coverage (issue #542).

The measure of the issue: a row rewritten and every later row re-signed with
the factory's own key passes ``verify_chain``, and the anchor check fails.
Keys (the customer's Ed25519 key, the authority's) are made at test time.
"""

import base64
import hashlib
import secrets
from datetime import UTC, datetime, timedelta

import audit_signing
import pytest
import tsa_fake
from audit_chain import chain_hash, verify_chain
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey
from cryptography.hazmat.primitives.serialization import Encoding, PublicFormat
from models import AuditAnchor, AuditAnchorSettings, AuditLog, OutboxEvent
from services.platform import audit_anchors
from services.platform.agent_service import AgentService
from sqlmodel import select


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
def factory_key(monkeypatch, tmp_path):
    monkeypatch.setenv("AUDIT_LOG_PATH", str(tmp_path))
    key = secrets.token_hex(32)
    monkeypatch.setattr(audit_signing, "_configured_key", lambda: key)
    return key


def company() -> int:
    return 20_000_000 + secrets.randbelow(10_000_000)


def write(session, company_id, n=1):
    for i in range(n):
        AgentService().audit_interaction(
            session=session,
            interaction_id=f"anchor-{secrets.token_hex(4)}",
            agent_id="developer",
            action="execute",
            details={"i": i},
            result={"ok": True},
            decision_signature=None,
            cost_tokens=1,
            company_id=company_id,
        )


def customer_key(session, company_id):
    """A key pair the customer holds; the backend is given the public half."""
    private = Ed25519PrivateKey.generate()
    public = private.public_key().public_bytes(
        Encoding.PEM, PublicFormat.SubjectPublicKeyInfo
    )
    fingerprint, pem = audit_anchors.parse_public_key(public.decode())
    settings = AuditAnchorSettings(
        company_id=company_id,
        customer_keys=[
            {
                "fingerprint": fingerprint,
                "public_key": pem,
                "added_at": "x",
                "retired_at": None,
            }
        ],
    )
    session.add(settings)
    session.commit()
    return private, fingerprint


def customer_signs(session, company_id, private, at=None):
    seq, chain_hash_ = at or audit_anchors.head(session, company_id)
    anchored_at = datetime.now(UTC)
    signature = private.sign(
        audit_anchors.statement(company_id, seq, chain_hash_, anchored_at)
    )
    anchor = audit_anchors.record_customer_signature(
        session,
        company_id,
        seq=seq,
        chain_hash=chain_hash_,
        anchored_at=anchored_at,
        signature=base64.b64encode(signature).decode(),
    )
    session.commit()
    return anchor


def tsa_anchor(session, company_id, tsa):
    at = audit_anchors.head(session, company_id)
    anchor = audit_anchors.create(session, company_id, "tsa", at, reason="cadence")
    session.flush()
    token = tsa_fake.token(tsa, audit_anchors.statement_digest(anchor))
    anchor.receipt = {
        "token": base64.b64encode(token).decode(),
        "tsa_cert_pem": tsa.cert_pem,
    }
    anchor.status = "verified"
    session.commit()
    return anchor


def rewrite_and_resign(session, company_id, seq, key):
    """What the holder of the factory's key can do: alter a row, re-sign the rest."""
    rows = session.exec(
        select(AuditLog)
        .where(AuditLog.company_id == company_id)
        .order_by(AuditLog.chain_seq)
    ).all()
    previous = None
    for row in rows:
        if row.chain_seq == seq:
            row.details = {"i": "rewritten"}
            row.decision_signature = audit_signing.sign_audit_payload(
                audit_signing.canonical_payload(
                    interaction_id=row.interaction_id,
                    agent_id=row.agent_id,
                    action=row.action,
                    details=row.details,
                    result=row.result,
                    cost_tokens=row.cost_tokens,
                    created_at=row.created_at,
                )
            )
        if row.chain_seq >= seq:
            row.prev_hash = previous
            row.chain_hash = chain_hash(company_id, row.chain_seq, previous, row, key)
        previous = row.chain_hash
        session.add(row)
    session.commit()


def test_the_statement_is_pinned_and_to_the_second():
    moment = datetime(2026, 11, 15, 12, 0, 1, 999, tzinfo=UTC)
    assert audit_anchors.statement(7, 3, "hmac-sha256:ab", moment) == (
        b'{"anchored_at":"2026-11-15T12:00:01Z","chain_hash":"hmac-sha256:ab",'
        b'"company_id":7,"seq":3,"type":"la-fabrique.audit-anchor","v":1}'
    )
    naive = moment.replace(tzinfo=None)
    assert audit_anchors.statement(7, 3, "h", naive) == audit_anchors.statement(
        7, 3, "h", moment
    )


def test_a_rewrite_resigned_with_the_factory_key_passes_the_chain_and_fails_the_anchors(
    sqlite_session, factory_key
):
    cid = company()
    write(sqlite_session, cid, 4)
    private, _ = customer_key(sqlite_session, cid)
    customer_signs(sqlite_session, cid, private)
    tsa_anchor(sqlite_session, cid, tsa_fake.make_tsa())
    before = audit_anchors.coverage(sqlite_session, cid)
    assert before["status"] == "covered"
    assert before["verified_through"] == before["head_seq"] == 4

    rewrite_and_resign(sqlite_session, cid, 2, factory_key)

    assert verify_chain(sqlite_session, cid)["status"] == "intact"
    after = audit_anchors.coverage(sqlite_session, cid)
    assert after["status"] == "anchor_mismatch"
    assert {(f["kind"], f["reason"]) for f in after["failures"]} == {
        ("customer_key", "hash_mismatch"),
        ("tsa", "hash_mismatch"),
    }


def test_rewriting_the_anchor_too_breaks_its_proof(sqlite_session, factory_key):
    cid = company()
    write(sqlite_session, cid, 3)
    private, _ = customer_key(sqlite_session, cid)
    customer_signs(sqlite_session, cid, private)
    tsa_anchor(sqlite_session, cid, tsa_fake.make_tsa())

    rewrite_and_resign(sqlite_session, cid, 1, factory_key)
    new_head = audit_anchors.head(sqlite_session, cid)[1]
    for anchor in sqlite_session.exec(
        select(AuditAnchor).where(AuditAnchor.company_id == cid)
    ):
        anchor.chain_hash = new_head
        sqlite_session.add(anchor)
    sqlite_session.commit()

    reasons = {
        f["kind"]: f["reason"]
        for f in audit_anchors.coverage(sqlite_session, cid)["failures"]
    }
    assert reasons["customer_key"] == "signature_invalid"
    assert reasons["tsa"].startswith("token_invalid")


def test_dropping_the_newest_rows_is_caught_by_an_anchor(sqlite_session):
    cid = company()
    write(sqlite_session, cid, 3)
    private, _ = customer_key(sqlite_session, cid)
    customer_signs(sqlite_session, cid, private)
    newest = sqlite_session.exec(
        select(AuditLog)
        .where(AuditLog.company_id == cid)
        .where(AuditLog.chain_seq == 3)
    ).one()
    sqlite_session.delete(newest)
    sqlite_session.commit()

    assert (
        verify_chain(sqlite_session, cid)["status"] == "intact"
    )  # the chain alone cannot tell
    report = audit_anchors.coverage(sqlite_session, cid)
    assert report["failures"][0]["reason"] == "row_missing"


def test_coverage_reports_ranges_and_the_uncovered_tail(sqlite_session):
    cid = company()
    assert audit_anchors.coverage(sqlite_session, cid)["status"] == "empty"
    write(sqlite_session, cid, 2)
    private, _ = customer_key(sqlite_session, cid)
    assert audit_anchors.coverage(sqlite_session, cid)["status"] == "no_anchors"
    customer_signs(sqlite_session, cid, private)
    write(sqlite_session, cid, 2)
    webhook = audit_anchors.create(
        sqlite_session,
        cid,
        "webhook",
        audit_anchors.head(sqlite_session, cid),
        reason="cadence",
        status="delivered",
    )
    sqlite_session.commit()
    write(sqlite_session, cid, 1)

    report = audit_anchors.coverage(sqlite_session, cid)
    assert report["status"] == "uncovered_tail"
    assert report["segments"] == [
        {"from": 1, "to": 2, "strength": "verified"},
        {"from": 3, "to": 4, "strength": "claim"},
    ]
    assert report["verified_through"] == 2 and report["covered_through"] == 4
    assert report["uncovered"] == {"from": 5, "to": 5}
    states = {a["id"]: a["state"] for a in report["anchors"]}
    assert states[webhook.id] == "claim"


def test_pending_and_undelivered_anchors_cover_nothing(sqlite_session):
    cid = company()
    write(sqlite_session, cid, 2)
    at = audit_anchors.head(sqlite_session, cid)
    audit_anchors.create(sqlite_session, cid, "tsa", at, reason="cadence")
    audit_anchors.create(
        sqlite_session,
        cid,
        "webhook",
        at,
        reason="cadence",
        status="failed",
        error="HTTP 500",
    )
    sqlite_session.commit()
    report = audit_anchors.coverage(sqlite_session, cid)
    assert report["status"] == "no_anchors" and report["failures"] == []
    assert sorted(a["state"] for a in report["anchors"]) == ["pending", "undelivered"]


def test_a_kind_anchors_a_head_once(sqlite_session):
    cid = company()
    write(sqlite_session, cid)
    at = audit_anchors.head(sqlite_session, cid)
    assert (
        audit_anchors.create(sqlite_session, cid, "tsa", at, reason="cadence")
        is not None
    )
    assert (
        audit_anchors.create(sqlite_session, cid, "tsa", at, reason="sprint_close")
        is None
    )


def test_a_customer_signature_is_checked_before_it_is_stored(sqlite_session):
    cid = company()
    write(sqlite_session, cid, 2)
    private, fingerprint = customer_key(sqlite_session, cid)
    seq, head_hash = audit_anchors.head(sqlite_session, cid)
    now = datetime.now(UTC)

    def submit(**overrides):
        fields = {"seq": seq, "chain_hash": head_hash, "anchored_at": now}
        fields.update(overrides)
        signer = fields.pop("signer", private)
        data = audit_anchors.statement(
            cid, fields["seq"], fields["chain_hash"], fields["anchored_at"]
        )
        fields.setdefault("signature", base64.b64encode(signer.sign(data)).decode())
        return audit_anchors.record_customer_signature(sqlite_session, cid, **fields)

    with pytest.raises(audit_anchors.InvalidAnchor, match="any active customer key"):
        submit(signer=Ed25519PrivateKey.generate())
    with pytest.raises(audit_anchors.InvalidAnchor, match="does not match"):
        submit(chain_hash="hmac-sha256:" + "0" * 64)
    with pytest.raises(audit_anchors.InvalidAnchor, match="no row"):
        submit(seq=99)
    with pytest.raises(audit_anchors.InvalidAnchor, match="future"):
        submit(anchored_at=now + timedelta(hours=1))
    with pytest.raises(audit_anchors.InvalidAnchor, match="64 bytes"):
        submit(signature=base64.b64encode(b"short").decode())
    anchor = submit()
    assert (anchor.status, anchor.key_fingerprint) == ("verified", fingerprint)
    with pytest.raises(audit_anchors.InvalidAnchor, match="already signed"):
        submit()


def test_a_retired_key_signs_nothing_new_and_its_anchors_still_verify(sqlite_session):
    cid = company()
    write(sqlite_session, cid)
    private, _ = customer_key(sqlite_session, cid)
    customer_signs(sqlite_session, cid, private)
    settings = audit_anchors.settings_for(sqlite_session, cid)
    settings.customer_keys = [dict(settings.customer_keys[0], retired_at="2026-11-15")]
    sqlite_session.add(settings)
    sqlite_session.commit()
    assert audit_anchors.coverage(sqlite_session, cid)["status"] == "covered"
    write(sqlite_session, cid)
    with pytest.raises(audit_anchors.InvalidAnchor, match="no active customer key"):
        customer_signs(sqlite_session, cid, private)


def test_only_ed25519_public_keys_are_accepted():
    raw = (
        Ed25519PrivateKey.generate()
        .public_key()
        .public_bytes(Encoding.Raw, PublicFormat.Raw)
    )
    fingerprint, pem = audit_anchors.parse_public_key(base64.b64encode(raw).decode())
    assert fingerprint == "ed25519:" + hashlib.sha256(raw).hexdigest()
    assert pem.startswith("-----BEGIN PUBLIC KEY-----")
    for bad in ("", "not a key", base64.b64encode(b"x" * 31).decode()):
        with pytest.raises(audit_anchors.InvalidAnchor):
            audit_anchors.parse_public_key(bad)
