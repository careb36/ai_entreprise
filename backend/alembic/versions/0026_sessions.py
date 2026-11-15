"""Browser sessions (issue #524; ``docs/design/SESSIONS.md``):
``session_families`` (one sign-in in one browser, its CSRF digest, absolute
expiry and revocation) and ``refresh_tokens`` (each rotation's token, stored
as sha256). New tables only; Bearer callers are untouched.

Revision ID: 0026
Revises: 0025
Create Date: 2026-11-15 18:00:00 UTC
"""

from __future__ import annotations

import sqlalchemy as sa
import sqlmodel

from alembic import op

revision = "0026"
down_revision = "0025"
branch_labels = None
depends_on = None


def upgrade() -> None:
    text = sqlmodel.sql.sqltypes.AutoString
    op.create_table(
        "session_families",
        sa.Column("id", sa.Integer(), primary_key=True, nullable=False),
        sa.Column("public_id", text(), nullable=False),
        sa.Column("user_id", sa.Integer(), sa.ForeignKey("users.id"), nullable=False),
        sa.Column("method", text(), nullable=False),
        sa.Column("csrf_hash", text(), nullable=False),
        sa.Column("created_at", sa.DateTime(), nullable=False),
        sa.Column("expires_at", sa.DateTime(), nullable=False),
        sa.Column("last_used_at", sa.DateTime(), nullable=True),
        sa.Column("revoked_at", sa.DateTime(), nullable=True),
        sa.Column("revoked_reason", text(), nullable=True),
    )
    op.create_index(
        "ix_session_families_public_id",
        "session_families",
        ["public_id"],
        unique=True,
    )
    op.create_index("ix_session_families_user_id", "session_families", ["user_id"])

    op.create_table(
        "refresh_tokens",
        sa.Column("id", sa.Integer(), primary_key=True, nullable=False),
        sa.Column(
            "family_id",
            sa.Integer(),
            sa.ForeignKey("session_families.id"),
            nullable=False,
        ),
        sa.Column("token_hash", text(), nullable=False),
        sa.Column("created_at", sa.DateTime(), nullable=False),
        sa.Column("used_at", sa.DateTime(), nullable=True),
    )
    op.create_index(
        "ix_refresh_tokens_token_hash", "refresh_tokens", ["token_hash"], unique=True
    )
    op.create_index("ix_refresh_tokens_family_id", "refresh_tokens", ["family_id"])


def downgrade() -> None:
    op.drop_index("ix_refresh_tokens_family_id", table_name="refresh_tokens")
    op.drop_index("ix_refresh_tokens_token_hash", table_name="refresh_tokens")
    op.drop_table("refresh_tokens")
    op.drop_index("ix_session_families_user_id", table_name="session_families")
    op.drop_index("ix_session_families_public_id", table_name="session_families")
    op.drop_table("session_families")
