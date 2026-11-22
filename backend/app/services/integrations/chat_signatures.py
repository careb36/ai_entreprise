"""Verifying that an inbound chat request came from the platform (``docs/archive/design/CHAT_APPROVALS.md``, C1, C2).

Pure functions over the raw body and the headers; nothing here reads the
database or parses the body before the signature holds.

- **Slack** signs ``v0:{X-Slack-Request-Timestamp}:{body}`` with the app's
  signing secret (HMAC-SHA256, hex) in ``X-Slack-Signature: v0=<hex>``. The
  timestamp must be within ``MAX_SKEW_SECONDS`` of now, in either direction.
- **Teams** (an outgoing webhook) signs the body with the key Teams showed
  when the webhook was created, base64-decoded (HMAC-SHA256, base64) in
  ``Authorization: HMAC <base64>``. The signature covers no timestamp, so the
  activity's own ``timestamp`` (inside the signed body) is checked by
  ``check_activity_time`` once the body is parsed.

Both compare with ``hmac.compare_digest``. After AixleHQ/flow's
``Webhooks::SignatureVerifier`` (``slack_v0``, a five-minute window;
Apache-2.0, design only).
"""

from __future__ import annotations

import base64
import binascii
import hashlib
import hmac
from datetime import UTC, datetime

MAX_SKEW_SECONDS = 300


class SignatureRefused(Exception):
    """The request is not the platform's, or is too old; the message says which check failed."""


def slack_signature(secret: str, timestamp: str, body: bytes) -> str:
    base = b"v0:" + timestamp.encode() + b":" + body
    return "v0=" + hmac.new(secret.encode(), base, hashlib.sha256).hexdigest()


def verify_slack(
    secret: str | None,
    timestamp: str | None,
    signature: str | None,
    body: bytes,
    now: float | None = None,
) -> None:
    """Raise ``SignatureRefused`` unless Slack signed *body* within the window."""
    if not secret:
        raise SignatureRefused("no signing secret configured")
    if not timestamp or not signature:
        raise SignatureRefused("missing signature headers")
    try:
        sent = int(timestamp)
    except ValueError as exc:
        raise SignatureRefused("timestamp is not a number") from exc
    current = now if now is not None else datetime.now(UTC).timestamp()
    if abs(current - sent) > MAX_SKEW_SECONDS:
        raise SignatureRefused("stale timestamp")
    expected = slack_signature(secret, timestamp, body)
    if not hmac.compare_digest(expected.encode(), signature.encode()):
        raise SignatureRefused("signature mismatch")


def teams_signature(secret_b64: str, body: bytes) -> str:
    key = base64.b64decode(secret_b64, validate=True)
    return base64.b64encode(hmac.new(key, body, hashlib.sha256).digest()).decode()


def verify_teams(
    secret_b64: str | None, authorization: str | None, body: bytes
) -> None:
    """Raise ``SignatureRefused`` unless Teams signed *body* with the webhook's key."""
    if not secret_b64:
        raise SignatureRefused("no signing secret configured")
    scheme, _, provided = (authorization or "").partition(" ")
    if scheme != "HMAC" or not provided.strip():
        raise SignatureRefused("missing signature header")
    try:
        expected = teams_signature(secret_b64, body)
    except (binascii.Error, ValueError) as exc:
        raise SignatureRefused("the stored key is not base64") from exc
    if not hmac.compare_digest(expected.encode(), provided.strip().encode()):
        raise SignatureRefused("signature mismatch")


def check_activity_time(value: object, now: datetime | None = None) -> None:
    """Raise ``SignatureRefused`` unless the signed activity's time is within the window."""
    if not isinstance(value, str) or not value:
        raise SignatureRefused("activity has no timestamp")
    try:
        sent = datetime.fromisoformat(value)
    except ValueError as exc:
        raise SignatureRefused("activity timestamp is not ISO 8601") from exc
    if sent.tzinfo is None:
        sent = sent.replace(tzinfo=UTC)
    current = now or datetime.now(UTC)
    if abs((current - sent).total_seconds()) > MAX_SKEW_SECONDS:
        raise SignatureRefused("stale timestamp")
