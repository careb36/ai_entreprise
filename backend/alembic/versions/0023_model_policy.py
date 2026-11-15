"""The routing policy by task difficulty: ``companies.model_policy`` (issue #319),
difficulty bands to a model or a proxy tier (``services/models/routing_policy.py``).
Empty for every company: the deployment's ``AGENT_MODEL_POLICY`` applies, else
the role defaults, as before.

Revision ID: 0023
Revises: 0022
Create Date: 2026-11-15 18:00:00 UTC
"""

from __future__ import annotations

import sqlalchemy as sa

from alembic import op

revision = "0023"
down_revision = "0022"
branch_labels = None
depends_on = None


def upgrade() -> None:
    with op.batch_alter_table("companies", schema=None) as batch_op:
        batch_op.add_column(sa.Column("model_policy", sa.JSON(), nullable=True))


def downgrade() -> None:
    with op.batch_alter_table("companies", schema=None) as batch_op:
        batch_op.drop_column("model_policy")
