"""A routine's revisions and variables (issue #774): ``routines.variables``
(its declared inputs, ``[]`` for every existing routine) and
``routines.revision`` (the number of its latest revision, ``1``), and
``routine_revisions``, one row per version of a routine. Each existing
routine gets its revision 1, a snapshot of what it holds now.

Follows 0062 (routines).

Revision ID: 0063
Revises: 0062
Create Date: 2026-11-22 18:00:00 UTC
"""

from __future__ import annotations

import json
from datetime import UTC, datetime

import sqlalchemy as sa
import sqlmodel

from alembic import op

revision = "0063"
down_revision = "0062"
branch_labels = None
depends_on = None

SNAPSHOT_FIELDS = ("title", "description", "assignee", "schedule", "enabled")


def upgrade() -> None:
    with op.batch_alter_table("routines", schema=None) as batch_op:
        batch_op.add_column(
            sa.Column("variables", sa.JSON(), nullable=False, server_default="[]")
        )
        batch_op.add_column(
            sa.Column("revision", sa.Integer(), nullable=False, server_default="1")
        )
    with op.batch_alter_table("routines", schema=None) as batch_op:
        batch_op.alter_column("variables", server_default=None)
        batch_op.alter_column("revision", server_default=None)
    text = sqlmodel.sql.sqltypes.AutoString
    revisions = op.create_table(
        "routine_revisions",
        sa.Column("id", sa.Integer(), primary_key=True, nullable=False),
        sa.Column(
            "company_id", sa.Integer(), sa.ForeignKey("companies.id"), nullable=False
        ),
        sa.Column("routine_id", sa.Integer(), nullable=False),
        sa.Column("number", sa.Integer(), nullable=False),
        sa.Column("snapshot", sa.JSON(), nullable=False),
        sa.Column("change_summary", text(), nullable=True),
        sa.Column("restored_from", sa.Integer(), nullable=True),
        sa.Column(
            "created_by_user_id", sa.Integer(), sa.ForeignKey("users.id"), nullable=True
        ),
        sa.Column("created_at", sa.DateTime(), nullable=False),
        sa.UniqueConstraint("routine_id", "number"),
    )
    op.create_index(
        "ix_routine_revisions_company_id", "routine_revisions", ["company_id"]
    )
    op.create_index(
        "ix_routine_revisions_routine_id", "routine_revisions", ["routine_id"]
    )
    bind = op.get_bind()
    rows = bind.execute(
        sa.text(
            "SELECT id, company_id, title, description, assignee, schedule, enabled, "
            "created_by_user_id FROM routines"
        )
    ).mappings()
    now = datetime.now(UTC).replace(tzinfo=None)
    seeds = []
    for row in rows:
        schedule = row["schedule"]
        if isinstance(schedule, str):
            schedule = json.loads(schedule)
        snapshot = {f: row[f] for f in SNAPSHOT_FIELDS}
        snapshot["schedule"] = schedule
        snapshot["enabled"] = bool(row["enabled"])
        snapshot["variables"] = []
        seeds.append(
            {
                "company_id": row["company_id"],
                "routine_id": row["id"],
                "number": 1,
                "snapshot": snapshot,
                "change_summary": "The routine as it was when revisions began",
                "restored_from": None,
                "created_by_user_id": row["created_by_user_id"],
                "created_at": now,
            }
        )
    if seeds:
        op.bulk_insert(revisions, seeds)


def downgrade() -> None:
    op.drop_index("ix_routine_revisions_routine_id", table_name="routine_revisions")
    op.drop_index("ix_routine_revisions_company_id", table_name="routine_revisions")
    op.drop_table("routine_revisions")
    with op.batch_alter_table("routines", schema=None) as batch_op:
        batch_op.drop_column("revision")
        batch_op.drop_column("variables")
