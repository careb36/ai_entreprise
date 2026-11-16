"""Whether a company hosts the factory, so its protected paths hold
(``docs/design/rsi-loop.md``, section 3.2, PR 2 of section 8).

``companies.protected_paths_enabled``, false for every existing company, so
nothing changes until an Owner turns it on (audited,
``services/platform/protected_paths_setting.py``).

Follows 0040 (why the Manager agent did not decide alone).

Revision ID: 0041
Revises: 0040
Create Date: 2026-11-16 18:00:00 UTC
"""

from __future__ import annotations

import sqlalchemy as sa

from alembic import op

revision = "0041"
down_revision = "0040"
branch_labels = None
depends_on = None


def upgrade() -> None:
    with op.batch_alter_table("companies", schema=None) as batch_op:
        batch_op.add_column(
            sa.Column(
                "protected_paths_enabled",
                sa.Boolean(),
                nullable=False,
                server_default=sa.false(),
            )
        )


def downgrade() -> None:
    with op.batch_alter_table("companies", schema=None) as batch_op:
        batch_op.drop_column("protected_paths_enabled")
