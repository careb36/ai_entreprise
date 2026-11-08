"""Add manager_policies and manager_decisions: the Manager agent (issue #331).

Revision ID: 0014
Revises: 0013
Create Date: 2026-11-08 20:00:00 UTC
"""

from __future__ import annotations

import sqlalchemy as sa
import sqlmodel

from alembic import op

revision = "0014"
down_revision = "0013"
branch_labels = None
depends_on = None


def upgrade() -> None:
    text = sqlmodel.sql.sqltypes.AutoString
    op.create_table(
        "manager_policies",
        sa.Column("id", sa.Integer(), primary_key=True, nullable=False),
        sa.Column(
            "company_id", sa.Integer(), sa.ForeignKey("companies.id"), nullable=False
        ),
        sa.Column("rules", sa.JSON(), nullable=True),
        sa.Column("max_files", sa.Integer(), nullable=False),
        sa.Column("max_criteria", sa.Integer(), nullable=False),
        sa.Column("updated_by", text(), nullable=True),
        sa.Column("updated_at", sa.DateTime(), nullable=False),
    )
    op.create_index(
        "ix_manager_policies_company_id",
        "manager_policies",
        ["company_id"],
        unique=True,
    )
    op.create_table(
        "manager_decisions",
        sa.Column("id", sa.Integer(), primary_key=True, nullable=False),
        sa.Column(
            "company_id", sa.Integer(), sa.ForeignKey("companies.id"), nullable=False
        ),
        sa.Column("item_kind", text(), nullable=False),
        sa.Column("item_id", sa.Integer(), nullable=False),
        sa.Column("item_state", text(), nullable=False),
        sa.Column("category", text(), nullable=False),
        sa.Column("evidence", sa.JSON(), nullable=True),
        sa.Column("suggested_decision", text(), nullable=True),
        sa.Column("suggested_reason", text(), nullable=True),
        sa.Column("suggested_by", text(), nullable=False),
        sa.Column("suggested_at", sa.DateTime(), nullable=False),
        sa.Column("action_state", text(), nullable=True),
        sa.Column("decided_by", text(), nullable=True),
        sa.Column("rule", text(), nullable=True),
        sa.Column("outcome", text(), nullable=True),
        sa.Column("decided_at", sa.DateTime(), nullable=True),
        sa.Column(
            "audit_log_id", sa.Integer(), sa.ForeignKey("audit_log.id"), nullable=True
        ),
        sa.Column("overridden_by", text(), nullable=True),
        sa.Column("overridden_at", sa.DateTime(), nullable=True),
        sa.Column(
            "override_audit_log_id",
            sa.Integer(),
            sa.ForeignKey("audit_log.id"),
            nullable=True,
        ),
        sa.UniqueConstraint("company_id", "item_kind", "item_id", "item_state"),
    )
    op.create_index(
        "ix_manager_decisions_company_id", "manager_decisions", ["company_id"]
    )


def downgrade() -> None:
    op.drop_index("ix_manager_decisions_company_id", table_name="manager_decisions")
    op.drop_table("manager_decisions")
    op.drop_index("ix_manager_policies_company_id", table_name="manager_policies")
    op.drop_table("manager_policies")
