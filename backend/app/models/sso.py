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
"""

from datetime import datetime
from typing import Any

from sqlalchemy import JSON, Column, UniqueConstraint
from sqlmodel import Field, SQLModel


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
    created_at: datetime | None = Field(default_factory=datetime.utcnow)
    updated_at: datetime | None = Field(default_factory=datetime.utcnow)


class SsoIdentity(SQLModel, table=True):
    __tablename__ = "sso_identities"
    __table_args__ = (UniqueConstraint("issuer", "subject"),)

    id: int | None = Field(default=None, primary_key=True)
    user_id: int = Field(foreign_key="users.id", index=True)
    connection_id: int | None = Field(default=None, index=True)
    issuer: str
    subject: str
    email: str | None = None  # the address the provider gave at the last sign-in
    created_at: datetime | None = Field(default_factory=datetime.utcnow)
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
    created_at: datetime | None = Field(default_factory=datetime.utcnow)
