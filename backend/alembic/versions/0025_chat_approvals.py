"""Approving from Slack or Microsoft Teams (issue #512, phase 4;
``docs/design/CHAT_APPROVALS.md``): ``chat_connections`` (a company's Slack app
or Teams webhooks, credentials encrypted), ``chat_identities`` (a chat user
linked to an account in one company), ``chat_link_codes`` (the one-time link
codes, hashed), ``chat_approval_messages`` (what was posted for which approval
point, and its head) and ``chat_interactions`` (inbound ids, once, against
replay). New tables only: nothing existing changes.

Revision ID: 0025
Revises: 0024
Create Date: 2026-11-15 18:00:00 UTC
"""

from __future__ import annotations

import sqlalchemy as sa
import sqlmodel

from alembic import op

revision = "0025"
down_revision = "0024"
branch_labels = None
depends_on = None


def upgrade() -> None:
    text = sqlmodel.sql.sqltypes.AutoString
    op.create_table(
        "chat_connections",
        sa.Column("id", sa.Integer(), primary_key=True, nullable=False),
        sa.Column(
            "company_id", sa.Integer(), sa.ForeignKey("companies.id"), nullable=False
        ),
        sa.Column("provider", text(), nullable=False),
        sa.Column("public_id", text(), nullable=False),
        sa.Column("workspace_id", text(), nullable=False),
        sa.Column("channel", text(), nullable=True),
        sa.Column("post_credential", text(), nullable=True),
        sa.Column("signing_secret", text(), nullable=True),
        sa.Column("enabled", sa.Boolean(), nullable=False),
        sa.Column("tested_at", sa.DateTime(), nullable=True),
        sa.Column("test_ok", sa.Boolean(), nullable=True),
        sa.Column("test_detail", text(), nullable=True),
        sa.Column("last_status", text(), nullable=True),
        sa.Column("last_error", text(), nullable=True),
        sa.Column("created_by", text(), nullable=True),
        sa.Column("created_at", sa.DateTime(), nullable=True),
        sa.Column("updated_at", sa.DateTime(), nullable=True),
        sa.UniqueConstraint("company_id", "provider"),
    )
    op.create_index(
        "ix_chat_connections_company_id", "chat_connections", ["company_id"]
    )
    op.create_index(
        "ix_chat_connections_public_id", "chat_connections", ["public_id"], unique=True
    )

    op.create_table(
        "chat_identities",
        sa.Column("id", sa.Integer(), primary_key=True, nullable=False),
        sa.Column(
            "company_id", sa.Integer(), sa.ForeignKey("companies.id"), nullable=False
        ),
        sa.Column("user_id", sa.Integer(), sa.ForeignKey("users.id"), nullable=False),
        sa.Column("provider", text(), nullable=False),
        sa.Column("workspace_id", text(), nullable=False),
        sa.Column("external_user_id", text(), nullable=False),
        sa.Column("display_name", text(), nullable=True),
        sa.Column("created_at", sa.DateTime(), nullable=True),
        sa.UniqueConstraint(
            "company_id", "provider", "workspace_id", "external_user_id"
        ),
        sa.UniqueConstraint("company_id", "provider", "user_id"),
    )
    op.create_index("ix_chat_identities_company_id", "chat_identities", ["company_id"])
    op.create_index("ix_chat_identities_user_id", "chat_identities", ["user_id"])

    op.create_table(
        "chat_link_codes",
        sa.Column("id", sa.Integer(), primary_key=True, nullable=False),
        sa.Column(
            "company_id", sa.Integer(), sa.ForeignKey("companies.id"), nullable=False
        ),
        sa.Column("user_id", sa.Integer(), sa.ForeignKey("users.id"), nullable=False),
        sa.Column("provider", text(), nullable=False),
        sa.Column("code_hash", text(), nullable=False),
        sa.Column("expires_at", sa.DateTime(), nullable=False),
        sa.Column("created_at", sa.DateTime(), nullable=True),
    )
    op.create_index("ix_chat_link_codes_company_id", "chat_link_codes", ["company_id"])
    op.create_index("ix_chat_link_codes_user_id", "chat_link_codes", ["user_id"])
    op.create_index(
        "ix_chat_link_codes_code_hash", "chat_link_codes", ["code_hash"], unique=True
    )

    op.create_table(
        "chat_approval_messages",
        sa.Column("id", sa.Integer(), primary_key=True, nullable=False),
        sa.Column(
            "company_id", sa.Integer(), sa.ForeignKey("companies.id"), nullable=False
        ),
        sa.Column(
            "connection_id",
            sa.Integer(),
            sa.ForeignKey("chat_connections.id"),
            nullable=False,
        ),
        sa.Column("provider", text(), nullable=False),
        sa.Column(
            "task_id", sa.Integer(), sa.ForeignKey("backlog_items.id"), nullable=False
        ),
        sa.Column(
            "run_id", sa.Integer(), sa.ForeignKey("agent_runs.id"), nullable=False
        ),
        sa.Column("run_version", sa.Integer(), nullable=False),
        sa.Column("hold_event_id", sa.Integer(), nullable=False),
        sa.Column("head_hash", text(), nullable=False),
        sa.Column("short_code", text(), nullable=False),
        sa.Column("channel", text(), nullable=True),
        sa.Column("message_ts", text(), nullable=True),
        sa.Column("status", text(), nullable=False),
        sa.Column("decision", text(), nullable=True),
        sa.Column("decided_by_user_id", sa.Integer(), nullable=True),
        sa.Column("decided_at", sa.DateTime(), nullable=True),
        sa.Column("outcome", text(), nullable=True),
        sa.Column("created_at", sa.DateTime(), nullable=True),
        sa.UniqueConstraint("connection_id", "hold_event_id"),
    )
    op.create_index(
        "ix_chat_approval_messages_company_id", "chat_approval_messages", ["company_id"]
    )
    op.create_index(
        "ix_chat_approval_messages_connection_id",
        "chat_approval_messages",
        ["connection_id"],
    )
    op.create_index(
        "ix_chat_approval_messages_task_id", "chat_approval_messages", ["task_id"]
    )
    op.create_index(
        "ix_chat_approval_messages_short_code",
        "chat_approval_messages",
        ["short_code"],
        unique=True,
    )

    op.create_table(
        "chat_interactions",
        sa.Column("id", sa.Integer(), primary_key=True, nullable=False),
        sa.Column("provider", text(), nullable=False),
        sa.Column("interaction_key", text(), nullable=False),
        sa.Column("connection_id", sa.Integer(), nullable=True),
        sa.Column("received_at", sa.DateTime(), nullable=True),
        sa.UniqueConstraint("provider", "interaction_key"),
    )


def downgrade() -> None:
    op.drop_table("chat_interactions")
    for name in ("short_code", "task_id", "connection_id", "company_id"):
        op.drop_index(
            f"ix_chat_approval_messages_{name}", table_name="chat_approval_messages"
        )
    op.drop_table("chat_approval_messages")
    for name in ("code_hash", "user_id", "company_id"):
        op.drop_index(f"ix_chat_link_codes_{name}", table_name="chat_link_codes")
    op.drop_table("chat_link_codes")
    op.drop_index("ix_chat_identities_user_id", table_name="chat_identities")
    op.drop_index("ix_chat_identities_company_id", table_name="chat_identities")
    op.drop_table("chat_identities")
    op.drop_index("ix_chat_connections_public_id", table_name="chat_connections")
    op.drop_index("ix_chat_connections_company_id", table_name="chat_connections")
    op.drop_table("chat_connections")
