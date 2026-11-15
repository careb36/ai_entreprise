"""Taking a company's audit anchors and sending them out (issue #542).

``docs/design/AUDIT_ANCHORS.md``. An anchor records the chain's head outside
the factory (``services/platform/audit_anchors.py``). This module decides when
one is taken and delivers it:

- **When.** On the Owner's cadence (``cadence_loop``, every
  ``cadence_minutes``; one replica at a time), when a sprint closes or
  something is released (a run published, a sprint's integration published;
  watched by a ``before_flush`` listener, ``install``, as the webhook does),
  and on demand (``POST /v1/audit/anchors``). A head already anchored by a
  kind is not anchored again by it.
- **How.** Each anchor is a row plus an outbox event in the same transaction;
  the outbox processor delivers it and retries a failure with backoff, as it
  does the notification webhook. ``deliver_webhook`` posts the statement to
  the company's webhook, signed with the webhook's secret, and keeps the
  endpoint's answer as the receipt; ``deliver_tsa`` asks the Owner's RFC 3161
  authority for a token over the statement's digest and keeps it once it
  verifies against the pinned certificate.

A delivery that fails its last attempt marks the anchor ``failed``: the
coverage report shows it as undelivered, never as covering anything.
"""

from __future__ import annotations

import asyncio
import base64
import hashlib
import json
import secrets
from datetime import UTC, datetime, timedelta
from typing import Any

import crypto
from logging_config import logger
from models import (
    AuditAnchor,
    AuditAnchorSettings,
    OutboxEvent,
    RunPublication,
    Sprint,
    SprintIntegration,
)
from sqlalchemy import event, func, inspect
from sqlalchemy.orm import Session as OrmSession
from sqlmodel import Session, select

from services.integrations import tsa_client, webhook_client, webhooks
from services.platform import audit_anchors, egress, locks, rfc3161

EVENT_TAKE = "audit_anchor_take"
EVENT_WEBHOOK = "audit_anchor_webhook"
EVENT_TSA = "audit_anchor_tsa"
EVENT_TYPES = (EVENT_TAKE, EVENT_WEBHOOK, EVENT_TSA)
# The outbox's last attempt is the one made with retry_count 5.
LAST_ATTEMPT = 5
RECEIPT_MAX_BYTES = 4096
TSA_TIMEOUT_SECONDS = 15.0
CADENCE_POLL_SECONDS = 60
CADENCE_LOCK = "audit-anchor-cadence"
MAX_ERROR_CHARS = 300


def take(
    session: Session, company_id: int, reason: str, requested_by: str | None = None
) -> list[AuditAnchor]:
    """Anchor the company's head with each configured kind; the caller commits."""
    settings = audit_anchors.settings_for(session, company_id)
    at = audit_anchors.head(session, company_id)
    if settings is None or at is None:
        return []
    kinds = []
    hook = webhooks.get(session, company_id)
    if settings.publish_webhook and hook is not None and hook.enabled:
        kinds.append(("webhook", EVENT_WEBHOOK))
    if settings.tsa_url and settings.tsa_cert_pem:
        kinds.append(("tsa", EVENT_TSA))
    created = []
    for kind, event_type in kinds:
        anchor = audit_anchors.create(
            session, company_id, kind, at, reason=reason, requested_by=requested_by
        )
        if anchor is None:
            continue
        session.flush()
        session.add(
            OutboxEvent(
                aggregate_type="audit_anchor",
                aggregate_id=str(anchor.id),
                event_type=event_type,
                payload={"anchor_id": anchor.id},
            )
        )
        created.append(anchor)
    return created


def _failed(
    anchor: AuditAnchor, error: str, attempt: int, *, final: bool = False
) -> None:
    anchor.error = error[:MAX_ERROR_CHARS]
    if final or attempt >= LAST_ATTEMPT:
        anchor.status = "failed"
    anchor.updated_at = datetime.now(UTC)


def _pending(session: Session, payload: dict[str, Any]) -> AuditAnchor | None:
    anchor = session.get(AuditAnchor, payload.get("anchor_id"))
    return anchor if anchor is not None and anchor.status == "pending" else None


def _receipt(status_code: int, answer: bytes) -> dict[str, Any]:
    text = answer.decode("utf-8", "replace")
    receipt: dict[str, Any] = {"status_code": status_code, "body": text}
    try:
        parsed = json.loads(text)
    except ValueError:
        return receipt
    if isinstance(parsed, dict) and isinstance(parsed.get("signature"), str):
        receipt["signature"] = parsed["signature"][:200]
    return receipt


