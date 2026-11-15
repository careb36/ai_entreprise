"""Sprint progress snapshots (issue #531): ``sprint_progress_snapshots``, one
row per close of a sprint with the time series built from its task events,
the retrospective's numbers, the sha256 the backend computed over both and
the audit row that seals it. A new table only: nothing existing changes.

Numbered 0099 so it cannot collide with the migrations of parallel work; it
is renumbered when merged.

Revision ID: 0099
Revises: 0027
Create Date: 2026-11-15 18:00:00 UTC
"""

from __future__ import annotations

import sqlalchemy as sa
import sqlmodel

from alembic import op

revision = "0099"
down_revision = "0027"
branch_labels = None
depends_on = None


def upgrade() -> None:
    text = sqlmodel.sql.sqltypes.AutoString
    op.create_table(
        "sprint_progress_snapshots",
        sa.Column("id", sa.Integer(), primary_key=True, nullable=False),
        sa.Column(
            "company_id", sa.Integer(), sa.ForeignKey("companies.id"), nullable=False
        ),
        sa.Column(
            "sprint_id", sa.Integer(), sa.ForeignKey("sprints.id"), nullable=False
        ),
        sa.Column("close_number", sa.Integer(), nullable=False),
        sa.Column("series", sa.JSON(), nullable=True),
        sa.Column("retrospective", sa.JSON(), nullable=True),
        sa.Column("digest", text(), nullable=False),
        sa.Column(
            "audit_log_id",
            sa.Integer(),
            sa.ForeignKey("audit_log.id"),
            nullable=True,
        ),
        sa.Column("created_at", sa.DateTime(), nullable=False),
        sa.UniqueConstraint("sprint_id", "close_number"),
    )
    op.create_index(
        "ix_sprint_progress_snapshots_company_id",
        "sprint_progress_snapshots",
        ["company_id"],
    )
    op.create_index(
        "ix_sprint_progress_snapshots_sprint_id",
        "sprint_progress_snapshots",
        ["sprint_id"],
    )


def downgrade() -> None:
    op.drop_index(
        "ix_sprint_progress_snapshots_sprint_id",
        table_name="sprint_progress_snapshots",
    )
    op.drop_index(
        "ix_sprint_progress_snapshots_company_id",
        table_name="sprint_progress_snapshots",
    )
    op.drop_table("sprint_progress_snapshots")
