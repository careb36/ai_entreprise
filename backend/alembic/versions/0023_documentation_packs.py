"""Documentation packs (issue #530, phase 1; ``docs/design/PROJECT_DOCUMENTATION.md``):
``documentation_packs``, one row per compiled version of a project's
management documentation (charter, scope, WBS, schedule, RACI, risk register,
lessons learned, C4 diagrams) with the sources each document cites, the digest
of the records it read, its approval and the sha256 the daemon read back for
each file written. A new table only: nothing existing changes.

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
        "documentation_packs",
        sa.Column("id", sa.Integer(), primary_key=True, nullable=False),
        sa.Column(
            "company_id", sa.Integer(), sa.ForeignKey("companies.id"), nullable=False
        ),
        sa.Column("version", sa.Integer(), nullable=False),
        sa.Column("status", text(), nullable=False),
        sa.Column(
            "specification_id",
            sa.Integer(),
            sa.ForeignKey("specifications.id"),
            nullable=True,
        ),
        sa.Column(
            "architecture_id",
            sa.Integer(),
            sa.ForeignKey("architectures.id"),
            nullable=True,
        ),
        sa.Column("compiled_by", text(), nullable=False),
        sa.Column("requested_by", text(), nullable=True),
        sa.Column("documents", sa.JSON(), nullable=True),
        sa.Column("diagrams", sa.JSON(), nullable=True),
        sa.Column("inputs_sha256", text(), nullable=False),
        sa.Column("approved_by", text(), nullable=True),
        sa.Column("approved_at", sa.DateTime(), nullable=True),
        sa.Column("written_files", sa.JSON(), nullable=True),
        sa.Column("written_at", sa.DateTime(), nullable=True),
        sa.Column("created_at", sa.DateTime(), nullable=False),
        sa.Column("updated_at", sa.DateTime(), nullable=False),
    )
    op.create_index(
        "ix_documentation_packs_company_id", "documentation_packs", ["company_id"]
    )


def downgrade() -> None:
    op.drop_index("ix_documentation_packs_company_id", table_name="documentation_packs")
    op.drop_table("documentation_packs")