async def deliver_webhook(
    session: Session,
    payload: dict[str, Any],
    delivery_id: int,
    attempt: int = 0,
    post=None,
) -> None:
    """Post one webhook anchor; raises on failure so the outbox retries it."""
    anchor = _pending(session, payload)
    if anchor is None:
        return
    hook = webhooks.get(session, anchor.company_id)
    if hook is None or not hook.enabled:
        _failed(anchor, "the webhook was removed or turned off", attempt, final=True)
        session.add(anchor)
        return
    data = audit_anchors.anchor_statement(anchor)
    body = json.dumps(
        {
            "event": "audit_anchor",
            "company_id": anchor.company_id,
            "anchor_id": anchor.id,
            "seq": anchor.chain_seq,
            "chain_hash": anchor.chain_hash,
            "anchored_at": audit_anchors.as_utc(anchor.anchored_at).isoformat(),
            "reason": anchor.reason,
            "statement": data.decode("utf-8"),
            "statement_sha256": hashlib.sha256(data).hexdigest(),
        },
        sort_keys=True,
    ).encode()
    headers = {
        "Content-Type": "application/json",
        "User-Agent": "La-Fabrique-Webhook",
        "X-Fabrique-Event": "audit_anchor",
        "X-Fabrique-Delivery": str(delivery_id),
        webhooks.SIGNATURE_HEADER: webhooks.sign(
            crypto.decrypt_value(hook.encrypted_secret), body
        ),
    }
    send = post or webhook_client.post_for_receipt
    try:
        # Checked, resolved once and pinned, as webhooks.deliver posts (#541).
        target = await asyncio.to_thread(webhooks._target, hook.url)
        status_code, answer = await send(
            target, body, headers, webhooks.TIMEOUT_SECONDS, RECEIPT_MAX_BYTES
        )
        if not 200 <= status_code < 300:
            raise RuntimeError(f"HTTP {status_code}")
    except Exception as exc:
        _failed(anchor, f"{type(exc).__name__}: {exc}", attempt)
        session.add(anchor)
        raise
    receipt = _receipt(status_code, answer)
    keys = audit_anchors.keys_for(
        audit_anchors.settings_for(session, anchor.company_id), active_only=True
    )
    signature = receipt.get("signature")
    verified_by = None
    if signature:
        try:
            verified_by = audit_anchors.signed_by(
                keys, data, audit_anchors.signature_bytes(signature)
            )
        except audit_anchors.InvalidAnchor:
            verified_by = None
    if verified_by:
        receipt["verified_by"] = verified_by
    anchor.receipt = receipt
    anchor.status = "verified" if verified_by else "delivered"
    anchor.external_ref = f"delivery:{delivery_id}"
    anchor.error = None
    anchor.updated_at = datetime.now(UTC)
    session.add(anchor)


async def deliver_tsa(
    session: Session, payload: dict[str, Any], attempt: int = 0, request=None
) -> None:
    """Ask the authority for a token over one anchor; raises on failure to retry."""
    anchor = _pending(session, payload)
    if anchor is None:
        return
    settings = audit_anchors.settings_for(session, anchor.company_id)
    if settings is None or not settings.tsa_url or not settings.tsa_cert_pem:
        _failed(anchor, "no timestamping authority is configured", attempt, final=True)
        session.add(anchor)
        return
    url, cert_pem = settings.tsa_url, settings.tsa_cert_pem
    digest = audit_anchors.statement_digest(anchor)
    nonce = secrets.randbits(63)
    send = request or tsa_client.request
    try:
        await asyncio.to_thread(webhooks.check_url, url, schemes=("https", "http"))
        # Resolved once and pinned: a company's TSA URL must not reach a
        # private address (#541, INJT-19), unless the operator allows it.
        target = await asyncio.to_thread(
            egress.pin, url, allow_private=webhooks._allow_private()
        )
        answer = await send(
            target, rfc3161.build_request(digest, nonce), TSA_TIMEOUT_SECONDS
        )
        token = rfc3161.token_from_response(answer)
        facts = rfc3161.verify_token(token, digest, cert_pem, nonce=nonce)
    except Exception as exc:
        _failed(anchor, f"{type(exc).__name__}: {exc}", attempt)
        session.add(anchor)
        raise
    anchor.receipt = {
        "token": base64.b64encode(token).decode(),
        "tsa_url": url,
        "tsa_cert_pem": cert_pem,
        "tsa_cert_sha256": facts.tsa_cert_sha256,
        "gen_time": facts.gen_time.isoformat(),
        "serial": str(facts.serial),
        "policy": facts.policy,
    }
    anchor.status = "verified"
    anchor.external_ref = f"tsa-serial:{facts.serial}"
    anchor.error = None
    anchor.updated_at = datetime.now(UTC)
    session.add(anchor)


