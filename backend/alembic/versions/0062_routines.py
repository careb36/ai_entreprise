"""A company's routines (issue #774): ``routines``, a task that runs on a
daily or weekly schedule, opening a small sprint each time. A new table only:
nothing existing changes.

Follows 0061 (company limits).

Revision ID: 0062
Revises: 0061
Create Date: 2026-11-22 14:00:00 UTC
"""

from __future__ import annotations

import sqlalchemy as sa
import sqlmodel

from alembic import op

revision = "0062"
down_revision = "0061"
branch_labels = None
depends_on = None


def upgrade() -> None:
    text = sqlmodel.sql.sqltypes.AutoString
    op.create_table(
        "routines",
        sa.Column("id", sa.Integer(), primary_key=True, nullable=False),
        sa.Column(
            "company_id", sa.Integer(), sa.ForeignKey("companies.id"), nullable=False
        ),
        sa.Column("title", text(), nullable=False),
        sa.Column("description", sa.Text(), nullable=False),
        sa.Column("assignee", text(), nullable=False),
        sa.Column("schedule", sa.JSON(), nullable=False),
        sa.Column("enabled", sa.Boolean(), nullable=False),
        sa.Column("next_run_at", sa.DateTime(), nullable=True),
        sa.Column("last_run_at", sa.DateTime(), nullable=True),
        sa.Column(
            "last_sprint_id", sa.Integer(), sa.ForeignKey("sprints.id"), nullable=True
        ),
        sa.Column(
            "created_by_user_id", sa.Integer(), sa.ForeignKey("users.id"), nullable=True
        ),
        sa.Column("created_at", sa.DateTime(), nullable=False),
    )
    op.create_index("ix_routines_company_id", "routines", ["company_id"])
    op.create_index("ix_routines_next_run_at", "routines", ["next_run_at"])


def downgrade() -> None:
    op.drop_index("ix_routines_next_run_at", table_name="routines")
    op.drop_index("ix_routines_company_id", table_name="routines")
    op.drop_table("routines")
