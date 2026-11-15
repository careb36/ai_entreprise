"""Teams inside a company (issue #512, phase 5; ``docs/design/TEAMS_AND_USAGE.md``).

- ``Team``        — a named group of a company's members with a role scope:
  ``Viewer`` or ``Manager``, never ``Owner``. Written only by an Owner.
- ``TeamMember``  — one member in one team. A row grants nothing on its own:
  the person must also be a member of the company (``company_members``), and
  their effective role there is the higher of their own role and their
  teams' roles (``services/platform/teams.py``).
"""

from datetime import UTC, datetime

from sqlalchemy import UniqueConstraint
from sqlmodel import Field, SQLModel


def _utcnow() -> datetime:
    return datetime.now(UTC).replace(tzinfo=None)


class Team(SQLModel, table=True):
    __tablename__ = "teams"
    __table_args__ = (UniqueConstraint("company_id", "name"),)

    id: int | None = Field(default=None, primary_key=True)
    company_id: int = Field(foreign_key="companies.id", index=True)
    name: str
    description: str | None = None
    role: str = "Viewer"  # "Viewer" | "Manager"; never "Owner"
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
