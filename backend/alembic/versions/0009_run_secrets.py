"""Add run_secrets table for per-run secrets (issue #357).

Revision ID: 0009
Revises: 0008
Create Date: 2026-11-08 04:00:00 UTC
"""

from __future__ import annotations

import sqlalchemy as sa
import sqlmodel

from alembic import op

revision = "0009"
down_revision = "0008"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "run_secrets",
        sa.Column("id", sa.Integer(), primary_key=True, nullable=False),
        sa.Column(
            "company_id", sa.Integer(), sa.ForeignKey("companies.id"), nullable=False
        ),
        sa.Column(
            "task_id", sa.Integer(), sa.ForeignKey("backlog_items.id"), nullable=False
        ),
        sa.Column("name", sqlmodel.sql.sqltypes.AutoString(), nullable=False),
        sa.Column(
            "encrypted_value", sqlmodel.sql.sqltypes.AutoString(), nullable=False
        ),
        sa.Column("created_at", sa.DateTime(), nullable=False),
    )
    op.create_index("ix_run_secrets_company_id", "run_secrets", ["company_id"])
    op.create_index("ix_run_secrets_task_id", "run_secrets", ["task_id"])


def downgrade() -> None:
    op.drop_index("ix_run_secrets_task_id", table_name="run_secrets")
    op.drop_index("ix_run_secrets_company_id", table_name="run_secrets")
    op.drop_table("run_secrets")
