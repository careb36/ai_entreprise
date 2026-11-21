"""A company's limits on its agents' work (issue #766).

``companies.limits``: ``null`` (the deployment's values, every company on
upgrade) or an object of the Developer's tool rounds, tool-result size and
answer cap and the task's rework budget, each within the ceilings of
``services/contracts/company_limits.py``. No existing row changes.

Follows 0060 (company acknowledgements).

Revision ID: 0061
Revises: 0060
Create Date: 2026-11-21 20:00:00 UTC
"""

from __future__ import annotations

import sqlalchemy as sa

from alembic import op

revision = "0061"
down_revision = "0060"
branch_labels = None
depends_on = None


def upgrade() -> None:
    with op.batch_alter_table("companies", schema=None) as batch_op:
        batch_op.add_column(sa.Column("limits", sa.JSON(none_as_null=True), nullable=True))


def downgrade() -> None:
    with op.batch_alter_table("companies", schema=None) as batch_op:
        batch_op.drop_column("limits")
