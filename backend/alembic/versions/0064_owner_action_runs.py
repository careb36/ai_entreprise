"""Owner actions (issue #701, ``docs/design/owner-actions.md``):
``owner_action_runs``, one row per run of a catalogue action an Owner
started from the app. A new table only: nothing existing changes.

Follows 0063 (routine revisions).

Revision ID: 0064
Revises: 0063
Create Date: 2026-11-24 12:00:00 UTC
"""

from __future__ import annotations

import sqlalchemy as sa
import sqlmodel

from alembic import op

revision = "0064"
down_revision = "0063"
branch_labels = None
depends_on = None


def upgrade() -> None:
    text = sqlmodel.sql.sqltypes.AutoString
    op.create_table(
        "owner_action_runs",
        sa.Column("id", sa.Integer(), primary_key=True, nullable=False),
        sa.Column(
            "company_id", sa.Integer(), sa.ForeignKey("companies.id"), nullable=False
        ),
        sa.Column("action", text(), nullable=False),
        sa.Column("status", text(), nullable=False),
        sa.Column("params", sa.JSON(), nullable=True),
        sa.Column(
            "started_by_user_id", sa.Integer(), sa.ForeignKey("users.id"), nullable=False
        ),
        sa.Column("started_at", sa.DateTime(), nullable=False),
        sa.Column("finished_at", sa.DateTime(), nullable=True),
        sa.Column("cancel_requested_at", sa.DateTime(), nullable=True),
        sa.Column(
            "cancel_requested_by_user_id",
            sa.Integer(),
            sa.ForeignKey("users.id"),
            nullable=True,
        ),
        sa.Column("exit_status", sa.Integer(), nullable=True),
        sa.Column("reason", text(), nullable=True),
        sa.Column("observations", sa.JSON(), nullable=True),
    )
    op.create_index(
        "ix_owner_action_runs_action_status", "owner_action_runs", ["action", "status"]
    )
    op.create_index(
        "ix_owner_action_runs_company_id", "owner_action_runs", ["company_id"]
    )


def downgrade() -> None:
    op.drop_index("ix_owner_action_runs_company_id", table_name="owner_action_runs")
    op.drop_index("ix_owner_action_runs_action_status", table_name="owner_action_runs")
    op.drop_table("owner_action_runs")
