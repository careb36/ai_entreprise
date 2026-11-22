"""Anchors of a company's audit chain, and how much of the chain they cover (issue #542).

``docs/archive/design/AUDIT_ANCHORS.md``. The chain (``audit_chain.py``) is keyed by
``AUDIT_SIGNING_KEY``, which the factory holds: whoever holds that key and can
write the database can rewrite a row and re-sign every row after it, and the
chain alone still verifies. ``docs/POSITIONING.md`` says hashes and signatures
are never accepted from the party being recorded, so the chain head is also
recorded outside, where the factory cannot rewrite it:

- ``tsa``: an RFC 3161 token from the timestamping authority the Owner chose,
  over the anchor's statement (``rfc3161.verify_token``, with the certificate
  the Owner pinned).
- ``customer_key``: the statement signed with an Ed25519 key whose private
  half the customer holds; the backend stores only the public half.
- ``webhook``: the statement posted to the company's endpoint, which keeps
  its own copy. The endpoint's answer is a claim, unless it carries a
  signature by a customer key, which makes it verified.

Every kind covers the same ``statement``: the company, a sequence number,
that row's chain hash and the time of the anchor. Because each chain hash
commits to every row before it, an anchor at sequence ``n`` covers rows
``1..n``. ``coverage`` re-checks every anchor against the chain as it is now
and reports which range each covers, the tail no external anchor covers yet,
and every anchor that no longer matches: a row rewritten and re-signed with
the factory's key passes ``verify_chain`` and fails here.

The anchor rows themselves live in the factory's database: rewriting one
breaks its token or signature, which the factory cannot produce. A webhook
anchor's evidence is the customer's copy; here it is shown as a claim.
"""

from __future__ import annotations

import base64
import binascii
import hashlib
import json
from datetime import UTC, datetime, timedelta
from typing import Any

from cryptography.exceptions import InvalidSignature
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PublicKey
from cryptography.hazmat.primitives.serialization import (
    Encoding,
    PublicFormat,
    load_pem_public_key,
)
from models import AuditAnchor, AuditAnchorSettings, AuditLog
from sqlalchemy.exc import IntegrityError
from sqlmodel import Session, select

from services.platform import rfc3161

STATEMENT_TYPE = "la-fabrique.audit-anchor"
STATEMENT_VERSION = 1
KINDS = ("webhook", "tsa", "customer_key")
DESIGNED_KINDS = ("git_tag",)
# How far a signer's clock may run ahead of the backend's.
MAX_CLOCK_SKEW = timedelta(minutes=5)


class InvalidAnchor(ValueError):
    """The anchor asked for cannot be recorded; the message says why."""


# ---------------------------------------------------------------------------
# The statement every kind covers
# ---------------------------------------------------------------------------


def as_utc(moment: datetime) -> datetime:
    """*moment* in UTC, to the second: what the statement says and the row stores."""
    if moment.tzinfo is None:
        moment = moment.replace(tzinfo=UTC)
    return moment.astimezone(UTC).replace(microsecond=0)


def statement(
    company_id: int, seq: int, chain_hash: str, anchored_at: datetime
) -> bytes:
    """The bytes an anchor covers (pinned format: a change needs a new version)."""
    body = {
        "type": STATEMENT_TYPE,
        "v": STATEMENT_VERSION,
        "company_id": company_id,
        "seq": seq,
        "chain_hash": chain_hash,
        "anchored_at": as_utc(anchored_at).strftime("%Y-%m-%dT%H:%M:%SZ"),
    }
    return json.dumps(body, sort_keys=True, separators=(",", ":")).encode("utf-8")


def anchor_statement(anchor: AuditAnchor) -> bytes:
    return statement(
        anchor.company_id, anchor.chain_seq, anchor.chain_hash, anchor.anchored_at
    )


def statement_digest(anchor: AuditAnchor) -> bytes:
    return hashlib.sha256(anchor_statement(anchor)).digest()


