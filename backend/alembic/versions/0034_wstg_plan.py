"""A person's recorded review of an OWASP WSTG test (issue #539): the
``wstg_reviews`` table, company-scoped. A new table only: nothing existing
changes.

Revision ID: 0034
Revises: 0033
Create Date: 2026-11-15 22:00:00 UTC
"""

from __future__ import annotations

import sqlalchemy as sa
import sqlmodel

from alembic import op

revision = "0034"
down_revision = "0033"
branch_labels = None
depends_on = None


def upgrade() -> None:
    text = sqlmodel.sql.sqltypes.AutoString
    op.create_table(
        "wstg_reviews",
        sa.Column("id", sa.Integer(), primary_key=True, nullable=False),
        sa.Column(
            "company_id", sa.Integer(), sa.ForeignKey("companies.id"), nullable=False
        ),
        sa.Column("wstg_id", text(), nullable=False),
        sa.Column("result", text(), nullable=False),
        sa.Column("reviewer_name", text(), nullable=False),
        sa.Column("reviewer_role", text(), nullable=False),
        sa.Column("reviewed_on", text(), nullable=False),
        sa.Column("artifact", text(), nullable=False),
        sa.Column("note", text(), nullable=True),
        sa.Column("recorded_by", text(), nullable=True),
        sa.Column("record_sha256", text(), nullable=False),
        sa.Column("created_at", sa.DateTime(), nullable=False),
    )
    op.create_index("ix_wstg_reviews_company_id", "wstg_reviews", ["company_id"])


def downgrade() -> None:
    op.drop_index("ix_wstg_reviews_company_id", table_name="wstg_reviews")
    op.drop_table("wstg_reviews")
