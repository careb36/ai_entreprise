"""API tokens carry a role and an expiry (issue #327).

``role`` is the company role a token acts with (``Viewer`` or ``Manager``);
existing tokens keep acting as a Manager, as they did. ``expires_at`` is when
the token stops working; existing tokens have none and never expire.

Revision ID: 0016
Revises: 0015
Create Date: 2026-11-08 21:00:00 UTC
"""

from __future__ import annotations

import sqlalchemy as sa
import sqlmodel

from alembic import op

revision = "0016"
down_revision = "0015"
branch_labels = None
depends_on = None


def upgrade() -> None:
    with op.batch_alter_table("api_tokens", schema=None) as batch_op:
        batch_op.add_column(
            sa.Column(
                "role",
                sqlmodel.sql.sqltypes.AutoString(),
                nullable=False,
                server_default="Manager",
            )
        )
        batch_op.add_column(sa.Column("expires_at", sa.DateTime(), nullable=True))


def downgrade() -> None:
    with op.batch_alter_table("api_tokens", schema=None) as batch_op:
        batch_op.drop_column("expires_at")
        batch_op.drop_column("role")
