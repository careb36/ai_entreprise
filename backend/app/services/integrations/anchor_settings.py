"""The Owner's settings for a company's audit anchors (issue #542).

``docs/archive/design/AUDIT_ANCHORS.md``. What may be changed, and the checks each
value passes before it is stored:

- ``enabled`` and ``cadence_minutes`` (15 to 1440): whether heads are anchored
  on a schedule, and how often.
- ``publish_webhook``: post each head to the company's notification webhook
  (``webhooks.py``: the same URL, secret, signature and address check).
- ``tsa_url`` and ``tsa_cert_pem``: the RFC 3161 authority and its
  certificate, the trust anchor for its tokens. The URL may be http or https
  (a token is signed by the authority, so the transport adds nothing to it)
  and passes the webhook's address check; the certificate must be a
  time-stamping one.
- ``add_public_key`` and ``retire_fingerprint``: the customer's Ed25519 keys.
  A retired key stays, so the anchors it signed still verify.

Nothing here is secret: a public key and a certificate are public.
"""

from __future__ import annotations

from datetime import UTC, datetime
from typing import Any

from cryptography import x509
from cryptography.hazmat.primitives import hashes
from cryptography.x509.oid import ExtendedKeyUsageOID
from models import AuditAnchorSettings
from sqlmodel import Session

from services.integrations import webhooks
from services.platform import audit_anchors, rfc3161

MIN_CADENCE, MAX_CADENCE = 15, 1440
MAX_ACTIVE_KEYS = 5
MAX_PEM_CHARS = 16_000


class InvalidAnchorSettings(ValueError):
    """The settings asked for cannot be saved; the message says why."""


def get(session: Session, company_id: int) -> AuditAnchorSettings | None:
    return audit_anchors.settings_for(session, company_id)


def describe(session: Session, company_id: int) -> dict[str, Any]:
    row = get(session, company_id)
    hook = webhooks.get(session, company_id)
    cert_sha256 = None
    if row is not None and row.tsa_cert_pem:
        cert_sha256 = (
            rfc3161.load_certificate(row.tsa_cert_pem)
            .fingerprint(hashes.SHA256())
            .hex()
        )
    return {
        "company_id": company_id,
        "enabled": bool(row and row.enabled),
        "cadence_minutes": row.cadence_minutes if row else 60,
        "publish_webhook": bool(row and row.publish_webhook),
        "webhook_configured": bool(hook and hook.enabled),
        "tsa_url": row.tsa_url if row else None,
        "tsa_cert_sha256": cert_sha256,
        "customer_keys": [
            {
                k: entry.get(k)
                for k in ("fingerprint", "public_key", "added_at", "retired_at")
            }
            for entry in ((row.customer_keys if row else None) or [])
        ],
        "kinds": list(audit_anchors.KINDS),
        "designed_not_built": list(audit_anchors.DESIGNED_KINDS),
        "updated_by": row.updated_by if row else None,
        "updated_at": row.updated_at if row else None,
    }


def _check_cert(pem: str) -> str:
    pem = pem.strip()
    if len(pem) > MAX_PEM_CHARS:
        raise InvalidAnchorSettings("tsa_cert_pem: too long")
    try:
        cert = rfc3161.load_certificate(pem)
        usage = cert.extensions.get_extension_for_class(x509.ExtendedKeyUsage).value
    except (rfc3161.TimestampError, x509.ExtensionNotFound) as exc:
        raise InvalidAnchorSettings(
            "tsa_cert_pem: not a PEM time-stamping certificate"
        ) from exc
    if ExtendedKeyUsageOID.TIME_STAMPING not in usage:
        raise InvalidAnchorSettings("tsa_cert_pem: not a time-stamping certificate")
    return pem


def save(
    session: Session, company_id: int, changes: dict[str, Any], updated_by: str
) -> AuditAnchorSettings:
    """Apply *changes* (only the keys given) to the company's settings; commits."""
    row = get(session, company_id) or AuditAnchorSettings(company_id=company_id)
    if "enabled" in changes:
        row.enabled = bool(changes["enabled"])
    if "publish_webhook" in changes:
        row.publish_webhook = bool(changes["publish_webhook"])
    if "cadence_minutes" in changes:
        cadence = changes["cadence_minutes"]
        if not isinstance(cadence, int) or not MIN_CADENCE <= cadence <= MAX_CADENCE:
            raise InvalidAnchorSettings(
                f"cadence_minutes: between {MIN_CADENCE} and {MAX_CADENCE}"
            )
        row.cadence_minutes = cadence
    if "tsa_url" in changes:
        url = (changes["tsa_url"] or "").strip()
        try:
            row.tsa_url = (
                webhooks.check_url(url, schemes=("https", "http")) if url else None
            )
        except webhooks.InvalidWebhook as exc:
            raise InvalidAnchorSettings(f"tsa_{exc}") from exc
    if "tsa_cert_pem" in changes:
        pem = (changes["tsa_cert_pem"] or "").strip()
        row.tsa_cert_pem = _check_cert(pem) if pem else None
    if row.tsa_url and not row.tsa_cert_pem:
        raise InvalidAnchorSettings(
            "tsa_cert_pem: the authority's certificate is needed to check its tokens"
        )

    keys = [dict(entry) for entry in (row.customer_keys or [])]
    now = datetime.now(UTC).isoformat()
    if changes.get("add_public_key"):
        try:
            fingerprint, pem = audit_anchors.parse_public_key(changes["add_public_key"])
        except audit_anchors.InvalidAnchor as exc:
            raise InvalidAnchorSettings(str(exc)) from exc
        if any(entry["fingerprint"] == fingerprint for entry in keys):
            raise InvalidAnchorSettings("public_key: already registered")
        if sum(1 for entry in keys if not entry.get("retired_at")) >= MAX_ACTIVE_KEYS:
            raise InvalidAnchorSettings(
                f"public_key: at most {MAX_ACTIVE_KEYS} active keys; retire one first"
            )
        keys.append(
            {
                "fingerprint": fingerprint,
                "public_key": pem,
                "added_at": now,
                "retired_at": None,
            }
        )
    if changes.get("retire_fingerprint"):
        target = next(
            (e for e in keys if e["fingerprint"] == changes["retire_fingerprint"]), None
        )
        if target is None:
            raise InvalidAnchorSettings("retire_fingerprint: no such key")
        target["retired_at"] = target.get("retired_at") or now
    row.customer_keys = keys
    row.updated_by = updated_by
    row.updated_at = datetime.now(UTC)
    session.add(row)
    session.commit()
    session.refresh(row)
    return row
