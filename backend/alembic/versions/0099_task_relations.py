"""Typed relations between tasks across sprints (issue #533): ``task_relations``,
one row per pair of tasks of one company (``blocked_by``, ``duplicate``,
``relates_to``), proposed or confirmed. A new table only: nothing existing
changes.

Revision ID: 0099
Revises: 0027
Create Date: 2026-11-15 20:00:00 UTC
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
        "task_relations",
        sa.Column("id", sa.Integer(), primary_key=True, nullable=False),
        sa.Column(
            "company_id", sa.Integer(), sa.ForeignKey("companies.id"), nullable=False
        ),
        sa.Column(
            "task_id", sa.Integer(), sa.ForeignKey("backlog_items.id"), nullable=False
        ),
        sa.Column(
            "related_task_id",
            sa.Integer(),
            sa.ForeignKey("backlog_items.id"),
            nullable=False,
        ),
        sa.Column("pair_low", sa.Integer(), nullable=False),
        sa.Column("pair_high", sa.Integer(), nullable=False),
        sa.Column("kind", text(), nullable=False),
        sa.Column("state", text(), nullable=False),
        sa.Column("proposed_by", text(), nullable=False),
        sa.Column("proposed_role", text(), nullable=False),
        sa.Column("confirmed_by", text(), nullable=True),
        sa.Column("confirmed_at", sa.DateTime(), nullable=True),
        sa.Column("added_dependency", sa.Boolean(), nullable=False),
        sa.Column("created_at", sa.DateTime(), nullable=False),
        sa.UniqueConstraint("pair_low", "pair_high"),
    )
    op.create_index("ix_task_relations_company_id", "task_relations", ["company_id"])
    op.create_index("ix_task_relations_task_id", "task_relations", ["task_id"])
    op.create_index(
        "ix_task_relations_related_task_id", "task_relations", ["related_task_id"]
    )


def downgrade() -> None:
    op.drop_index("ix_task_relations_related_task_id", table_name="task_relations")
    op.drop_index("ix_task_relations_task_id", table_name="task_relations")
    op.drop_index("ix_task_relations_company_id", table_name="task_relations")
    op.drop_table("task_relations")
