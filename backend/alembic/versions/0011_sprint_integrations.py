"""Add sprint_integrations: a sprint's task branches merged and tested (issue #314).

Revision ID: 0011
Revises: 0010
Create Date: 2026-11-08 18:00:00 UTC
"""

from __future__ import annotations

import sqlalchemy as sa
import sqlmodel

from alembic import op

revision = "0011"
down_revision = "0010"
branch_labels = None
depends_on = None


def upgrade() -> None:
    text = sqlmodel.sql.sqltypes.AutoString
    op.create_table(
        "sprint_integrations",
        sa.Column("id", sa.Integer(), primary_key=True, nullable=False),
        sa.Column(
            "company_id", sa.Integer(), sa.ForeignKey("companies.id"), nullable=False
        ),
        sa.Column(
            "sprint_id", sa.Integer(), sa.ForeignKey("sprints.id"), nullable=False
        ),
        sa.Column("trigger", text(), nullable=False),
        sa.Column("requested_by", text(), nullable=False),
        sa.Column("status", text(), nullable=False),
        sa.Column("branch", text(), nullable=False),
        sa.Column("base", text(), nullable=False),
        sa.Column("repository", text(), nullable=True),
        sa.Column("worktree", text(), nullable=True),
        sa.Column("start_commit", text(), nullable=True),
        sa.Column("commit", text(), nullable=True),
        sa.Column("merged", sa.JSON(), nullable=True),
        sa.Column("conflict", sa.JSON(), nullable=True),
        sa.Column("test_run", sa.JSON(), nullable=True),
        sa.Column("details", sa.JSON(), nullable=True),
        sa.Column("publication", sa.JSON(), nullable=True),
        sa.Column("created_at", sa.DateTime(), nullable=False),
        sa.Column(
            "audit_log_id", sa.Integer(), sa.ForeignKey("audit_log.id"), nullable=True
        ),
    )
    op.create_index(
        "ix_sprint_integrations_company_id", "sprint_integrations", ["company_id"]
    )
    op.create_index(
        "ix_sprint_integrations_sprint_id", "sprint_integrations", ["sprint_id"]
    )


def downgrade() -> None:
    op.drop_index("ix_sprint_integrations_sprint_id", table_name="sprint_integrations")
    op.drop_index("ix_sprint_integrations_company_id", table_name="sprint_integrations")
    op.drop_table("sprint_integrations")
