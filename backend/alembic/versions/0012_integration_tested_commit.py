"""sprint_integrations: the commit tested and each task's evidence at integration (issue #314).

Revision ID: 0012
Revises: 0011
Create Date: 2026-11-08 20:00:00 UTC
"""

from __future__ import annotations

import sqlalchemy as sa
import sqlmodel

from alembic import op

revision = "0012"
down_revision = "0011"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column(
        "sprint_integrations",
        sa.Column("tested_commit", sqlmodel.sql.sqltypes.AutoString(), nullable=True),
    )
    op.add_column("sprint_integrations", sa.Column("tasks", sa.JSON(), nullable=True))


def downgrade() -> None:
    op.drop_column("sprint_integrations", "tasks")
    op.drop_column("sprint_integrations", "tested_commit")
