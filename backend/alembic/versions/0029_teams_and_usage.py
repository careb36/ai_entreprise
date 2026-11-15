"""Teams and the usage report (issue #512, phase 5;
``docs/design/TEAMS_AND_USAGE.md``): ``teams`` (a named group of a company's
members with a role scope, Viewer or Manager), ``team_members`` (one member in
one team) and ``sprints.started_by_user_id`` (the signed-in account that
started a sprint, from the session; empty for every existing sprint, which
the usage report counts as unattributed).

Numbered 0029: 0026 (sessions, #524), 0027 (documentation packs, #530) and
0028 (the project wiki, #445) are in flight on other branches. Until they
land this revision follows 0025; whichever merges after them re-points
``down_revision`` at the head it finds, so the chain keeps one head.

Revision ID: 0029
Revises: 0025
Create Date: 2026-11-15 20:00:00 UTC
"""

from __future__ import annotations

import sqlalchemy as sa
import sqlmodel

from alembic import op

revision = "0029"
down_revision = "0025"
branch_labels = None
depends_on = None


def upgrade() -> None:
    text = sqlmodel.sql.sqltypes.AutoString
    op.create_table(
        "teams",
        sa.Column("id", sa.Integer(), primary_key=True, nullable=False),
        sa.Column(
            "company_id", sa.Integer(), sa.ForeignKey("companies.id"), nullable=False
        ),
        sa.Column("name", text(), nullable=False),
        sa.Column("description", text(), nullable=True),
        sa.Column("role", text(), nullable=False),
        sa.Column("created_by", text(), nullable=True),
        sa.Column("created_at", sa.DateTime(), nullable=False),
        sa.Column("updated_at", sa.DateTime(), nullable=False),
        sa.UniqueConstraint("company_id", "name"),
    )
    op.create_index("ix_teams_company_id", "teams", ["company_id"])

    op.create_table(
        "team_members",
        sa.Column("id", sa.Integer(), primary_key=True, nullable=False),
        sa.Column(
            "company_id", sa.Integer(), sa.ForeignKey("companies.id"), nullable=False
        ),
        sa.Column("team_id", sa.Integer(), sa.ForeignKey("teams.id"), nullable=False),
        sa.Column("user_id", sa.Integer(), sa.ForeignKey("users.id"), nullable=False),
        sa.Column("added_by", text(), nullable=True),
        sa.Column("added_at", sa.DateTime(), nullable=False),
        sa.UniqueConstraint("team_id", "user_id"),
    )
    op.create_index("ix_team_members_company_id", "team_members", ["company_id"])
    op.create_index("ix_team_members_team_id", "team_members", ["team_id"])
    op.create_index("ix_team_members_user_id", "team_members", ["user_id"])

    with op.batch_alter_table("sprints", schema=None) as batch:
        batch.add_column(sa.Column("started_by_user_id", sa.Integer(), nullable=True))
        batch.create_foreign_key(
            "fk_sprints_started_by_user_id", "users", ["started_by_user_id"], ["id"]
        )


def downgrade() -> None:
    with op.batch_alter_table("sprints", schema=None) as batch:
        batch.drop_constraint("fk_sprints_started_by_user_id", type_="foreignkey")
        batch.drop_column("started_by_user_id")
    op.drop_index("ix_team_members_user_id", table_name="team_members")
    op.drop_index("ix_team_members_team_id", table_name="team_members")
    op.drop_index("ix_team_members_company_id", table_name="team_members")
    op.drop_table("team_members")
    op.drop_index("ix_teams_company_id", table_name="teams")
    op.drop_table("teams")
