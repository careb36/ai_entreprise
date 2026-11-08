"""Add archived_at, archived_by and archive_reason to sprints: a halted
sprint with recorded work is archived by its Owner instead of deleted
(``services/sprint_archive.py``). All nullable: every existing sprint stays
listed.

Revision ID: 0016
Revises: 0015
Create Date: 2026-11-08 22:00:00 UTC
"""

from __future__ import annotations

import sqlalchemy as sa
import sqlmodel

from alembic import op

revision = "0016"
down_revision = "0015"
branch_labels = None
depends_on = None


def upgrade() -> None:
    text = sqlmodel.sql.sqltypes.AutoString
    with op.batch_alter_table("sprints", schema=None) as batch_op:
        batch_op.add_column(sa.Column("archived_at", sa.DateTime(), nullable=True))
        batch_op.add_column(sa.Column("archived_by", text(), nullable=True))
        batch_op.add_column(sa.Column("archive_reason", text(), nullable=True))


def downgrade() -> None:
    with op.batch_alter_table("sprints", schema=None) as batch_op:
        batch_op.drop_column("archive_reason")
        batch_op.drop_column("archived_by")
        batch_op.drop_column("archived_at")
