"""Keep the model ids a provider connection listed at its last validation.

Revision ID: 0007
Revises: 0006
Create Date: 2026-11-08 00:30:00 UTC
"""

from __future__ import annotations

import sqlalchemy as sa

from alembic import op

revision = "0007"
down_revision = "0006"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column(
        "provider_connections", sa.Column("listed_models", sa.JSON(), nullable=True)
    )


def downgrade() -> None:
    op.drop_column("provider_connections", "listed_models")
