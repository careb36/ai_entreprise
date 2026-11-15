"""Saved views of a company's tasks and each person's display preferences
(issue #535; ``docs/design/SAVED_VIEWS.md``), company-scoped. New tables
only: the filters are evaluated from the task projections, so nothing
existing changes.

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


def _company() -> sa.Column:
    return sa.Column(
        "company_id", sa.Integer(), sa.ForeignKey("companies.id"), nullable=False
    )


def upgrade() -> None:
    text = sqlmodel.sql.sqltypes.AutoString
    op.create_table(
        "saved_views",
        sa.Column("id", sa.Integer(), primary_key=True, nullable=False),
        _company(),
        sa.Column("name", text(), nullable=False),
        sa.Column("filters", sa.JSON(), nullable=True),
        sa.Column("group_by", text(), nullable=True),
        sa.Column("order_by", text(), nullable=True),
        sa.Column("layout", text(), nullable=False),
        sa.Column("visible_columns", sa.JSON(), nullable=True),
        sa.Column("access", text(), nullable=False),
        sa.Column("locked", sa.Boolean(), nullable=False),
        sa.Column(
            "owner_user_id", sa.Integer(), sa.ForeignKey("users.id"), nullable=True
        ),
        sa.Column("owner_name", text(), nullable=False),
        sa.Column("updated_by", text(), nullable=True),
        sa.Column("created_at", sa.DateTime(), nullable=False),
        sa.Column("updated_at", sa.DateTime(), nullable=False),
    )
    op.create_index("ix_saved_views_company_id", "saved_views", ["company_id"])
    op.create_index("ix_saved_views_owner_user_id", "saved_views", ["owner_user_id"])
    op.create_table(
        "saved_view_preferences",
        sa.Column("id", sa.Integer(), primary_key=True, nullable=False),
        _company(),
        sa.Column(
            "view_id", sa.Integer(), sa.ForeignKey("saved_views.id"), nullable=False
        ),
        sa.Column("user_id", sa.Integer(), sa.ForeignKey("users.id"), nullable=True),
        sa.Column("display", sa.JSON(), nullable=True),
        sa.Column("updated_at", sa.DateTime(), nullable=False),
        sa.UniqueConstraint("view_id", "user_id", name="uq_saved_view_preferences"),
    )
    op.create_index(
        "ix_saved_view_preferences_company_id",
        "saved_view_preferences",
        ["company_id"],
    )
    op.create_index(
        "ix_saved_view_preferences_view_id", "saved_view_preferences", ["view_id"]
    )


def downgrade() -> None:
    op.drop_index(
        "ix_saved_view_preferences_view_id", table_name="saved_view_preferences"
    )
    op.drop_index(
        "ix_saved_view_preferences_company_id", table_name="saved_view_preferences"
    )
    op.drop_table("saved_view_preferences")
    op.drop_index("ix_saved_views_owner_user_id", table_name="saved_views")
    op.drop_index("ix_saved_views_company_id", table_name="saved_views")
    op.drop_table("saved_views")
