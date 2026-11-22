"""Identity-provider groups mapped to teams (issue #703;
``docs/archive/design/SSO_GROUP_MAPPING.md``).

- ``sso_connections``: ``groups_enabled`` (false), ``groups_claim``
  (``groups``) and ``group_role_cap`` (``Viewer``; a check constraint keeps
  ``Owner`` out).
- ``teams.membership_source``: ``owner`` or ``sso``; every existing team stays
  ``owner``, so nothing an Owner placed by hand changes.
- ``sso_group_mappings``, ``sso_manager_approvals``, ``sso_team_exclusions``,
  ``sso_manual_exceptions``, ``team_membership_source_previews`` and
  ``sso_group_observations`` (append-only).
- ``team_membership_periods``: ``started_observation_id``,
  ``ended_observation_id`` and ``ended_exclusion_id``, null for every
  existing period.

No existing row changes beyond the new columns' defaults.

Follows 0053 (the forge policy, PR #730).

Revision ID: 0054
Revises: 0053
Create Date: 2026-11-19 20:00:00 UTC
"""

from __future__ import annotations

import sqlalchemy as sa
import sqlmodel

from alembic import op

revision = "0054"
down_revision = "0053"
branch_labels = None
depends_on = None

CAP_CHECK = "ck_sso_connections_group_role_cap"
SOURCE_CHECK = "ck_teams_membership_source"
EXCLUSION_INDEX = "ux_sso_team_exclusions_in_force"
EXCEPTION_INDEX = "ux_sso_manual_exceptions_in_force"
NEW_TABLES = (
    "sso_group_observations",
    "team_membership_source_previews",
    "sso_manual_exceptions",
    "sso_team_exclusions",
    "sso_manager_approvals",
    "sso_group_mappings",
)
INDEXED = {
    "sso_group_mappings": ("company_id", "connection_id", "team_id"),
    "sso_manager_approvals": ("company_id", "connection_id", "team_id"),
    "sso_team_exclusions": ("company_id", "team_id", "user_id"),
    "sso_manual_exceptions": ("company_id", "team_id", "user_id"),
    "team_membership_source_previews": ("company_id", "team_id"),
    "sso_group_observations": ("company_id", "connection_id", "user_id"),
}
PERIOD_COLUMNS = (
    "started_observation_id",
    "ended_observation_id",
    "ended_exclusion_id",
)


def _company():
    return sa.Column(
        "company_id", sa.Integer(), sa.ForeignKey("companies.id"), nullable=False
    )


def _team_fk():
    return sa.Column(
        "team_id",
        sa.Integer(),
        sa.ForeignKey("teams.id", ondelete="CASCADE"),
        nullable=False,
    )