def head(session: Session, company_id: int) -> tuple[int, str] | None:
    """The company's last chained row: its sequence number and hash."""
    row = session.exec(
        select(AuditLog.chain_seq, AuditLog.chain_hash)
        .where(AuditLog.company_id == company_id)
        .where(AuditLog.chain_seq.is_not(None))  # type: ignore[union-attr]
        .order_by(AuditLog.chain_seq.desc())  # type: ignore[union-attr]
        .limit(1)
    ).first()
    if row is None or row[1] is None:
        return None
    return int(row[0]), str(row[1])


# ---------------------------------------------------------------------------
# Customer keys (Ed25519)
# ---------------------------------------------------------------------------


def parse_public_key(text: str) -> tuple[str, str]:
    """An Ed25519 public key (PEM, or base64 of its 32 bytes): its fingerprint and PEM."""
    text = (text or "").strip()
    try:
        if text.startswith("-----BEGIN"):
            key = load_pem_public_key(text.encode())
        else:
            key = Ed25519PublicKey.from_public_bytes(
                base64.b64decode(text, validate=True)
            )
    except (ValueError, TypeError, binascii.Error) as exc:
        raise InvalidAnchor("public_key: not an Ed25519 public key") from exc
    if not isinstance(key, Ed25519PublicKey):
        raise InvalidAnchor("public_key: only Ed25519 keys are accepted")
    raw = key.public_bytes(Encoding.Raw, PublicFormat.Raw)
    pem = key.public_bytes(Encoding.PEM, PublicFormat.SubjectPublicKeyInfo).decode()
    return "ed25519:" + hashlib.sha256(raw).hexdigest(), pem


def settings_for(session: Session, company_id: int) -> AuditAnchorSettings | None:
    return session.exec(
        select(AuditAnchorSettings).where(AuditAnchorSettings.company_id == company_id)
    ).first()


def keys_for(
    settings: AuditAnchorSettings | None, *, active_only: bool
) -> dict[str, Ed25519PublicKey]:
    keys: dict[str, Ed25519PublicKey] = {}
    for entry in (settings.customer_keys if settings else None) or []:
        if active_only and entry.get("retired_at"):
            continue
        try:
            key = load_pem_public_key(str(entry["public_key"]).encode())
        except (KeyError, ValueError, TypeError):
            continue  # an unreadable entry verifies nothing
        if isinstance(key, Ed25519PublicKey):
            keys[str(entry["fingerprint"])] = key
    return keys


def signature_bytes(signature: str) -> bytes:
    try:
        raw = base64.b64decode((signature or "").strip(), validate=True)
    except (ValueError, binascii.Error) as exc:
        raise InvalidAnchor("signature: not base64") from exc
    if len(raw) != 64:
        raise InvalidAnchor("signature: an Ed25519 signature is 64 bytes")
    return raw


def signed_by(
    keys: dict[str, Ed25519PublicKey], data: bytes, signature: bytes
) -> str | None:
    """The fingerprint of the key in *keys* whose signature *signature* is, or None."""
    for fingerprint, key in keys.items():
        try:
            key.verify(signature, data)
        except InvalidSignature:
            continue
        return fingerprint
    return None


# ---------------------------------------------------------------------------
# Recording anchors
# ---------------------------------------------------------------------------


def existing(
    session: Session, company_id: int, kind: str, seq: int
) -> AuditAnchor | None:
    return session.exec(
        select(AuditAnchor)
        .where(AuditAnchor.company_id == company_id)
        .where(AuditAnchor.kind == kind)
        .where(AuditAnchor.chain_seq == seq)
    ).first()


