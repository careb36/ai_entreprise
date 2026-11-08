"""The company's sandbox: where its agent-written code runs (``delivery_sandbox``:
host, container or bwrap; empty means the deployment's ``DELIVERY_SANDBOX``)
and the environment variables a sandboxed run may see, by name
(``sandbox_env``). Both nullable: an existing company keeps the deployment's
default, which is ``host``, as before.

Revision ID: 0002
Revises: 0001
Create Date: 2026-11-07 20:35:48.012666+00:00

"""

from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

# revision identifiers, used by Alembic.
revision: str = "0002"
down_revision: str | Sequence[str] | None = "0001"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    """Upgrade schema."""
    with op.batch_alter_table("companies", schema=None) as batch_op:
        batch_op.add_column(sa.Column("delivery_sandbox", sa.String(), nullable=True))
        batch_op.add_column(sa.Column("sandbox_env", sa.JSON(), nullable=True))


def downgrade() -> None:
    """Downgrade schema."""
    with op.batch_alter_table("companies", schema=None) as batch_op:
        batch_op.drop_column("sandbox_env")
        batch_op.drop_column("delivery_sandbox")
