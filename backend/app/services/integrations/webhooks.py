"""Tell a company, at a URL it chose, that work finished (issue #357, part 4).

A Manager sets one webhook per company: a URL, a secret and the events to
send. When a task closes, a task is blocked, a sprint closes or the Verifier
refutes a run, a delivery goes into the outbox in the same transaction as the
change, so it is sent exactly when the change is committed and never for a
change that rolled back. The outbox processor posts it (``deliver``) with the
body signed by HMAC-SHA256 in ``X-Fabrique-Signature-256`` (``sha256=<hex>``,
the shape GitHub uses for the webhooks this backend receives) and retries a
failed post with backoff. A webhook that is down never holds up a sprint: the
change is committed first, and the delivery is its own row.

The events are watched in one place, a ``before_flush`` listener over the
session (``install``), rather than at each of the places that change a task's
status: a new place that closes or blocks a task is notified without knowing
about webhooks.

The URL must be https and, unless ``WEBHOOK_ALLOW_PRIVATE_HOSTS`` is set, must
not resolve to a private, loopback or link-local address: checked when it is
saved and again before every post, since a name can resolve elsewhere later.
Before a post the name is resolved once and the post goes to the address that
was checked (``services/platform/egress.py``), so a DNS answer that changes
between the check and the connection cannot steer it inward.
"""

from __future__ import annotations

import asyncio
import hashlib
import hmac
import json
import os
from datetime import UTC, datetime
from typing import Any
from urllib.parse import urlsplit

import crypto
from logging_config import logger
from models import BacklogItem, CompanyWebhook, OutboxEvent, Sprint, Verification
from sqlalchemy import event, inspect
from sqlalchemy.orm import Session as OrmSession
from sqlmodel import Session, select

from services.integrations import webhook_client
from services.platform import egress
from services.tasks import board

EVENT_TYPE = "webhook_post"
EVENTS = ("task_done", "task_blocked", "sprint_closed", "run_refuted")
PING = "ping"
SIGNATURE_HEADER = "X-Fabrique-Signature-256"
TIMEOUT_SECONDS = 10.0
MAX_URL_CHARS = 2000
MAX_ERROR_CHARS = 300


class InvalidWebhook(ValueError):
    """The webhook asked for cannot be saved; the message says why."""


def _allow_private() -> bool:
    return os.getenv("WEBHOOK_ALLOW_PRIVATE_HOSTS", "").strip().lower() in {
        "1",
        "true",
        "yes",
    }


def _public_address(host: str) -> bool:
    """Whether every address *host* resolves to is a public one."""
    return egress.all_public(host)


def check_url(url: str, *, resolve: bool = True) -> str:
    """*url* as a webhook target, or ``InvalidWebhook`` with the reason."""
    url = (url or "").strip()
    if not url or len(url) > MAX_URL_CHARS or any(ch.isspace() for ch in url):
        raise InvalidWebhook("url: not a URL")
    parts = urlsplit(url)
    if parts.scheme != "https" or not parts.hostname:
        raise InvalidWebhook("url: must be an https URL")
    if parts.username or parts.password:
        raise InvalidWebhook("url: credentials belong in the secret, not the URL")
    if resolve and not _allow_private() and not _public_address(parts.hostname):
        raise InvalidWebhook(
            "url: the host resolves to a private or unreachable address "
            "(set WEBHOOK_ALLOW_PRIVATE_HOSTS to allow it)"
        )
    return url


def _target(url: str) -> egress.Target:
    """Where a post to *url* goes: checked, resolved once and pinned."""
    check_url(url, resolve=False)
    try:
        return egress.pin(url, allow_private=_allow_private())
    except egress.EgressRefused as exc:
        raise InvalidWebhook(
            "url: the host resolves to a private or unreachable address "
            "(set WEBHOOK_ALLOW_PRIVATE_HOSTS to allow it)"
        ) from exc