def create(
    session: Session,
    company_id: int,
    kind: str,
    at: tuple[int, str],
    *,
    reason: str,
    requested_by: str | None = None,
    anchored_at: datetime | None = None,
    **fields: Any,
) -> AuditAnchor | None:
    """A new anchor of *kind* at chain position *at*; None when one already exists.

    Flushed in a savepoint, so a second replica racing to the same anchor
    (refused by the unique index) leaves the caller's transaction usable.
    """
    if kind not in KINDS:
        raise InvalidAnchor(f"kind: unknown {kind}")
    seq, chain_hash = at
    if kind != "customer_key" and existing(session, company_id, kind, seq) is not None:
        return None
    anchor = AuditAnchor(
        company_id=company_id,
        kind=kind,
        chain_seq=seq,
        chain_hash=chain_hash,
        anchored_at=as_utc(anchored_at or datetime.now(UTC)),
        reason=reason,
        requested_by=requested_by,
        **fields,
    )
    try:
        with session.begin_nested():
            session.add(anchor)
    except IntegrityError:
        return None
    return anchor


def record_customer_signature(
    session: Session,
    company_id: int,
    *,
    seq: int,
    chain_hash: str,
    anchored_at: datetime,
    signature: str,
    requested_by: str | None = None,
) -> AuditAnchor:
    """Store a head the customer signed outside, once its signature verifies.

    The head must be the chain's row at *seq* as it is now: a customer who
    recorded a hash the chain no longer has has found a rewrite, and is told.
    """
    raw = signature_bytes(signature)
    anchored_at = as_utc(anchored_at)
    if anchored_at > datetime.now(UTC) + MAX_CLOCK_SKEW:
        raise InvalidAnchor("anchored_at: in the future")
    stored = session.exec(
        select(AuditLog.chain_hash)
        .where(AuditLog.company_id == company_id)
        .where(AuditLog.chain_seq == seq)
    ).first()
    if stored is None:
        raise InvalidAnchor("seq: the chain has no row with this number")
    if stored != chain_hash:
        raise InvalidAnchor("chain_hash: does not match the chain's row at seq")
    keys = keys_for(settings_for(session, company_id), active_only=True)
    if not keys:
        raise InvalidAnchor("signature: the company has no active customer key")
    data = statement(company_id, seq, chain_hash, anchored_at)
    fingerprint = signed_by(keys, data, raw)
    if fingerprint is None:
        raise InvalidAnchor("signature: does not verify with any active customer key")
    anchor = create(
        session,
        company_id,
        "customer_key",
        (seq, chain_hash),
        reason="customer",
        requested_by=requested_by,
        anchored_at=anchored_at,
        status="verified",
        external_ref=fingerprint,
        key_fingerprint=fingerprint,
        receipt={"signature": base64.b64encode(raw).decode()},
    )
    if anchor is None:
        raise InvalidAnchor("seq: this key has already signed this head")
    return anchor


# ---------------------------------------------------------------------------
# Coverage
# ---------------------------------------------------------------------------


def check(
    anchor: AuditAnchor, chain: dict[int, str], keys: dict[str, Ed25519PublicKey]
) -> tuple[str, str | None]:
    """The anchor's state now: verified, claim, pending, undelivered or mismatch (with why)."""
    if anchor.status == "pending":
        return "pending", None
    if anchor.status == "failed":
        return "undelivered", anchor.error
    if anchor.chain_seq not in chain:
        return "mismatch", "row_missing"
    if chain[anchor.chain_seq] != anchor.chain_hash:
        return "mismatch", "hash_mismatch"
    receipt = anchor.receipt or {}
    data = anchor_statement(anchor)
    if anchor.kind == "tsa":
        try:
            token = base64.b64decode(str(receipt.get("token") or ""), validate=True)
            rfc3161.verify_token(
                token,
                hashlib.sha256(data).digest(),
                str(receipt.get("tsa_cert_pem") or ""),
            )
        except (rfc3161.TimestampError, ValueError, binascii.Error) as exc:
            return "mismatch", f"token_invalid: {exc}"
        return "verified", None
    signature = receipt.get("signature")
    if anchor.kind == "customer_key":
        key = keys.get(anchor.key_fingerprint)
        if key is None:
            return "mismatch", "key_unknown"
        try:
            raw = signature_bytes(str(signature or ""))
        except InvalidAnchor:
            return "mismatch", "signature_invalid"
        return (
            ("verified", None)
            if signed_by({anchor.key_fingerprint: key}, data, raw)
            else ("mismatch", "signature_invalid")
        )
    # webhook: the endpoint keeps the copy; its answer is a claim unless a customer key signed it.
    if isinstance(signature, str):
        try:
            if signed_by(keys, data, signature_bytes(signature)):
                return "verified", None
        except InvalidAnchor:
            pass
    return "claim", None


