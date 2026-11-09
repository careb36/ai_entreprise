"""Console producers' environment: ``companies.console_env``, the variable
names only a console producer's run sees (its provider's key), beside the
``sandbox_env`` every sandboxed run of agent code sees
(``services/console_clis.py``, ``run_sandbox``). Empty for every company.

Revision ID: 0021
Revises: 0020
Create Date: 2026-11-09 22:00:00 UTC
"""

from __future__ import annotations

import sqlalchemy as sa

from alembic import op

revision = "0021"
down_revision = "0020"
branch_labels = None
depends_on = None


def upgrade() -> None:
    with op.batch_alter_table("companies", schema=None) as batch_op:
        batch_op.add_column(sa.Column("console_env", sa.JSON(), nullable=True))


def downgrade() -> None:
    with op.batch_alter_table("companies", schema=None) as batch_op:
        batch_op.drop_column("console_env")