def upgrade() -> None:
    text = sqlmodel.sql.sqltypes.AutoString
    with op.batch_alter_table("sso_connections", schema=None) as batch:
        batch.add_column(
            sa.Column(
                "groups_enabled",
                sa.Boolean(),
                nullable=False,
                server_default=sa.false(),
            )
        )
        batch.add_column(
            sa.Column("groups_claim", text(), nullable=False, server_default="groups")
        )
        batch.add_column(
            sa.Column("group_role_cap", text(), nullable=False, server_default="Viewer")
        )
        batch.create_check_constraint(
            CAP_CHECK, "group_role_cap IN ('Viewer', 'Manager')"
        )
    with op.batch_alter_table("teams", schema=None) as batch:
        batch.add_column(
            sa.Column(
                "membership_source", text(), nullable=False, server_default="owner"
            )
        )
        batch.create_check_constraint(
            SOURCE_CHECK, "membership_source IN ('owner', 'sso')"
        )
    with op.batch_alter_table("team_membership_periods", schema=None) as batch:
        for column in PERIOD_COLUMNS:
            batch.add_column(sa.Column(column, sa.Integer(), nullable=True))

    op.create_table(
        "sso_group_mappings",
        sa.Column("id", sa.Integer(), primary_key=True, nullable=False),
        _company(),
        sa.Column(
            "connection_id",
            sa.Integer(),
            sa.ForeignKey("sso_connections.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("group_value", text(), nullable=False),
        _team_fk(),
        sa.Column("created_by", sa.Integer(), nullable=True),
        sa.Column("created_at", sa.DateTime(), nullable=False),
        sa.Column("approval_id", sa.Integer(), nullable=True),
        sa.UniqueConstraint("connection_id", "group_value", "team_id"),
    )
    op.create_table(
        "sso_manager_approvals",
        sa.Column("id", sa.Integer(), primary_key=True, nullable=False),
        _company(),
        sa.Column("connection_id", sa.Integer(), nullable=False),
        sa.Column("group_value", text(), nullable=False),
        sa.Column("team_id", sa.Integer(), nullable=False),
        sa.Column("team_role", text(), nullable=False),
        sa.Column("group_role_cap", text(), nullable=False),
        sa.Column("proposal_sha256", text(), nullable=False),
        sa.Column("requested_by", sa.Integer(), nullable=False),
        sa.Column("requested_at", sa.DateTime(), nullable=False),
        sa.Column("expires_at", sa.DateTime(), nullable=False),
        sa.Column("state", text(), nullable=False),
        sa.Column("decided_by", sa.Integer(), nullable=True),
        sa.Column("decided_at", sa.DateTime(), nullable=True),
        sa.Column("reason", text(), nullable=True),
        sa.Column("reviewed_in", sa.Integer(), nullable=True),
    )
    op.create_table(
        "sso_team_exclusions",
        sa.Column("id", sa.Integer(), primary_key=True, nullable=False),
        _company(),
        _team_fk(),
        sa.Column("user_id", sa.Integer(), sa.ForeignKey("users.id"), nullable=False),
        sa.Column("created_by", sa.Integer(), nullable=False),
        sa.Column("created_at", sa.DateTime(), nullable=False),
        sa.Column("reason", text(), nullable=False),
        sa.Column("lifted_by", sa.Integer(), nullable=True),
        sa.Column("lifted_at", sa.DateTime(), nullable=True),
        sa.Column("lift_reason", text(), nullable=True),
    )
    op.create_table(
        "sso_manual_exceptions",
        sa.Column("id", sa.Integer(), primary_key=True, nullable=False),
        _company(),
        _team_fk(),
        sa.Column("user_id", sa.Integer(), sa.ForeignKey("users.id"), nullable=False),
        sa.Column("created_by", sa.Integer(), nullable=False),
        sa.Column("created_at", sa.DateTime(), nullable=False),
        sa.Column("preview_id", sa.Integer(), nullable=True),
        sa.Column("ended_by", sa.Integer(), nullable=True),
        sa.Column("ended_at", sa.DateTime(), nullable=True),
    )
    op.create_table(
        "team_membership_source_previews",
        sa.Column("id", sa.Integer(), primary_key=True, nullable=False),
        _company(),
        sa.Column("team_id", sa.Integer(), nullable=False),
        sa.Column("created_by", sa.Integer(), nullable=False),
        sa.Column("created_at", sa.DateTime(), nullable=False),
        sa.Column("expires_at", sa.DateTime(), nullable=False),
        sa.Column("members_sha256", text(), nullable=False),
        sa.Column("members", sa.JSON(), nullable=True),
        sa.Column("used_at", sa.DateTime(), nullable=True),
    )
    op.create_table(
        "sso_group_observations",
        sa.Column("id", sa.Integer(), primary_key=True, nullable=False),
        _company(),
        sa.Column("connection_id", sa.Integer(), nullable=False),
        sa.Column("user_id", sa.Integer(), nullable=False),
        sa.Column("source", text(), nullable=False),
        sa.Column("observed_at", sa.DateTime(), nullable=False),
        sa.Column("token_iat", sa.DateTime(), nullable=True),
        sa.Column("claim_name", text(), nullable=False),
        sa.Column("state", text(), nullable=False),
        sa.Column("value_count", sa.Integer(), nullable=False),
        sa.Column("values_sha256", text(), nullable=True),
        sa.Column("matched_values", sa.JSON(), nullable=True),
        sa.Column("overage_host", text(), nullable=True),
        sa.Column("applied", sa.Boolean(), nullable=False),
        sa.Column("skipped", sa.JSON(), nullable=True),
    )
    for table, columns in INDEXED.items():
        for column in columns:
            op.create_index(f"ix_{table}_{column}", table, [column])
    for name, table, ended in (
        (EXCLUSION_INDEX, "sso_team_exclusions", "lifted_at"),
        (EXCEPTION_INDEX, "sso_manual_exceptions", "ended_at"),
    ):
        op.create_index(
            name,
            table,
            ["team_id", "user_id"],
            unique=True,
            sqlite_where=sa.text(f"{ended} IS NULL"),
            postgresql_where=sa.text(f"{ended} IS NULL"),
        )


def downgrade() -> None:
    op.drop_index(EXCEPTION_INDEX, table_name="sso_manual_exceptions")
    op.drop_index(EXCLUSION_INDEX, table_name="sso_team_exclusions")
    for table, columns in INDEXED.items():
        for column in columns:
            op.drop_index(f"ix_{table}_{column}", table_name=table)
    for table in NEW_TABLES:
        op.drop_table(table)
    with op.batch_alter_table("team_membership_periods", schema=None) as batch:
        for column in reversed(PERIOD_COLUMNS):
            batch.drop_column(column)
    with op.batch_alter_table("teams", schema=None) as batch:
        batch.drop_constraint(SOURCE_CHECK, type_="check")
        batch.drop_column("membership_source")
    with op.batch_alter_table("sso_connections", schema=None) as batch:
        batch.drop_constraint(CAP_CHECK, type_="check")
        for column in ("group_role_cap", "groups_claim", "groups_enabled"):
            batch.drop_column(column)