async def deliver(
    session: Session,
    event_type: str,
    payload: dict[str, Any],
    delivery_id: int,
    attempt: int,
) -> None:
    """The outbox processor's entry point for the three anchor events."""
    if event_type == EVENT_TAKE:
        take(session, int(payload["company_id"]), str(payload.get("reason") or "event"))
    elif event_type == EVENT_WEBHOOK:
        await deliver_webhook(session, payload, delivery_id, attempt)
    elif event_type == EVENT_TSA:
        await deliver_tsa(session, payload, attempt)


# ---------------------------------------------------------------------------
# The cadence
# ---------------------------------------------------------------------------


def due(session: Session, now: datetime | None = None) -> list[int]:
    """The companies whose last cadence anchor is older than their cadence."""
    now = now or datetime.now(UTC)
    found = []
    for settings in session.exec(
        select(AuditAnchorSettings).where(AuditAnchorSettings.enabled == True)
    ).all():
        last = session.exec(
            select(func.max(AuditAnchor.anchored_at))
            .where(AuditAnchor.company_id == settings.company_id)
            .where(AuditAnchor.reason == "cadence")
        ).first()
        interval = timedelta(minutes=settings.cadence_minutes)
        if last is None or audit_anchors.as_utc(last) + interval <= now:
            found.append(settings.company_id)
    return found


def run_cadence(engine: Any, now: datetime | None = None) -> int:
    """Take the anchors that are due; how many were created (0 without the lock)."""
    created = 0
    with locks.held(engine, CADENCE_LOCK) as got:
        if not got:
            return 0
        with Session(engine) as session:
            for company_id in due(session, now):
                created += len(take(session, company_id, "cadence"))
                session.commit()
    return created


async def cadence_loop(engine: Any) -> None:
    """The background worker that takes cadence anchors every minute it is due."""
    while True:
        await asyncio.sleep(CADENCE_POLL_SECONDS)
        try:
            await asyncio.to_thread(run_cadence, engine)
        except Exception as exc:  # noqa: BLE001 - Try again on the next round.
            logger.warning("Audit anchors not taken: %s", exc)


# ---------------------------------------------------------------------------
# Sprint close and release
# ---------------------------------------------------------------------------


def _changed_to(obj: Any, attribute: str) -> Any:
    history = inspect(obj).attrs[attribute].history
    return history.added[0] if history.added else None


def _is_release(session: OrmSession, obj: Any) -> bool:
    """A run published, or a sprint's integration published."""
    if isinstance(obj, RunPublication):
        return obj in session.new
    return (
        isinstance(obj, SprintIntegration)
        and obj.id is not None
        and bool(_changed_to(obj, "publication"))
    )


def _events_in(session: OrmSession) -> list[tuple[int, str]]:
    found: list[tuple[int, str]] = []
    for obj in list(session.new) + list(session.dirty):
        if isinstance(obj, Sprint) and obj.id is not None:
            if _changed_to(obj, "status") == "completed":
                found.append((obj.company_id, "sprint_close"))
        elif _is_release(session, obj):
            found.append((obj.company_id, "release"))
    return found


def _before_flush(session: OrmSession, _context: Any, _instances: Any) -> None:
    try:
        with session.no_autoflush:
            found = _events_in(session)
            seen: set[tuple[int, str]] = set()
            for company_id, reason in found:
                if (company_id, reason) in seen:
                    continue
                seen.add((company_id, reason))
                enabled = session.execute(
                    select(AuditAnchorSettings.enabled).where(
                        AuditAnchorSettings.company_id == company_id
                    )
                ).scalar()
                if enabled:
                    session.add(
                        OutboxEvent(
                            aggregate_type="audit_anchor",
                            aggregate_id=str(company_id),
                            event_type=EVENT_TAKE,
                            payload={"company_id": company_id, "reason": reason},
                        )
                    )
    except Exception:  # noqa: BLE001 - An anchor never stops the change it follows.
        logger.warning("Audit anchors: could not queue an anchor", exc_info=True)


_installed = False


def install() -> None:
    """Anchor the head when a sprint closes or something is released (idempotent)."""
    global _installed
    if not _installed:
        event.listen(OrmSession, "before_flush", _before_flush)
        _installed = True
