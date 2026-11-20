"""The objective a person wrote for a sprint, whole: ``sprints.objective``.

``sprints.goal`` holds the sprint's goal, which the planner's model may
write (a claim, one line); the person's objective is input and never shares
that field. ``objective`` is TEXT, so a long, structured objective is stored
as written, never cut. NULL for every sprint before this migration and for
one started without a prompt (from a specification, or by an API client
that sends none), which is what they had.

Follows 0055 (run window, #736).

Revision ID: 0056
Revises: 0055
Create Date: 2026-11-20 10:00:00 UTC
"""

from __future__ import annotations

import sqlalchemy as sa

from alembic import op

revision = "0056"
down_revision = "0055"
branch_labels = None
depends_on = None


def upgrade() -> None:
    with op.batch_alter_table("sprints", schema=None) as batch_op:
        batch_op.add_column(sa.Column("objective", sa.Text(), nullable=True))


def downgrade() -> None:
    with op.batch_alter_table("sprints", schema=None) as batch_op:
        batch_op.drop_column("objective")
