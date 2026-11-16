"""A person's acknowledgement of the protected paths an integration changes
(``docs/design/rsi-loop.md``, section 3.3 layer 3, PR 3 of section 8; REQ-13).

``integration_reviews.protected_paths_ack``: the protected paths a signed-in
Owner named in an approving review, as a JSON list; empty for every existing
review, so nothing changes for a company without the flag of 0041.

Follows 0041 (whether a company hosts the factory).

Revision ID: 0042
Revises: 0041
Create Date: 2026-11-16 21:00:00 UTC
"""

from __future__ import annotations

import sqlalchemy as sa

from alembic import op

revision = "0042"
down_revision = "0041"
branch_labels = None
depends_on = None


def upgrade() -> None:
    with op.batch_alter_table("integration_reviews", schema=None) as batch_op:
        batch_op.add_column(sa.Column("protected_paths_ack", sa.JSON(), nullable=True))


def downgrade() -> None:
    with op.batch_alter_table("integration_reviews", schema=None) as batch_op:
        batch_op.drop_column("protected_paths_ack")
