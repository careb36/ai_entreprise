"""A person's recorded acknowledgement of a weaker guarantee (issue #766).

``company_acknowledgements``: an Owner's statement that the company runs
with tests off or with agent code on the host, with the reason, the account,
when it ends and, once withdrawn, by whom. No existing row changes.

Follows 0059 (company standards, #700).

Revision ID: 0060
Revises: 0059
Create Date: 2026-11-20 20:00:00 UTC
"""

from __future__ import annotations

import sqlalchemy as sa
import sqlmodel

from alembic import op

revision = "0060"
down_revision = "0059"
branch_labels = None
depends_on = None


def upgrade() -> None:
    text = sqlmodel.sql.sqltypes.AutoString
    op.create_table(
        "company_acknowledgements",
        sa.Column("id", sa.Integer(), primary_key=True, nullable=False),
        sa.Column(
            "company_id", sa.Integer(), sa.ForeignKey("companies.id"), nullable=False
        ),
        sa.Column("kind", text(), nullable=False),
        sa.Column("reason", text(), nullable=False),
        sa.Column("recorded_by", text(), nullable=False),
        sa.Column(
            "recorded_by_user_id",
            sa.Integer(),
            sa.ForeignKey("users.id"),
            nullable=True,
        ),
        sa.Column("recorded_at", sa.DateTime(), nullable=False),
        sa.Column("expires_at", sa.DateTime(), nullable=False),
        sa.Column("withdrawn_by", text(), nullable=True),
        sa.Column("withdrawn_at", sa.DateTime(), nullable=True),
    )
    op.create_index(
        "ix_company_acknowledgements_kind",
        "company_acknowledgements",
        ["company_id", "kind"],
    )


def downgrade() -> None:
    op.drop_index(
        "ix_company_acknowledgements_kind", table_name="company_acknowledgements"
    )
    op.drop_table("company_acknowledgements")
