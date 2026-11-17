"""The findings of the self-improvement loop (``docs/design/rsi-loop.md``,
PR 9 of section 8; REQ-18, REQ-20): ``findings``, one row per retrospective
finding a company works on, with its specification's sha256 (computed by the
platform), its target measure and the outcomes of its cycles, which only the
platform writes.

New table; no existing row changes.

Follows 0042 (a person's acknowledgement of the protected paths).

Revision ID: 0043
Revises: 0042
Create Date: 2026-11-17 09:00:00 UTC
"""

from __future__ import annotations

import sqlalchemy as sa
import sqlmodel

from alembic import op

revision = "0043"
down_revision = "0042"
branch_labels = None
depends_on = None


def upgrade() -> None:
    text = sqlmodel.sql.sqltypes.AutoString
    op.create_table(
        "findings",
        sa.Column("id", sa.Integer(), primary_key=True, nullable=False),
        sa.Column(
            "company_id", sa.Integer(), sa.ForeignKey("companies.id"), nullable=False
        ),
        sa.Column("source", text(), nullable=True),
        sa.Column("source_ref", text(), nullable=True),
        sa.Column("title", text(), nullable=False),
        sa.Column("specification_sha256", text(), nullable=True),
        sa.Column("specification_path", text(), nullable=True),
        sa.Column("specification_text", text(), nullable=True),
        sa.Column("target_measure", text(), nullable=True),
        sa.Column("status", text(), nullable=False),
        sa.Column("outcomes", sa.JSON(none_as_null=True), nullable=True),
        sa.Column("closed_by", text(), nullable=True),
        sa.Column("created_at", sa.DateTime(), nullable=False),
        sa.Column("updated_at", sa.DateTime(), nullable=True),
        # One finding per specification in a company (REQ-20).
        sa.UniqueConstraint(
            "company_id",
            "specification_sha256",
            name="uq_findings_company_specification",
        ),
    )
    op.create_index("ix_findings_company_id", "findings", ["company_id"], unique=False)
    op.create_index(
        "ix_findings_specification_sha256",
        "findings",
        ["specification_sha256"],
        unique=False,
    )


def downgrade() -> None:
    op.drop_index("ix_findings_specification_sha256", table_name="findings")
    op.drop_index("ix_findings_company_id", table_name="findings")
    op.drop_table("findings")
