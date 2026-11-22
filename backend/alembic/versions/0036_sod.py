"""Segregation of duties and the periodic access review (issue #543;
``docs/archive/design/SEGREGATION_OF_DUTIES.md``): ``sod_policies`` (one per company:
which approvals the rule holds, the single-person exception, the access
review's interval and inactivity threshold) and ``access_reviews`` (one
completed review: what was listed and the Owner's decision on each item).

No existing row changes: a company without a policy row gets the defaults
(the rule on for high-risk work, a review every 90 days).

Follows 0029 (teams and the usage report, #512); renumbered at merge.

Revision ID: 0036
Revises: 0035
Create Date: 2026-11-15 22:00:00 UTC
"""

from __future__ import annotations

import sqlalchemy as sa
import sqlmodel

from alembic import op

revision = "0036"
down_revision = "0035"
branch_labels = None
depends_on = None


def upgrade() -> None:
    text = sqlmodel.sql.sqltypes.AutoString
    op.create_table(
        "sod_policies",
        sa.Column("id", sa.Integer(), primary_key=True, nullable=False),
        sa.Column(
            "company_id", sa.Integer(), sa.ForeignKey("companies.id"), nullable=False
        ),
        sa.Column("mode", text(), nullable=False),
        sa.Column("solo_exception", sa.Boolean(), nullable=False),
        sa.Column("solo_reason", text(), nullable=True),
        sa.Column("solo_granted_by", text(), nullable=True),
        sa.Column("solo_granted_at", sa.DateTime(), nullable=True),
        sa.Column("review_interval_days", sa.Integer(), nullable=False),
        sa.Column("inactive_days", sa.Integer(), nullable=False),
        sa.Column("updated_by", text(), nullable=True),
        sa.Column("created_at", sa.DateTime(), nullable=False),
        sa.Column("updated_at", sa.DateTime(), nullable=False),
    )
    op.create_index(
        "ix_sod_policies_company_id", "sod_policies", ["company_id"], unique=True
    )

    op.create_table(
        "access_reviews",
        sa.Column("id", sa.Integer(), primary_key=True, nullable=False),
        sa.Column(
            "company_id", sa.Integer(), sa.ForeignKey("companies.id"), nullable=False
        ),
        sa.Column("due_at", sa.DateTime(), nullable=True),
        sa.Column("completed_at", sa.DateTime(), nullable=False),
        sa.Column("completed_by", text(), nullable=False),
        sa.Column(
            "completed_by_user_id",
            sa.Integer(),
            sa.ForeignKey("users.id"),
            nullable=True,
        ),
        sa.Column("on_time", sa.Boolean(), nullable=False),
        sa.Column("items", sa.JSON(), nullable=True),
        sa.Column("summary", sa.JSON(), nullable=True),
        sa.Column("note", text(), nullable=True),
        sa.Column(
            "audit_log_id", sa.Integer(), sa.ForeignKey("audit_log.id"), nullable=True
        ),
    )
    op.create_index("ix_access_reviews_company_id", "access_reviews", ["company_id"])


def downgrade() -> None:
    op.drop_index("ix_access_reviews_company_id", table_name="access_reviews")
    op.drop_table("access_reviews")
    op.drop_index("ix_sod_policies_company_id", table_name="sod_policies")
    op.drop_table("sod_policies")
