"""A company's own standards, imported from its repository, and the
time-boxed exceptions to a standard (issue #700;
``docs/design/COMPANY_STANDARDS.md`` §11.2, §12 decisions 1 and 8).

- ``company_standard_sets``: one row per imported version (``draft``,
  ``active``, ``superseded``, ``reverted``, ``rejected``), with the bytes the
  daemon read at the default branch's head, the sha256 the backend computed,
  the parsed standards, the guards' hashes, the baselines and the dry run.
- ``company_standard_exceptions``: an exception to one standard, requested
  by an Owner and approved by a second Owner, at most 90 days.

No existing row changes. A company with no row is held to the catalogue
alone, as before.

Follows 0056 (the sprint's objective).

Revision ID: 0057
Revises: 0056
Create Date: 2026-11-20 16:00:00 UTC
"""

from __future__ import annotations

import sqlalchemy as sa
import sqlmodel

from alembic import op

revision = "0057"
down_revision = "0056"
branch_labels = None
depends_on = None


def _company():
    return sa.Column(
        "company_id", sa.Integer(), sa.ForeignKey("companies.id"), nullable=False
    )


def _user(name: str, nullable: bool = True):
    return sa.Column(name, sa.Integer(), sa.ForeignKey("users.id"), nullable=nullable)


def upgrade() -> None:
    text = sqlmodel.sql.sqltypes.AutoString
    op.create_table(
        "company_standard_sets",
        sa.Column("id", sa.Integer(), primary_key=True, nullable=False),
        _company(),
        sa.Column("version", sa.Integer(), nullable=False),
        sa.Column("status", text(), nullable=False),
        sa.Column("source_path", text(), nullable=False),
        sa.Column("source_commit", text(), nullable=False),
        sa.Column("source_base", text(), nullable=True),
        sa.Column("sha256", text(), nullable=False),
        sa.Column("raw", sa.LargeBinary(), nullable=False),
        sa.Column("parsed", sa.JSON(), nullable=True),
        sa.Column("guard_hashes", sa.JSON(), nullable=True),
        sa.Column("baselines", sa.JSON(), nullable=True),
        sa.Column("import_refusals", sa.JSON(), nullable=True),
        sa.Column("dry_run", sa.JSON(), nullable=True),
        sa.Column("sandbox_seen", text(), nullable=True),
        sa.Column("proposed_by", text(), nullable=True),
        sa.Column("imported_by", text(), nullable=True),
        _user("imported_by_user_id"),
        sa.Column("imported_at", sa.DateTime(), nullable=False),
        sa.Column("activated_by", text(), nullable=True),
        _user("activated_by_user_id"),
        sa.Column("activated_at", sa.DateTime(), nullable=True),
        sa.Column("single_owner", sa.Boolean(), nullable=False),
        sa.Column("single_owner_reason", text(), nullable=True),
        sa.Column("reviewed_in", sa.Integer(), nullable=True),
        sa.Column("supersedes_id", sa.Integer(), nullable=True),
        sa.Column("ended_by", text(), nullable=True),
        sa.Column("ended_at", sa.DateTime(), nullable=True),
        sa.Column("baseline", sa.JSON(), nullable=True),
        sa.Column("measurement", sa.JSON(), nullable=True),
        sa.Column("result", text(), nullable=True),
    )
    op.create_index(
        op.f("ix_company_standard_sets_company_id"),
        "company_standard_sets",
        ["company_id"],
        unique=False,
    )
    op.create_table(
        "company_standard_exceptions",
        sa.Column("id", sa.Integer(), primary_key=True, nullable=False),
        _company(),
        sa.Column("standard_id", text(), nullable=False),
        sa.Column("reason", sa.Text(), nullable=False),
        sa.Column("compensating_control", sa.Text(), nullable=False),
        sa.Column("days", sa.Integer(), nullable=False),
        sa.Column("state", text(), nullable=False),
        _user("requested_by", nullable=False),
        sa.Column("requested_at", sa.DateTime(), nullable=False),
        _user("decided_by"),
        sa.Column("decided_at", sa.DateTime(), nullable=True),
        sa.Column("decision_reason", text(), nullable=True),
        sa.Column("expires_at", sa.DateTime(), nullable=True),
        sa.Column("reviewed_in", sa.Integer(), nullable=True),
        _user("revoked_by"),
        sa.Column("revoked_at", sa.DateTime(), nullable=True),
    )
    op.create_index(
        op.f("ix_company_standard_exceptions_company_id"),
        "company_standard_exceptions",
        ["company_id"],
        unique=False,
    )


def downgrade() -> None:
    op.drop_index(
        op.f("ix_company_standard_exceptions_company_id"),
        table_name="company_standard_exceptions",
    )
    op.drop_table("company_standard_exceptions")
    op.drop_index(
        op.f("ix_company_standard_sets_company_id"),
        table_name="company_standard_sets",
    )
    op.drop_table("company_standard_sets")
