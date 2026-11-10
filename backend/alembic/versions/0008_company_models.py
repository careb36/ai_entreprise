"""The company's models: a default (``default_model``) and one per role
(``role_models``, a JSON object keyed by role), used when an agent's own model
is empty (``services/models/company_models.py``). Both nullable: an existing company
keeps the deployment's ``AGENT_MODEL`` settings, as before.

Revision ID: 0008
Revises: 0007
Create Date: 2026-11-08 03:20:00+00:00

"""

from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

# revision identifiers, used by Alembic.
revision: str = "0008"
down_revision: str | Sequence[str] | None = "0007"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    """Upgrade schema."""
    with op.batch_alter_table("companies", schema=None) as batch_op:
        batch_op.add_column(sa.Column("default_model", sa.String(), nullable=True))
        batch_op.add_column(sa.Column("role_models", sa.JSON(), nullable=True))


def downgrade() -> None:
    """Downgrade schema."""
    with op.batch_alter_table("companies", schema=None) as batch_op:
        batch_op.drop_column("role_models")
        batch_op.drop_column("default_model")
