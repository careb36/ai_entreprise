"""Console producers: ``companies.console_producers``, the terminal coding agent
that produces a role's tasks instead of the LLM call (``{"Developer":
"claude"}``, ``services/console_producer.py``). Empty for every company: each
role stays produced by its model until a Manager names a CLI.

Revision ID: 0020
Revises: 0019
Create Date: 2026-11-09 18:00:00 UTC
"""

from __future__ import annotations

import sqlalchemy as sa

from alembic import op

revision = "0020"
down_revision = "0019"
branch_labels = None
depends_on = None


def upgrade() -> None:
    with op.batch_alter_table("companies", schema=None) as batch_op:
        batch_op.add_column(sa.Column("console_producers", sa.JSON(), nullable=True))


def downgrade() -> None:
    with op.batch_alter_table("companies", schema=None) as batch_op:
        batch_op.drop_column("console_producers")
