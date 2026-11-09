"""Add team and team_source to sprints, and team_selection to companies:
the roles a sprint's tasks may go to, suggested by the PO and confirmed by a
Manager or taken automatically, and who chose them
(``services/sprint_team.py``). Nullable: an existing sprint keeps every role
the company hired, as before, and a company without a value is "suggested".

Revision ID: 0019
Revises: 0018
Create Date: 2026-11-09 12:00:00 UTC
"""

from __future__ import annotations

import sqlalchemy as sa
import sqlmodel

from alembic import op

revision = "0019"
down_revision = "0018"
branch_labels = None
depends_on = None


def upgrade() -> None:
    text = sqlmodel.sql.sqltypes.AutoString
    with op.batch_alter_table("sprints", schema=None) as batch_op:
        batch_op.add_column(sa.Column("team", sa.JSON(), nullable=True))
        batch_op.add_column(sa.Column("team_source", text(), nullable=True))
    with op.batch_alter_table("companies", schema=None) as batch_op:
        batch_op.add_column(sa.Column("team_selection", text(), nullable=True))


def downgrade() -> None:
    with op.batch_alter_table("companies", schema=None) as batch_op:
        batch_op.drop_column("team_selection")
    with op.batch_alter_table("sprints", schema=None) as batch_op:
        batch_op.drop_column("team_source")
        batch_op.drop_column("team")
