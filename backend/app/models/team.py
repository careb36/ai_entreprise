"""Teams inside a company (issue #512, phase 5; ``docs/design/TEAMS_AND_USAGE.md``).

- ``Team``        — a named group of a company's members with a role scope:
  ``Viewer`` or ``Manager``, never ``Owner``. Written only by an Owner.
- ``TeamMember``  — one member in one team. A row grants nothing on its own:
  the person must also be a member of the company (``company_members``), and
  their effective role there is the higher of their own role and their
  teams' roles (``services/platform/teams.py``).
- ``TeamMembershipPeriod`` — when a member was in a team (issue #702): opened
  by the backend when an Owner adds them, closed when they leave the team, the
  team is deleted or they leave the company. Only the end is ever written
  after the row is made, and only once; no row is deleted
  (``teams.HistoryRefused``). The team is kept by id and name, without a
  foreign key, so a deleted team's periods stay.
- ``Team.membership_source`` — ``owner`` (members added by hand) or ``sso``
  (members from the SSO group mapping, issue #703).
- ``TeamHistoryStart`` — the moment migration 0052 started the history: the
  usage report gives work before it today's teams, and says so.
"""

from datetime import UTC, datetime

from sqlalchemy import CheckConstraint, Index, UniqueConstraint, text
from sqlmodel import Field, SQLModel


def _utcnow() -> datetime:
    return datetime.now(UTC).replace(tzinfo=None)


MEMBERSHIP_SOURCE_CHECK = "ck_teams_membership_source"


class Team(SQLModel, table=True):
    __tablename__ = "teams"
    __table_args__ = (
        UniqueConstraint("company_id", "name"),
        CheckConstraint(
            "membership_source IN ('owner', 'sso')", name=MEMBERSHIP_SOURCE_CHECK
        ),
    )

    id: int | None = Field(default=None, primary_key=True)
    company_id: int = Field(foreign_key="companies.id", index=True)
    name: str
    description: str | None = None
    role: str = "Viewer"  # "Viewer" | "Manager"; never "Owner"
    # "owner": members added by hand; "sso": from the group mapping (issue #703).
    membership_source: str = "owner"
    created_by: str | None = None
    created_at: datetime = Field(default_factory=_utcnow)
    updated_at: datetime = Field(default_factory=_utcnow)


class TeamMember(SQLModel, table=True):
    __tablename__ = "team_members"
    __table_args__ = (UniqueConstraint("team_id", "user_id"),)

    id: int | None = Field(default=None, primary_key=True)
    # The team's company, kept on the row so every read is scoped by it.
    company_id: int = Field(foreign_key="companies.id", index=True)
    team_id: int = Field(foreign_key="teams.id", index=True)
    user_id: int = Field(foreign_key="users.id", index=True)
    added_by: str | None = None
    added_at: datetime = Field(default_factory=_utcnow)


OPEN_PERIOD_INDEX = "ux_team_membership_periods_open"


class TeamMembershipPeriod(SQLModel, table=True):
    __tablename__ = "team_membership_periods"
    __table_args__ = (
        # At most one open period per team and person.
        Index(
            OPEN_PERIOD_INDEX,
            "team_id",
            "user_id",
            unique=True,
            sqlite_where=text("ended_at IS NULL"),
            postgresql_where=text("ended_at IS NULL"),
        ),
    )

    id: int | None = Field(default=None, primary_key=True)
    company_id: int = Field(foreign_key="companies.id", index=True)
    team_id: int = Field(index=True)  # no foreign key: outlives the team
    team_name: str  # the team's name when the period opened
    user_id: int = Field(foreign_key="users.id", index=True)
    started_at: datetime
    started_by: str | None = None
    ended_at: datetime | None = None
    ended_by: str | None = None
    # The group observation that opened or closed it, the exclusion that
    # closed it (issue #703); no foreign key, null for an Owner's change.
    started_observation_id: int | None = None
    ended_observation_id: int | None = None
    ended_exclusion_id: int | None = None


class TeamHistoryStart(SQLModel, table=True):
    __tablename__ = "team_history_start"

    id: int | None = Field(default=None, primary_key=True)
    started_at: datetime
