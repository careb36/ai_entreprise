"""Approving from Slack or Microsoft Teams (issue #512, phase 4; ``docs/archive/design/CHAT_APPROVALS.md``).

- ``ChatConnection``      — a company's Slack app or Teams webhooks, written by
  an Owner. The credential that posts (Slack bot token, Teams incoming webhook
  URL) and the key that verifies inbound requests (Slack signing secret, Teams
  outgoing webhook key) are encrypted with the Fernet key and never returned.
- ``ChatIdentity``        — a chat user linked to a La Fabrique account in one
  company, by the one-time code the person asked for while signed in.
- ``ChatLinkCode``        — that code, as sha256, until it is used or expires.
- ``ChatApprovalMessage`` — a message posted for one approval point, with what
  it was posted for (the task's event-chain head and the run's version), so a
  click on an older message is refused.
- ``ChatInteraction``     — every inbound interaction's id, once: a replay is
  refused by the unique key.
"""

from datetime import UTC, datetime

from sqlalchemy import UniqueConstraint
from sqlmodel import Field, SQLModel


def _utcnow() -> datetime:
    return datetime.now(UTC).replace(tzinfo=None)


class ChatConnection(SQLModel, table=True):
    __tablename__ = "chat_connections"
    __table_args__ = (UniqueConstraint("company_id", "provider"),)

    id: int | None = Field(default=None, primary_key=True)
    company_id: int = Field(foreign_key="companies.id", index=True)
    provider: str  # "slack" | "teams"
    # The opaque routing key in the inbound URL; not a secret.
    public_id: str = Field(unique=True, index=True)
    # Slack's workspace (team) id, or the Teams tenant id: inbound requests
    # from any other workspace are refused.
    workspace_id: str
    channel: str | None = None  # Slack channel id; unused for Teams
    post_credential: str | None = None  # crypto.encrypt_value; never returned
    signing_secret: str | None = None  # crypto.encrypt_value; never returned
    enabled: bool = False
    tested_at: datetime | None = None
    test_ok: bool | None = None
    test_detail: str | None = None
    last_status: str | None = None
    last_error: str | None = None
    created_by: str | None = None
    created_at: datetime | None = Field(default_factory=_utcnow)
    updated_at: datetime | None = Field(default_factory=_utcnow)


class ChatIdentity(SQLModel, table=True):
    __tablename__ = "chat_identities"
    __table_args__ = (
        UniqueConstraint("company_id", "provider", "workspace_id", "external_user_id"),
        UniqueConstraint("company_id", "provider", "user_id"),
    )

    id: int | None = Field(default=None, primary_key=True)
    company_id: int = Field(foreign_key="companies.id", index=True)
    user_id: int = Field(foreign_key="users.id", index=True)
    provider: str
    workspace_id: str
    external_user_id: str
    display_name: str | None = None
    created_at: datetime | None = Field(default_factory=_utcnow)


class ChatLinkCode(SQLModel, table=True):
    __tablename__ = "chat_link_codes"

    id: int | None = Field(default=None, primary_key=True)
    company_id: int = Field(foreign_key="companies.id", index=True)
    user_id: int = Field(foreign_key="users.id", index=True)
    provider: str
    code_hash: str = Field(unique=True, index=True)
    expires_at: datetime
    created_at: datetime | None = Field(default_factory=_utcnow)


class ChatApprovalMessage(SQLModel, table=True):
    __tablename__ = "chat_approval_messages"
    __table_args__ = (UniqueConstraint("connection_id", "hold_event_id"),)

    id: int | None = Field(default=None, primary_key=True)
    company_id: int = Field(foreign_key="companies.id", index=True)
    connection_id: int = Field(foreign_key="chat_connections.id", index=True)
    provider: str
    task_id: int = Field(foreign_key="backlog_items.id", index=True)
    run_id: int = Field(foreign_key="agent_runs.id")
    run_version: int
    hold_event_id: int
    head_hash: str  # the task's event-chain head when the message was posted
    # What a person types in Teams, and Slack's button value with the head.
    short_code: str = Field(unique=True, index=True)
    channel: str | None = None
    message_ts: str | None = None  # Slack's message id once posted
    # "queued" | "posted" | "deciding" | "decided" | "stale"
    status: str = "queued"
    decision: str | None = None
    decided_by_user_id: int | None = None
    decided_at: datetime | None = None
    outcome: str | None = None
    created_at: datetime | None = Field(default_factory=_utcnow)


class ChatInteraction(SQLModel, table=True):
    __tablename__ = "chat_interactions"
    __table_args__ = (UniqueConstraint("provider", "interaction_key"),)

    id: int | None = Field(default=None, primary_key=True)
    provider: str
    interaction_key: str
    connection_id: int | None = None
    received_at: datetime | None = Field(default_factory=_utcnow)
