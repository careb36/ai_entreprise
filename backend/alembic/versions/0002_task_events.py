"""Add task_events table for the task lifecycle event log.

Revision ID: 0002
Revises: 0001
Create Date: 2026-11-07 21:00:00 UTC
"""

from __future__ import annotations

import sqlalchemy as sa
import sqlmodel

from alembic import op

revision = "0002"
down_revision = "0001"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "task_events",
        sa.Column("id", sa.Integer(), primary_key=True, nullable=False),
        sa.Column("company_id", sa.Integer(), sa.ForeignKey("companies.id"), nullable=False),
        sa.Column("sprint_id", sa.Integer(), sa.ForeignKey("sprints.id"), nullable=False),
        sa.Column("task_id", sa.Integer(), sa.ForeignKey("backlog_items.id"), nullable=True),
        sa.Column("event_type", sqlmodel.sql.sqltypes.AutoString(), nullable=False),
        sa.Column("payload", sa.JSON(), nullable=True),
        sa.Column("created_at", sa.DateTime(), nullable=False),
    )
    op.create_index("ix_task_events_company_id", "task_events", ["company_id"])
    op.create_index("ix_task_events_sprint_id", "task_events", ["sprint_id"])


def downgrade() -> None:
    op.drop_index("ix_task_events_sprint_id", table_name="task_events")
    op.drop_index("ix_task_events_company_id", table_name="task_events")
    op.drop_table("task_events")