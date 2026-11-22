"""Audit anchors (issue #542; ``docs/archive/design/AUDIT_ANCHORS.md``):
``audit_anchors``, one row per head of a company's audit chain recorded
outside the factory (webhook, RFC 3161 token, customer-held key) with what
came back; and ``audit_anchor_settings``, the Owner's cadence, timestamping
authority and customer keys. New tables only: the chain's rows are not
changed.

Revision ID: 0032
Revises: 0031
Create Date: 2026-11-15 20:00:00 UTC
"""

from __future__ import annotations

import sqlalchemy as sa
import sqlmodel

from alembic import op

revision = "0032"
down_revision = "0031"
branch_labels = None
depends_on = None


def upgrade() -> None:
    text = sqlmodel.sql.sqltypes.AutoString
    op.create_table(
        "audit_anchors",
        sa.Column("id", sa.Integer(), primary_key=True, nullable=False),
        sa.Column(
            "company_id", sa.Integer(), sa.ForeignKey("companies.id"), nullable=False
        ),
        sa.Column("kind", text(), nullable=False),
        sa.Column("chain_seq", sa.Integer(), nullable=False),
        sa.Column("chain_hash", text(), nullable=False),
        sa.Column("anchored_at", sa.DateTime(), nullable=False),
        sa.Column("reason", text(), nullable=False),
        sa.Column("status", text(), nullable=False),
        sa.Column("external_ref", text(), nullable=True),
        sa.Column("key_fingerprint", text(), nullable=False, server_default=""),
        sa.Column("receipt", sa.JSON(), nullable=True),
        sa.Column("error", text(), nullable=True),
        sa.Column("requested_by", text(), nullable=True),
        sa.Column("created_at", sa.DateTime(), nullable=False),
        sa.Column("updated_at", sa.DateTime(), nullable=False),
    )
    op.create_index("ix_audit_anchors_company_id", "audit_anchors", ["company_id"])
    op.create_index(
        "ix_audit_anchors_company_seq", "audit_anchors", ["company_id", "chain_seq"]
    )
    op.create_index(
        "ux_audit_anchors_company_kind_seq",
        "audit_anchors",
        ["company_id", "kind", "chain_seq", "key_fingerprint"],
        unique=True,
    )
    op.create_table(
        "audit_anchor_settings",
        sa.Column("id", sa.Integer(), primary_key=True, nullable=False),
        sa.Column(
            "company_id", sa.Integer(), sa.ForeignKey("companies.id"), nullable=False
        ),
        sa.Column("enabled", sa.Boolean(), nullable=False, server_default=sa.false()),
        sa.Column("cadence_minutes", sa.Integer(), nullable=False, server_default="60"),
        sa.Column(
            "publish_webhook", sa.Boolean(), nullable=False, server_default=sa.false()
        ),
        sa.Column("tsa_url", text(), nullable=True),
        sa.Column("tsa_cert_pem", text(), nullable=True),
        sa.Column("customer_keys", sa.JSON(), nullable=True),
        sa.Column("updated_by", text(), nullable=True),
        sa.Column("updated_at", sa.DateTime(), nullable=False),
    )
    op.create_index(
        "ix_audit_anchor_settings_company_id",
        "audit_anchor_settings",
        ["company_id"],
        unique=True,
    )


def downgrade() -> None:
    op.drop_index(
        "ix_audit_anchor_settings_company_id", table_name="audit_anchor_settings"
    )
    op.drop_table("audit_anchor_settings")
    op.drop_index("ux_audit_anchors_company_kind_seq", table_name="audit_anchors")
    op.drop_index("ix_audit_anchors_company_seq", table_name="audit_anchors")
    op.drop_index("ix_audit_anchors_company_id", table_name="audit_anchors")
    op.drop_table("audit_anchors")