def check_events(events: Any) -> list[str]:
    if events is None:
        return list(EVENTS)
    if not isinstance(events, list) or not all(isinstance(e, str) for e in events):
        raise InvalidWebhook("events: must be a list of event names")
    unknown = sorted(set(events) - set(EVENTS))
    if unknown:
        raise InvalidWebhook(
            f"events: unknown {', '.join(unknown)}; known: {', '.join(EVENTS)}"
        )
    return [e for e in EVENTS if e in events]


def get(session: Session, company_id: int) -> CompanyWebhook | None:
    return session.exec(
        select(CompanyWebhook).where(CompanyWebhook.company_id == company_id)
    ).first()


def describe(hook: CompanyWebhook | None) -> dict[str, Any] | None:
    """What the API shows: everything but the secret."""
    if hook is None:
        return None
    return {
        "url": hook.url,
        "events": list(hook.events or []),
        "enabled": hook.enabled,
        "has_secret": bool(hook.encrypted_secret),
        "last_delivery_at": hook.last_delivery_at,
        "last_status": hook.last_status,
        "last_error": hook.last_error,
    }


def save(
    session: Session,
    company_id: int,
    *,
    url: str,
    secret: str | None,
    events: Any = None,
    enabled: bool = True,
) -> CompanyWebhook:
    """Create or replace the company's webhook; an omitted secret keeps the stored one."""
    if not crypto.is_encryption_enabled():
        raise InvalidWebhook(
            "secret: FERNET_SECRET_KEY is not set, so the secret cannot be stored"
        )
    url = check_url(url)
    events = check_events(events)
    hook = get(session, company_id)
    if secret is not None:
        secret = secret.strip()
        if len(secret) < 16:
            raise InvalidWebhook("secret: at least 16 characters")
    elif hook is None:
        raise InvalidWebhook("secret: required for a new webhook")
    now = datetime.now(UTC)
    if hook is None:
        hook = CompanyWebhook(company_id=company_id, url=url, encrypted_secret="")
    hook.url, hook.events, hook.enabled, hook.updated_at = url, events, enabled, now
    if secret is not None:
        hook.encrypted_secret = crypto.encrypt_value(secret)
    session.add(hook)
    session.commit()
    session.refresh(hook)
    return hook


def sign(secret: str, body: bytes) -> str:
    return "sha256=" + hmac.new(secret.encode(), body, hashlib.sha256).hexdigest()


def enqueue(
    session: Session, hook: CompanyWebhook, name: str, data: dict[str, Any]
) -> None:
    """Add the delivery to the outbox in the caller's transaction."""
    body = {
        "event": name,
        "company_id": hook.company_id,
        "occurred_at": datetime.now(UTC).isoformat(),
        **data,
    }
    session.add(
        OutboxEvent(
            aggregate_type="company_webhook",
            aggregate_id=str(hook.id),
            event_type=EVENT_TYPE,
            payload={"webhook_id": hook.id, "event": name, "body": body},
        )
    )


# ---------------------------------------------------------------------------
# Watching the session
# ---------------------------------------------------------------------------


def _changed_to(obj: Any, attribute: str) -> str | None:
    history = inspect(obj).attrs[attribute].history
    if not history.added:
        return None
    new = history.added[0]
    return new if new not in (history.deleted or ()) else None


