"""Single sign-on over OpenID Connect (issue #512, phase 3; ``docs/archive/design/SSO.md``):
``sso_connections`` (one identity provider per company, the client secret
encrypted), ``sso_identities`` (an account's ``(issuer, subject)``) and
``sso_flow_states`` (the one-time login state and handoff codes, hashed).
New tables only: nothing existing changes, and password sign-in is untouched
until an Owner requires SSO.

Revision ID: 0022
Revises: 0021
Create Date: 2026-11-15 12:00:00 UTC
"""

from __future__ import annotations

import sqlalchemy as sa
import sqlmodel

from alembic import op

revision = "0022"
down_revision = "0021"
branch_labels = None
depends_on = None


def upgrade() -> None:
    text = sqlmodel.sql.sqltypes.AutoString
    op.create_table(
        "sso_connections",
        sa.Column("id", sa.Integer(), primary_key=True, nullable=False),
        sa.Column(
            "company_id", sa.Integer(), sa.ForeignKey("companies.id"), nullable=False
        ),
        sa.Column("slug", text(), nullable=False),
        sa.Column("preset", text(), nullable=False),
        sa.Column("tenant", text(), nullable=True),
        sa.Column("issuer", text(), nullable=False),
        sa.Column("client_id", text(), nullable=False),
        sa.Column("client_secret", text(), nullable=True),
        sa.Column("allowed_domains", sa.JSON(), nullable=True),
        sa.Column("default_role", text(), nullable=False),
        sa.Column("auto_join", sa.Boolean(), nullable=False),
        sa.Column("enabled", sa.Boolean(), nullable=False),
        sa.Column("require_sso", sa.Boolean(), nullable=False),
        sa.Column("tested_at", sa.DateTime(), nullable=True),
        sa.Column("test_ok", sa.Boolean(), nullable=True),
        sa.Column("test_detail", text(), nullable=True),
        sa.Column("created_by", text(), nullable=True),
        sa.Column("created_at", sa.DateTime(), nullable=True),
        sa.Column("updated_at", sa.DateTime(), nullable=True),
    )
    op.create_index(
        "ix_sso_connections_company_id", "sso_connections", ["company_id"], unique=True
    )
    op.create_index("ix_sso_connections_slug", "sso_connections", ["slug"], unique=True)

    op.create_table(
        "sso_identities",
        sa.Column("id", sa.Integer(), primary_key=True, nullable=False),
        sa.Column("user_id", sa.Integer(), sa.ForeignKey("users.id"), nullable=False),
        sa.Column("connection_id", sa.Integer(), nullable=True),
        sa.Column("issuer", text(), nullable=False),
        sa.Column("subject", text(), nullable=False),
        sa.Column("email", text(), nullable=True),
        sa.Column("created_at", sa.DateTime(), nullable=True),
        sa.Column("last_login_at", sa.DateTime(), nullable=True),
        sa.UniqueConstraint("issuer", "subject"),
    )
    op.create_index("ix_sso_identities_user_id", "sso_identities", ["user_id"])
    op.create_index(
        "ix_sso_identities_connection_id", "sso_identities", ["connection_id"]
    )

    op.create_table(
        "sso_flow_states",
        sa.Column("id", sa.Integer(), primary_key=True, nullable=False),
        sa.Column("kind", text(), nullable=False),
        sa.Column("key_hash", text(), nullable=False),
        sa.Column("connection_id", sa.Integer(), nullable=True),
        sa.Column("user_id", sa.Integer(), nullable=True),
        sa.Column("nonce", text(), nullable=True),
        sa.Column("code_verifier", text(), nullable=True),
        sa.Column("binding_hash", text(), nullable=True),
        sa.Column("expires_at", sa.DateTime(), nullable=False),
        sa.Column("created_at", sa.DateTime(), nullable=True),
    )
    op.create_index(
        "ix_sso_flow_states_key_hash", "sso_flow_states", ["key_hash"], unique=True
    )


def downgrade() -> None:
    op.drop_index("ix_sso_flow_states_key_hash", table_name="sso_flow_states")
    op.drop_table("sso_flow_states")
    op.drop_index("ix_sso_identities_connection_id", table_name="sso_identities")
    op.drop_index("ix_sso_identities_user_id", table_name="sso_identities")
    op.drop_table("sso_identities")
    op.drop_index("ix_sso_connections_slug", table_name="sso_connections")
    op.drop_index("ix_sso_connections_company_id", table_name="sso_connections")
    op.drop_table("sso_connections")
