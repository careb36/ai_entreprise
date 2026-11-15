"""The sprint board (issue #439; ``docs/design/SPRINT_BOARD.md``):
``sprint_board_notes``, the short typed notes a sprint's tasks leave each
other, company-scoped and kept with their sprint. A new table only: nothing
existing changes.

Revision ID: 0023
Revises: 0022
Create Date: 2026-11-15 18:00:00 UTC
"""

from __future__ import annotations

import sqlalchemy as sa
import sqlmodel

from alembic import op

revision = "0023"
down_revision = "0022"
branch_labels = None
depends_on = None


def upgrade() -> None:
    text = sqlmodel.sql.sqltypes.AutoString
    op.create_table(
        "sprint_board_notes",
        sa.Column("id", sa.Integer(), primary_key=True, nullable=False),
        sa.Column(
            "company_id", sa.Integer(), sa.ForeignKey("companies.id"), nullable=False
        ),
        sa.Column(
            "sprint_id", sa.Integer(), sa.ForeignKey("sprints.id"), nullable=False
        ),
        sa.Column("kind", text(), nullable=False),
        sa.Column("text", text(), nullable=False),
        sa.Column("author_role", text(), nullable=False),
        sa.Column("author_name", text(), nullable=True),
        sa.Column(
            "author_task_id",
            sa.Integer(),
            sa.ForeignKey("backlog_items.id"),
            nullable=True,
        ),
        sa.Column(
            "run_id", sa.Integer(), sa.ForeignKey("agent_runs.id"), nullable=True
        ),
        sa.Column("links", sa.JSON(), nullable=True),
        sa.Column(
            "supersedes_id",
            sa.Integer(),
            sa.ForeignKey("sprint_board_notes.id"),
            nullable=True,
        ),
        sa.Column("status", text(), nullable=False),
        sa.Column("status_by", text(), nullable=True),
        sa.Column("status_at", sa.DateTime(), nullable=True),
        sa.Column("status_reason", text(), nullable=True),
        sa.Column("created_at", sa.DateTime(), nullable=False),
    )
    op.create_index(
        "ix_sprint_board_notes_company_id", "sprint_board_notes", ["company_id"]
    )
    op.create_index(
        "ix_sprint_board_notes_sprint_id", "sprint_board_notes", ["sprint_id"]
    )
    op.create_index(
        "ix_sprint_board_notes_author_task_id",
        "sprint_board_notes",
        ["author_task_id"],
    )


def downgrade() -> None:
    op.drop_index(
        "ix_sprint_board_notes_author_task_id", table_name="sprint_board_notes"
    )
    op.drop_index("ix_sprint_board_notes_sprint_id", table_name="sprint_board_notes")
    op.drop_index("ix_sprint_board_notes_company_id", table_name="sprint_board_notes")
    op.drop_table("sprint_board_notes")
