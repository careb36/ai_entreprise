"""The intake queue (issue #532): requests that reach a company, with their
source, the Support agent's suggestion, a person's decision and the task or
specification version they became. A new table only: nothing existing changes.

Revision ID: 0099
Revises: 0028
Create Date: 2026-11-15 22:00:00 UTC
"""

from __future__ import annotations

import sqlalchemy as sa
import sqlmodel

from alembic import op

revision = "0099"
down_revision = "0028"
branch_labels = None
depends_on = None


def upgrade() -> None:
    text = sqlmodel.sql.sqltypes.AutoString
    op.create_table(
        "intake_items",
        sa.Column("id", sa.Integer(), primary_key=True, nullable=False),
        sa.Column(
            "company_id", sa.Integer(), sa.ForeignKey("companies.id"), nullable=False
        ),
        sa.Column("source", text(), nullable=False),
        sa.Column("source_ref", text(), nullable=True),
        sa.Column("submitted_by", text(), nullable=True),
        sa.Column("title", text(), nullable=False),
        sa.Column("body", text(), nullable=False),
        sa.Column("status", text(), nullable=False),
        sa.Column("snoozed_until", sa.DateTime(), nullable=True),
        sa.Column(
            "duplicate_of_item_id",
            sa.Integer(),
            sa.ForeignKey("intake_items.id"),
            nullable=True,
        ),
        sa.Column(
            "duplicate_of_task_id",
            sa.Integer(),
            sa.ForeignKey("backlog_items.id"),
            nullable=True,
        ),
        sa.Column(
            "outcome_task_id",
            sa.Integer(),
            sa.ForeignKey("backlog_items.id"),
            nullable=True,
        ),
        sa.Column(
            "outcome_specification_id",
            sa.Integer(),
            sa.ForeignKey("specifications.id"),
            nullable=True,
        ),
        sa.Column("suggested_status", text(), nullable=True),
        sa.Column("suggested_category", text(), nullable=True),
        sa.Column("suggested_duplicate_of", text(), nullable=True),
        sa.Column("suggested_reason", text(), nullable=True),
        sa.Column("triaged_by", text(), nullable=True),
        sa.Column("triaged_at", sa.DateTime(), nullable=True),
        sa.Column("decided_status", text(), nullable=True),
        sa.Column("decided_category", text(), nullable=True),
        sa.Column("decision_reason", text(), nullable=True),
        sa.Column("decided_by", text(), nullable=True),
        sa.Column("decided_at", sa.DateTime(), nullable=True),
        sa.Column("created_at", sa.DateTime(), nullable=False),
        sa.Column("updated_at", sa.DateTime(), nullable=False),
    )
    op.create_index("ix_intake_items_company_id", "intake_items", ["company_id"])
    op.create_index("ix_intake_items_status", "intake_items", ["status"])


def downgrade() -> None:
    op.drop_index("ix_intake_items_status", table_name="intake_items")
    op.drop_index("ix_intake_items_company_id", table_name="intake_items")
    op.drop_table("intake_items")
