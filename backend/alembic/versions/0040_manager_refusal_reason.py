"""Why the Manager agent did not decide alone (review of PR #596, option 2).

``manager_decisions.refusal_reason`` is written by the platform when a row
settles ``not_allowed`` or ``stale``: the sentence of the policy or of the
rule that did not hold. It is never the model's answer, which stays in
``suggested_*``. Nullable and not backfilled: a row refused before this
migration has no recorded reason, and a row decided alone has none.

Follows 0039 (the files an architecture's write left).

Revision ID: 0040
Revises: 0039
Create Date: 2026-11-16 12:00:00 UTC
"""

from __future__ import annotations

import sqlalchemy as sa
import sqlmodel

from alembic import op

revision = "0040"
down_revision = "0039"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column(
        "manager_decisions",
        sa.Column("refusal_reason", sqlmodel.sql.sqltypes.AutoString(), nullable=True),
    )


def downgrade() -> None:
    with op.batch_alter_table("manager_decisions", schema=None) as batch:
        batch.drop_column("refusal_reason")