def _events_in(session: OrmSession) -> list[tuple[int, str, dict[str, Any]]]:
    found: list[tuple[int, str, dict[str, Any]]] = []
    for obj in list(session.new) + list(session.dirty):
        if isinstance(obj, BacklogItem) and obj.id is not None:
            status = _changed_to(obj, "status")
            if status not in ("done", "blocked"):
                continue
            sprint = session.get(Sprint, obj.sprint_id)
            if sprint is None:
                continue
            data = {
                "sprint_id": sprint.id,
                "task_id": obj.id,
                "title": obj.title,
                "status": status,
            }
            if status == "blocked":
                data["block_reason"] = board.block_reason(obj.comments)
                data["waits_for_a_person"] = board.item_is_settled(obj)
            found.append(
                (
                    sprint.company_id,
                    "task_done" if status == "done" else "task_blocked",
                    data,
                )
            )
        elif isinstance(obj, Sprint) and obj.id is not None:
            if _changed_to(obj, "status") == "completed":
                found.append(
                    (
                        obj.company_id,
                        "sprint_closed",
                        {"sprint_id": obj.id, "name": obj.sprint_name},
                    )
                )
        elif (
            isinstance(obj, Verification)
            and obj in session.new
            and obj.verdict == "refuted"
        ):
            found.append(
                (
                    obj.company_id,
                    "run_refuted",
                    {"run_id": obj.agent_run_id, "verdict": obj.verdict},
                )
            )
    return found


def _before_flush(session: OrmSession, _context: Any, _instances: Any) -> None:
    try:
        with session.no_autoflush:
            found = _events_in(session)
            if not found:
                return
            hooks: dict[int, CompanyWebhook | None] = {}
            for company_id, name, data in found:
                if company_id not in hooks:
                    hooks[company_id] = (
                        session.execute(
                            select(CompanyWebhook).where(
                                CompanyWebhook.company_id == company_id
                            )
                        )
                        .scalars()
                        .first()
                    )
                hook = hooks[company_id]
                if hook and hook.enabled and name in (hook.events or []):
                    enqueue(session, hook, name, data)
    except Exception:  # noqa: BLE001 - A notification never stops the change it reports.
        logger.warning("Webhook: could not queue a notification", exc_info=True)


_installed = False


def install() -> None:
    """Watch every session for the events a webhook may ask for (idempotent)."""
    global _installed
    if not _installed:
        event.listen(OrmSession, "before_flush", _before_flush)
        _installed = True


# ---------------------------------------------------------------------------
# Delivery (the outbox processor)
# ---------------------------------------------------------------------------


def _record(
    session: Session, hook: CompanyWebhook, status: str, error: str | None
) -> None:
    hook.last_delivery_at = datetime.now(UTC)
    hook.last_status = status
    hook.last_error = (error or "")[:MAX_ERROR_CHARS] or None
    session.add(hook)


async def deliver(
    session: Session, payload: dict[str, Any], delivery_id: int, post=None
) -> None:
    """Post one queued delivery; raises on failure so the outbox retries it."""
    hook = session.get(CompanyWebhook, payload.get("webhook_id"))
    if hook is None or not hook.enabled:
        return  # Removed or turned off since: nothing to send, nothing to retry.
    body = json.dumps(payload.get("body") or {}, sort_keys=True, default=str).encode()
    headers = {
        "Content-Type": "application/json",
        "User-Agent": "La-Fabrique-Webhook",
        "X-Fabrique-Event": str(payload.get("event") or ""),
        "X-Fabrique-Delivery": str(delivery_id),
        SIGNATURE_HEADER: sign(crypto.decrypt_value(hook.encrypted_secret), body),
    }
    try:
        # DNS lookup off the event loop; the post goes to the checked address.
        target = await asyncio.to_thread(_target, hook.url)
        if post is None:
            status_code = await webhook_client.post(
                target, body, headers, TIMEOUT_SECONDS
            )
        else:
            status_code = await post(hook.url, body, headers)
    except Exception as exc:
        # The adapter's error already names the transport's exception.
        reason = (
            str(exc)
            if isinstance(exc, webhook_client.WebhookPostError)
            else f"{type(exc).__name__}: {exc}"
        )
        _record(session, hook, "error", reason)
        raise
    if not 200 <= status_code < 300:
        _record(session, hook, str(status_code), f"HTTP {status_code}")
        raise RuntimeError(f"webhook answered HTTP {status_code}")
    _record(session, hook, str(status_code), None)
