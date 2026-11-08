"""Add company_webhooks: notify a company when work finishes (issue #357).

Revision ID: 0010
Revises: 0009
Create Date: 2026-11-08 13:00:00 UTC
"""

from __future__ import annotations

import sqlalchemy as sa
import sqlmodel

from alembic import op

revision = "0010"
down_revision = "0009"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "company_webhooks",
        sa.Column("id", sa.Integer(), primary_key=True, nullable=False),
        sa.Column(
            "company_id", sa.Integer(), sa.ForeignKey("companies.id"), nullable=False
        ),
        sa.Column("url", sqlmodel.sql.sqltypes.AutoString(), nullable=False),
        sa.Column(
            "encrypted_secret", sqlmodel.sql.sqltypes.AutoString(), nullable=False
        ),
        sa.Column("events", sa.JSON(), nullable=True),
        sa.Column("enabled", sa.Boolean(), nullable=False),
        sa.Column("created_at", sa.DateTime(), nullable=False),
        sa.Column("updated_at", sa.DateTime(), nullable=False),
        sa.Column("last_delivery_at", sa.DateTime(), nullable=True),
        sa.Column("last_status", sqlmodel.sql.sqltypes.AutoString(), nullable=True),
        sa.Column("last_error", sqlmodel.sql.sqltypes.AutoString(), nullable=True),
    )
    op.create_index(
        "ix_company_webhooks_company_id",
        "company_webhooks",
        ["company_id"],
        unique=True,
    )


def downgrade() -> None:
    op.drop_index("ix_company_webhooks_company_id", table_name="company_webhooks")
    op.drop_table("company_webhooks")