def describe(anchor: AuditAnchor, state: str, reason: str | None) -> dict[str, Any]:
    receipt = anchor.receipt or {}
    return {
        "id": anchor.id,
        "kind": anchor.kind,
        "seq": anchor.chain_seq,
        "chain_hash": anchor.chain_hash,
        "anchored_at": as_utc(anchor.anchored_at).isoformat(),
        "reason": anchor.reason,
        "status": anchor.status,
        "state": state,
        "problem": reason,
        "external_ref": anchor.external_ref,
        "gen_time": receipt.get("gen_time"),
        "tsa_cert_sha256": receipt.get("tsa_cert_sha256"),
    }


def coverage(session: Session, company_id: int) -> dict[str, Any]:
    """Which anchors cover which range of the chain, and what they do not cover.

    ``status``: ``empty`` (no chain), ``no_anchors`` (nothing outside covers
    it), ``uncovered_tail`` (rows after the last anchor), ``covered`` (every
    row up to the head) or ``anchor_mismatch`` (an anchor no longer matches
    the chain or its own proof: the chain was rewritten, or the anchor was).
    ``verified_through``: the last row covered by a proof the backend checked
    with a key it does not hold; ``covered_through`` adds webhook claims.
    """
    rows = session.exec(
        select(AuditLog.chain_seq, AuditLog.chain_hash)
        .where(AuditLog.company_id == company_id)
        .where(AuditLog.chain_seq.is_not(None))  # type: ignore[union-attr]
    ).all()
    chain = {int(seq): str(chain_hash) for seq, chain_hash in rows}
    head_seq = max(chain) if chain else 0
    anchors = session.exec(
        select(AuditAnchor)
        .where(AuditAnchor.company_id == company_id)
        .order_by(AuditAnchor.chain_seq, AuditAnchor.id)  # type: ignore[arg-type]
    ).all()
    keys = keys_for(settings_for(session, company_id), active_only=False)

    listed, failures = [], []
    covering: dict[int, set[str]] = {}
    for anchor in anchors:
        state, reason = check(anchor, chain, keys)
        listed.append(describe(anchor, state, reason))
        if state == "mismatch":
            failures.append(
                {
                    "anchor_id": anchor.id,
                    "kind": anchor.kind,
                    "seq": anchor.chain_seq,
                    "reason": reason,
                }
            )
        elif state in ("verified", "claim"):
            covering.setdefault(anchor.chain_seq, set()).add(state)

    segments, start = [], 1
    for seq in sorted(covering):
        segments.append(
            {
                "from": start,
                "to": seq,
                "strength": "verified" if "verified" in covering[seq] else "claim",
            }
        )
        start = seq + 1
    verified = [seq for seq, states in covering.items() if "verified" in states]
    covered_through = max(covering, default=0)
    uncovered = (
        {"from": covered_through + 1, "to": head_seq}
        if head_seq > covered_through
        else None
    )

    if failures:
        status = "anchor_mismatch"
    elif not chain:
        status = "empty"
    elif not covering:
        status = "no_anchors"
    elif uncovered:
        status = "uncovered_tail"
    else:
        status = "covered"
    return {
        "company_id": company_id,
        "status": status,
        "head_seq": head_seq,
        "covered_through": covered_through,
        "verified_through": max(verified, default=0),
        "segments": segments,
        "uncovered": uncovered,
        "failures": failures,
        "anchors": listed,
        "designed_not_built": list(DESIGNED_KINDS),
    }
