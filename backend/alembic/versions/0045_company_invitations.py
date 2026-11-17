"""Pending invitations to a company: ``company_invitations``, one row per
invitation of an address to a company with a role, its inviter, its expiry,
its status and the sha256 of its single-use token (never the token). Inviting
adds nobody; the invitee's acceptance creates the membership
(``services/platform/invitations.py``).

New table; no existing row changes.

Follows 0044 (one account per email address).

Revision ID: 0045
Revises: 0044
Create Date: 2026-11-17 13:05:00 UTC
"""

from __future__ import annotations

import sqlalchemy as sa
import sqlmodel

from alembic import op

revision = "0045"
down_revision = "0044"
branch_labels = None
depends_on = None


def upgrade() -> None:
    text = sqlmodel.sql.sqltypes.AutoString
    op.create_table(
        "company_invitations",
        sa.Column("id", sa.Integer(), primary_key=True, nullable=False),
        sa.Column(
            "company_id", sa.Integer(), sa.ForeignKey("companies.id"), nullable=False
        ),
        sa.Column("email", text(), nullable=False),
        sa.Column("human_role", text(), nullable=False),
        sa.Column(
            "invited_by_user_id",
            sa.Integer(),
            sa.ForeignKey("users.id"),
            nullable=True,
        ),
        sa.Column("invited_by", text(), nullable=True),
        sa.Column("token_sha256", text(), nullable=False),
        sa.Column("status", text(), nullable=False),
        sa.Column("created_at", sa.DateTime(), nullable=False),
        sa.Column("expires_at", sa.DateTime(), nullable=False),
        sa.Column("responded_at", sa.DateTime(), nullable=True),
        sa.Column(
            "responded_by_user_id",
            sa.Integer(),
            sa.ForeignKey("users.id"),
            nullable=True,
        ),
    )
    op.create_index(
        "ix_company_invitations_company_id",
        "company_invitations",
        ["company_id"],
        unique=False,
    )
    op.create_index(
        "ix_company_invitations_email", "company_invitations", ["email"], unique=False
    )
    op.create_index(
        "ix_company_invitations_token_sha256",
        "company_invitations",
        ["token_sha256"],
        unique=True,
    )


def downgrade() -> None:
    op.drop_index("ix_company_invitations_token_sha256", "company_invitations")
    op.drop_index("ix_company_invitations_email", "company_invitations")
    op.drop_index("ix_company_invitations_company_id", "company_invitations")
    op.drop_table("company_invitations")
