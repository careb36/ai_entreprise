"""Single sign-on over OpenID Connect (issue #512, phase 3; ``docs/design/SSO.md``).

- ``SsoConnection``  — a company's identity provider: one per company, written
  by an Owner. The client secret is encrypted with the Fernet key
  (``crypto.py``) and never leaves the backend through the API.
- ``SsoIdentity``    — an account's identity at a provider, ``(issuer, subject)``,
  never the email: once linked, it always resolves to the same account.
- ``SsoFlowState``   — the one-time values of a sign-in in flight: the login
  ``state`` (with its PKCE verifier, nonce and browser binding) and the
  handoff code the frontend exchanges for an access token. Only sha256 of
  the values the browser carries is stored.
- ``SsoDomain``      — an allowed domain's DNS verification: the token the
  backend issued for the company to publish, and the status the last
  definite DNS answer left (``services/platform/sso_domains.py``).
- ``SsoDomainCheck`` — each DNS check, append-only: when, why (the Owner, the
  re-check, a newer claim), the outcome and the TXT strings seen. The
  observation; the Owner's listing of a domain is only the claim.
"""

from datetime import UTC, datetime
from typing import Any

from sqlalchemy import JSON, Column, UniqueConstraint
from sqlmodel import Field, SQLModel


def _utcnow() -> datetime:
    return datetime.now(UTC).replace(tzinfo=None)


class SsoConnection(SQLModel, table=True):
    __tablename__ = "sso_connections"

    id: int | None = Field(default=None, primary_key=True)
    company_id: int = Field(foreign_key="companies.id", unique=True, index=True)
    # What the login page's "Sign in with SSO" takes, beside an email domain.
    slug: str = Field(unique=True, index=True)
    # "generic" | "google" | "entra": a preset only fills the issuer.
    preset: str = "generic"
    tenant: str | None = None  # Entra's tenant id, for the issuer template
    issuer: str
    client_id: str
    client_secret: str | None = None  # crypto.encrypt_value; never returned
    allowed_domains: list[Any] = Field(default_factory=list, sa_column=Column(JSON))
    default_role: str = "Viewer"  # "Viewer" | "Manager"; never "Owner"
    auto_join: bool = False
    enabled: bool = False
    require_sso: bool = False
    # The last test: an observation of the provider (discovery and JWKS).
    tested_at: datetime | None = None
    test_ok: bool | None = None
    test_detail: str | None = None
    created_by: str | None = None
    created_at: datetime | None = Field(default_factory=_utcnow)
    updated_at: datetime | None = Field(default_factory=_utcnow)


class SsoIdentity(SQLModel, table=True):
    __tablename__ = "sso_identities"
    __table_args__ = (UniqueConstraint("issuer", "subject"),)

    id: int | None = Field(default=None, primary_key=True)
    user_id: int = Field(foreign_key="users.id", index=True)
    connection_id: int | None = Field(default=None, index=True)
    issuer: str
    subject: str
    email: str | None = None  # the address the provider gave at the last sign-in
    created_at: datetime | None = Field(default_factory=_utcnow)
    last_login_at: datetime | None = None


class SsoFlowState(SQLModel, table=True):
    __tablename__ = "sso_flow_states"

    id: int | None = Field(default=None, primary_key=True)
    kind: str  # "login" | "handoff"
    key_hash: str = Field(unique=True, index=True)
    connection_id: int | None = None
    user_id: int | None = None
    nonce: str | None = None
    code_verifier: str | None = None
    binding_hash: str | None = None
    expires_at: datetime
    created_at: datetime | None = Field(default_factory=_utcnow)


class SsoDomain(SQLModel, table=True):
    __tablename__ = "sso_domains"
    __table_args__ = (UniqueConstraint("company_id", "domain"),)

    id: int | None = Field(default=None, primary_key=True)
    company_id: int = Field(foreign_key="companies.id", index=True)
    domain: str = Field(index=True)
    # Issued by the backend (secrets.token_urlsafe), never chosen by the Owner.
    token: str
    # Listed before verification existed (migration 0049): usable during
    # the grace period (SSO_LEGACY_DOMAIN_GRACE, SSO_LEGACY_DOMAIN_GRACE_DAYS).
    legacy: bool = False
    status: str = "unverified"  # "unverified" | "verified"
    verified_at: datetime | None = None  # the check that found the token
    last_checked_at: datetime | None = None
    last_outcome: str | None = None
    created_at: datetime | None = Field(default_factory=_utcnow)


class SsoDomainCheck(SQLModel, table=True):
    __tablename__ = "sso_domain_checks"

    id: int | None = Field(default=None, primary_key=True)
    # No foreign key to sso_domains: the history outlives a removed domain.
    company_id: int = Field(foreign_key="companies.id", index=True)
    domain: str = Field(index=True)
    checked_at: datetime = Field(default_factory=_utcnow)
    trigger: str  # "owner" | "recheck" | "conflict"
    requested_by: str | None = None  # the Owner's email, for "owner"
    # "verified" | "no_match" | "not_found" | "unavailable" | "verified_elsewhere"
    outcome: str
    answer: list[Any] = Field(default_factory=list, sa_column=Column(JSON))
    detail: str | None = None
