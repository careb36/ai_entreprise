"""Run window per company (issue #736): ``companies.run_window``,
``sprints.urgent`` and ``sprints.run_window_wait_until``.

``companies.run_window`` holds when work that can wait may start (a preset
such as the provider's off-peak hours, or intervals in a named IANA zone);
NULL is off, which is what every company had until now, so nothing changes
on upgrade. ``sprints.urgent`` is a Manager's bypass of the window (false for
every sprint). ``sprints.run_window_wait_until`` is set while the window holds
some of a sprint's work back: when the backend computed it opens.

Follows 0054 (SSO group mapping, #703 / PR #738).

Revision ID: 0055
Revises: 0054
Create Date: 2026-11-19 22:00:00 UTC
"""

from __future__ import annotations

import sqlalchemy as sa

from alembic import op

revision = "0055"
down_revision = "0054"
branch_labels = None
depends_on = None


def upgrade() -> None:
    with op.batch_alter_table("companies", schema=None) as batch_op:
        batch_op.add_column(
            sa.Column("run_window", sa.JSON(none_as_null=True), nullable=True)
        )
    with op.batch_alter_table("sprints", schema=None) as batch_op:
        batch_op.add_column(
            sa.Column("urgent", sa.Boolean(), nullable=False, server_default=sa.false())
        )
        batch_op.add_column(
            sa.Column("run_window_wait_until", sa.DateTime(), nullable=True)
        )


def downgrade() -> None:
    with op.batch_alter_table("sprints", schema=None) as batch_op:
        batch_op.drop_column("run_window_wait_until")
        batch_op.drop_column("urgent")
    with op.batch_alter_table("companies", schema=None) as batch_op:
        batch_op.drop_column("run_window")
