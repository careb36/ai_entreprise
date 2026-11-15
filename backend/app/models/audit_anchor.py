"""Anchors of a company's audit chain outside the factory (issue #542).

``docs/design/AUDIT_ANCHORS.md``. The chain (``audit_chain.py``) is keyed by
``AUDIT_SIGNING_KEY``, which the factory holds; an anchor records the chain's
head (a sequence number and its hash) somewhere the factory cannot rewrite it,
so a chain re-signed with the factory's own key no longer matches.

The chain's own rows are not changed: anchors sit beside them.
"""

from datetime import UTC, datetime
from typing import Any

from sqlalchemy import JSON, Index
from sqlmodel import Column, Field, SQLModel


def _utcnow() -> datetime:
    return datetime.now(UTC)


class AuditAnchor(SQLModel, table=True):
    """One chain head recorded outside the factory, and what came back.

    ``kind``: ``webhook`` (posted to the company's endpoint), ``tsa`` (an RFC
    3161 token from the authority the Owner chose) or ``customer_key`` (signed
    by a key the customer holds); ``git_tag`` is designed, not built.
    ``status``: ``pending`` (queued), ``delivered`` (the endpoint answered 2xx:
    its receipt is a claim), ``verified`` (a token or signature the backend
    checked with a key it does not hold) or ``failed``.
    ``anchored_at`` goes into the statement every kind covers
    (``services/platform/audit_anchors.statement``); ``receipt`` holds what
    the outside returned (the token, the signature, the endpoint's answer).
    """

    __tablename__ = "audit_anchors"
    __table_args__ = (
        Index("ix_audit_anchors_company_seq", "company_id", "chain_seq"),
        Index(
            "ux_audit_anchors_company_kind_seq",
            "company_id",
            "kind",
            "chain_seq",
            "key_fingerprint",
            unique=True,
        ),
    )

    id: int | None = Field(default=None, primary_key=True)
    company_id: int = Field(foreign_key="companies.id", index=True)
    kind: str
    chain_seq: int
    chain_hash: str
    anchored_at: datetime = Field(default_factory=_utcnow)
    # Why it was taken: cadence, sprint_close, release, on_demand, customer.
    reason: str = "on_demand"
    status: str = "pending"
    # The webhook delivery id, the TSA's serial number, or the key's fingerprint.
    external_ref: str | None = None
    # The Ed25519 key (customer_key, or a webhook receipt signed by it); empty otherwise.
    key_fingerprint: str = ""
    receipt: Any | None = Field(default=None, sa_column=Column(JSON))
    error: str | None = None
    requested_by: str | None = None
    created_at: datetime = Field(default_factory=_utcnow)
    updated_at: datetime = Field(default_factory=_utcnow)


class AuditAnchorSettings(SQLModel, table=True):
    """The Owner's choices for a company's anchors.

    ``customer_keys``: Ed25519 public keys, each ``{"fingerprint", "public_key",
    "added_at", "retired_at"}``; a retired key still verifies the anchors it
    signed and signs no new ones. ``tsa_cert_pem`` is the timestamping
    authority's certificate, the trust anchor for its tokens.
    """

    __tablename__ = "audit_anchor_settings"

    id: int | None = Field(default=None, primary_key=True)
    company_id: int = Field(foreign_key="companies.id", index=True, unique=True)
    enabled: bool = False
    cadence_minutes: int = 60
    publish_webhook: bool = False
    tsa_url: str | None = None
    tsa_cert_pem: str | None = None
    customer_keys: list | None = Field(default=None, sa_column=Column(JSON))
    updated_by: str | None = None
    updated_at: datetime = Field(default_factory=_utcnow)
