"""A person's statements for the sections of a documentation pack only a
person can write (issue #530, phase 2): the ``person_statements`` table,
company-scoped and append-only (the newest row per section is the current
statement; a row without text is a withdrawal).

New table; no existing row changes.

Follows 0046 (company invitations).

Revision ID: 0047
Revises: 0046
Create Date: 2026-11-17 18:00:00 UTC
"""

from __future__ import annotations

import sqlalchemy as sa
import sqlmodel

from alembic import op

revision = "0047"
down_revision = "0046"
branch_labels = None
depends_on = None


def upgrade() -> None:
    text = sqlmodel.sql.sqltypes.AutoString
    op.create_table(
        "person_statements",
        sa.Column("id", sa.Integer(), primary_key=True, nullable=False),
        sa.Column(
            "company_id", sa.Integer(), sa.ForeignKey("companies.id"), nullable=False
        ),
        sa.Column("section", text(), nullable=False),
        sa.Column("text", text(), nullable=True),
        sa.Column("author_role", text(), nullable=False),
        sa.Column("recorded_by", text(), nullable=False),
        sa.Column(
            "recorded_by_user_id",
            sa.Integer(),
            sa.ForeignKey("users.id"),
            nullable=True,
        ),
        sa.Column("supersedes_id", sa.Integer(), nullable=True),
        sa.Column("statement_sha256", text(), nullable=False),
        sa.Column("created_at", sa.DateTime(), nullable=False),
    )
    op.create_index(
        "ix_person_statements_company_id", "person_statements", ["company_id"]
    )


def downgrade() -> None:
    op.drop_index("ix_person_statements_company_id", table_name="person_statements")
    op.drop_table("person_statements")
