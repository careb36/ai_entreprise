"""The Manager agent's further rules (issue #394): ``max_files_high_risk``,
the tighter file limit ``accept_high_risk`` reads (3 by default), and
``publish_enabled``, the client's switch for publishing branches (off). An
existing policy keeps every rule it named and gains neither.

Revision ID: 0016
Revises: 0015
Create Date: 2026-11-09 10:00:00 UTC
"""

from __future__ import annotations

import sqlalchemy as sa

from alembic import op

revision = "0016"
down_revision = "0015"
branch_labels = None
depends_on = None


def upgrade() -> None:
    with op.batch_alter_table("manager_policies", schema=None) as batch_op:
        batch_op.add_column(
            sa.Column(
                "max_files_high_risk",
                sa.Integer(),
                nullable=False,
                server_default="3",
            )
        )
        batch_op.add_column(
            sa.Column(
                "publish_enabled",
                sa.Boolean(),
                nullable=False,
                server_default=sa.false(),
            )
        )


def downgrade() -> None:
    with op.batch_alter_table("manager_policies", schema=None) as batch_op:
        batch_op.drop_column("publish_enabled")
        batch_op.drop_column("max_files_high_risk")
