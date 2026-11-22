"""Team membership history (issue #702; ``docs/archive/design/TEAMS_AND_USAGE.md``,
Team membership history): ``team_membership_periods`` (when a member was in a
team, opened and closed by the backend when the membership changes, never
edited after) and ``team_history_start`` (the moment this migration ran).

Seeds one open period per existing ``team_members`` row, starting at the
moment the migration runs, and records that moment: the usage report gives
work from before it today's teams and says which period that covers. No
existing row changes.

Follows 0051 (PR #681).

Revision ID: 0052
Revises: 0051
Create Date: 2026-11-19 12:00:00 UTC
"""

from __future__ import annotations

from datetime import UTC, datetime

import sqlalchemy as sa
import sqlmodel

from alembic import op

revision = "0052"
down_revision = "0051"
branch_labels = None
depends_on = None

OPEN_INDEX = "ux_team_membership_periods_open"
SEEDED_BY = "migration:0052"


def upgrade() -> None:
    text = sqlmodel.sql.sqltypes.AutoString
    periods = op.create_table(
        "team_membership_periods",
        sa.Column("id", sa.Integer(), primary_key=True, nullable=False),
        sa.Column(
            "company_id", sa.Integer(), sa.ForeignKey("companies.id"), nullable=False
        ),
        sa.Column("team_id", sa.Integer(), nullable=False),
        sa.Column("team_name", text(), nullable=False),
        sa.Column("user_id", sa.Integer(), sa.ForeignKey("users.id"), nullable=False),
        sa.Column("started_at", sa.DateTime(), nullable=False),
        sa.Column("started_by", text(), nullable=True),
        sa.Column("ended_at", sa.DateTime(), nullable=True),
        sa.Column("ended_by", text(), nullable=True),
    )
    for column in ("company_id", "team_id", "user_id"):
        op.create_index(
            f"ix_team_membership_periods_{column}",
            "team_membership_periods",
            [column],
        )
    op.create_index(
        OPEN_INDEX,
        "team_membership_periods",
        ["team_id", "user_id"],
        unique=True,
        sqlite_where=sa.text("ended_at IS NULL"),
        postgresql_where=sa.text("ended_at IS NULL"),
    )
    start = op.create_table(
        "team_history_start",
        sa.Column("id", sa.Integer(), primary_key=True, nullable=False),
        sa.Column("started_at", sa.DateTime(), nullable=False),
    )

    now = datetime.now(UTC).replace(tzinfo=None, microsecond=0)
    bind = op.get_bind()
    members = sa.table(
        "team_members",
        sa.column("company_id", sa.Integer),
        sa.column("team_id", sa.Integer),
        sa.column("user_id", sa.Integer),
    )
    teams = sa.table(
        "teams",
        sa.column("id", sa.Integer),
        sa.column("company_id", sa.Integer),
        sa.column("name"),
    )
    rows = bind.execute(
        sa.select(
            members.c.company_id, members.c.team_id, teams.c.name, members.c.user_id
        )
        .select_from(
            members.join(
                teams,
                sa.and_(
                    teams.c.id == members.c.team_id,
                    teams.c.company_id == members.c.company_id,
                ),
            )
        )
        .order_by(members.c.team_id, members.c.user_id)
    ).fetchall()
    if rows:
        op.bulk_insert(
            periods,
            [
                {
                    "company_id": company_id,
                    "team_id": team_id,
                    "team_name": name,
                    "user_id": user_id,
                    "started_at": now,
                    "started_by": SEEDED_BY,
                }
                for company_id, team_id, name, user_id in rows
            ],
        )
    op.bulk_insert(start, [{"started_at": now}])


def downgrade() -> None:
    op.drop_table("team_history_start")
    op.drop_index(OPEN_INDEX, table_name="team_membership_periods")
    for column in ("company_id", "team_id", "user_id"):
        op.drop_index(
            f"ix_team_membership_periods_{column}",
            table_name="team_membership_periods",
        )
    op.drop_table("team_membership_periods")
