"""Personal notifications: one row per receiver, and each person's
preferences per kind (issue #683; ``services/platform/notifications.py``).

New tables only. ``personal_notifications`` holds a reference to what a
notice is about (``subject_type``, ``subject_id``, a few integer ids in
``context``), never a copy of its text; ``notification_preferences`` one row
per person and kind they changed. Nothing existing changes.

Follows 0057 (vector memory embedding model).

Revision ID: 0058
Revises: 0057
Create Date: 2026-11-20 16:00:00 UTC
"""

from __future__ import annotations

import sqlalchemy as sa
import sqlmodel

from alembic import op

revision = "0058"
down_revision = "0057"
branch_labels = None
depends_on = None


def upgrade() -> None:
    text = sqlmodel.sql.sqltypes.AutoString
    op.create_table(
        "personal_notifications",
        sa.Column("id", sa.Integer(), primary_key=True, nullable=False),
        sa.Column("user_id", sa.Integer(), sa.ForeignKey("users.id"), nullable=False),
        sa.Column(
            "company_id", sa.Integer(), sa.ForeignKey("companies.id"), nullable=False
        ),
        sa.Column("kind", text(), nullable=False),
        sa.Column("subject_type", text(), nullable=False),
        sa.Column("subject_id", sa.Integer(), nullable=False),
        sa.Column("context", sa.JSON(), nullable=True),
        sa.Column("dedupe_key", text(), nullable=True),
        sa.Column("created_at", sa.DateTime(), nullable=False),
        sa.Column("read_at", sa.DateTime(), nullable=True),
        sa.Column("snoozed_until", sa.DateTime(), nullable=True),
    )
    op.create_index(
        "ix_personal_notifications_company_id", "personal_notifications", ["company_id"]
    )
    op.create_index(
        "ix_personal_notifications_created_at", "personal_notifications", ["created_at"]
    )
    op.create_index(
        "ix_personal_notifications_receiver",
        "personal_notifications",
        ["user_id", "company_id"],
    )
    op.create_index(
        "ix_personal_notifications_dedupe",
        "personal_notifications",
        ["user_id", "dedupe_key"],
    )
    op.create_table(
        "notification_preferences",
        sa.Column("id", sa.Integer(), primary_key=True, nullable=False),
        sa.Column("user_id", sa.Integer(), sa.ForeignKey("users.id"), nullable=False),
        sa.Column("kind", text(), nullable=False),
        sa.Column("in_app", sa.Boolean(), nullable=False),
        sa.Column("email", sa.Boolean(), nullable=False),
        sa.Column("updated_at", sa.DateTime(), nullable=False),
        sa.UniqueConstraint("user_id", "kind", name="uq_notification_preferences"),
    )
    op.create_index(
        "ix_notification_preferences_user_id", "notification_preferences", ["user_id"]
    )


def downgrade() -> None:
    op.drop_index(
        "ix_notification_preferences_user_id", table_name="notification_preferences"
    )
    op.drop_table("notification_preferences")
    for name in ("dedupe", "receiver", "created_at", "company_id"):
        op.drop_index(
            f"ix_personal_notifications_{name}", table_name="personal_notifications"
        )
    op.drop_table("personal_notifications")
