"""Add provider_connections: the model providers a company has connected.

Revision ID: 0005
Revises: 0004
Create Date: 2026-11-08 00:00:00 UTC
"""

from __future__ import annotations

import sqlalchemy as sa
import sqlmodel

from alembic import op

revision = "0005"
down_revision = "0004"
branch_labels = None
depends_on = None


def upgrade() -> None:
    text = sqlmodel.sql.sqltypes.AutoString
    op.create_table(
        "provider_connections",
        sa.Column("id", sa.Integer(), primary_key=True, nullable=False),
        sa.Column(
            "company_id", sa.Integer(), sa.ForeignKey("companies.id"), nullable=False
        ),
        sa.Column("provider", text(), nullable=False),
        sa.Column("name", text(), nullable=False),
        sa.Column("base_url", text(), nullable=False),
        sa.Column("api_key", text(), nullable=True),
        sa.Column("headers", text(), nullable=True),
        sa.Column("enabled", sa.Boolean(), nullable=False),
        sa.Column("validated_at", sa.DateTime(), nullable=True),
        sa.Column("validation_ok", sa.Boolean(), nullable=True),
        sa.Column("validation_detail", text(), nullable=True),
        sa.Column("created_by", text(), nullable=True),
        sa.Column("created_at", sa.DateTime(), nullable=True),
        sa.Column("updated_at", sa.DateTime(), nullable=True),
        sa.UniqueConstraint("company_id", "name"),
    )
    op.create_index(
        "ix_provider_connections_company_id", "provider_connections", ["company_id"]
    )


def downgrade() -> None:
    op.drop_index(
        "ix_provider_connections_company_id", table_name="provider_connections"
    )
    op.drop_table("provider_connections")
