"""Identity-provider groups mapped to teams (issue #703; ``docs/archive/design/SSO_GROUP_MAPPING.md``).

The Owner's configuration, the IdP's observation and a second Owner's
decision never share a field (POSITIONING design rule 1):

- ``SsoGroupMapping``  — an active ``(group value, team)`` pair of a
  connection, written by an Owner; a pair to a Manager team exists only once
  a second Owner approved it (``approval_id``).
- ``SsoManagerApproval`` — a proposed pair to a Manager team, pending until a
  second Owner decides it; never deleted, its decision fields written once.
- ``SsoTeamExclusion`` — a pinned break-glass exclusion: the mapping never
  adds the person to the team while it is in force.
- ``SsoManualException`` — a member an Owner had placed by hand in a team
  converted to SSO: the mapping never removes them while it is in force.
- ``TeamMembershipSourcePreview`` — what converting a team to SSO would keep,
  hashed by the backend, single use, for 15 minutes.
- ``SsoGroupObservation`` — what the IdP said at each evaluation: the claim's
  state, the matched values only, their count and the backend's sha256 of
  the full list; append-only (``sso_group_sync``'s guard).

Accounts are kept by id (``requested_by``, ``decided_by``, ``created_by``...),
as segregation of duties matches them (``sod.same_person``).
"""

from datetime import UTC, datetime
from typing import Any

from sqlalchemy import JSON, Column, Index, UniqueConstraint, text
from sqlmodel import Field, SQLModel


def _utcnow() -> datetime:
    return datetime.now(UTC).replace(tzinfo=None)


class SsoGroupMapping(SQLModel, table=True):
    __tablename__ = "sso_group_mappings"
    __table_args__ = (UniqueConstraint("connection_id", "group_value", "team_id"),)

    id: int | None = Field(default=None, primary_key=True)
    company_id: int = Field(foreign_key="companies.id", index=True)
    connection_id: int = Field(
        foreign_key="sso_connections.id", ondelete="CASCADE", index=True
    )
    group_value: str  # at most 256 characters; lower-cased for Entra group ids
    team_id: int = Field(foreign_key="teams.id", ondelete="CASCADE", index=True)
    created_by: int | None = None  # the Owner's account id
    created_at: datetime = Field(default_factory=_utcnow)
    approval_id: int | None = None  # set for a pair to a Manager team


class SsoManagerApproval(SQLModel, table=True):
    __tablename__ = "sso_manager_approvals"

    id: int | None = Field(default=None, primary_key=True)
    company_id: int = Field(foreign_key="companies.id", index=True)
    connection_id: int = Field(index=True)  # no foreign key: outlives it
    group_value: str
    team_id: int = Field(index=True)  # no foreign key: outlives the team
    team_role: str  # the team's role when proposed
    group_role_cap: str  # the connection's cap when proposed
    proposal_sha256: str  # computed by the backend
    requested_by: int  # the proposer's account id, from the session
    requested_at: datetime = Field(default_factory=_utcnow)
    expires_at: datetime
    # pending | approved | rejected | withdrawn | expired | confirmed_single_owner
    state: str = "pending"
    decided_by: int | None = None  # from the session
    decided_at: datetime | None = None
    reason: str | None = None
    reviewed_in: int | None = None  # the access review that confirmed it


EXCLUSION_IN_FORCE_INDEX = "ux_sso_team_exclusions_in_force"
EXCEPTION_IN_FORCE_INDEX = "ux_sso_manual_exceptions_in_force"


class SsoTeamExclusion(SQLModel, table=True):
    __tablename__ = "sso_team_exclusions"
    __table_args__ = (
        Index(
            EXCLUSION_IN_FORCE_INDEX,
            "team_id",
            "user_id",
            unique=True,
            sqlite_where=text("lifted_at IS NULL"),
            postgresql_where=text("lifted_at IS NULL"),
        ),
    )

    id: int | None = Field(default=None, primary_key=True)
    company_id: int = Field(foreign_key="companies.id", index=True)
    team_id: int = Field(foreign_key="teams.id", ondelete="CASCADE", index=True)
    user_id: int = Field(foreign_key="users.id", index=True)
    created_by: int
    created_at: datetime = Field(default_factory=_utcnow)
    reason: str
    lifted_by: int | None = None
    lifted_at: datetime | None = None
    lift_reason: str | None = None


class SsoManualException(SQLModel, table=True):
    __tablename__ = "sso_manual_exceptions"
    __table_args__ = (
        Index(
            EXCEPTION_IN_FORCE_INDEX,
            "team_id",
            "user_id",
            unique=True,
            sqlite_where=text("ended_at IS NULL"),
            postgresql_where=text("ended_at IS NULL"),
        ),
    )

    id: int | None = Field(default=None, primary_key=True)
    company_id: int = Field(foreign_key="companies.id", index=True)
    team_id: int = Field(foreign_key="teams.id", ondelete="CASCADE", index=True)
    user_id: int = Field(foreign_key="users.id", index=True)
    created_by: int  # the Owner who confirmed the conversion
    created_at: datetime = Field(default_factory=_utcnow)
    preview_id: int | None = None
    ended_by: int | None = None
    ended_at: datetime | None = None


class TeamMembershipSourcePreview(SQLModel, table=True):
    __tablename__ = "team_membership_source_previews"

    id: int | None = Field(default=None, primary_key=True)
    company_id: int = Field(foreign_key="companies.id", index=True)
    team_id: int = Field(index=True)
    created_by: int
    created_at: datetime = Field(default_factory=_utcnow)
    expires_at: datetime
    members_sha256: str  # the backend's hash of the member ids and the role
    members: list[Any] = Field(default_factory=list, sa_column=Column(JSON))
    used_at: datetime | None = None


class SsoGroupObservation(SQLModel, table=True):
    __tablename__ = "sso_group_observations"

    id: int | None = Field(default=None, primary_key=True)
    company_id: int = Field(foreign_key="companies.id", index=True)
    connection_id: int = Field(index=True)  # no foreign key: outlives it
    user_id: int = Field(index=True)
    source: str = "sign_in"  # sign_in; directory_sync and scim reserved
    observed_at: datetime = Field(default_factory=_utcnow)
    token_iat: datetime | None = None
    claim_name: str
    state: str  # present | empty | absent | overage | malformed
    value_count: int = 0
    values_sha256: str | None = None
    matched_values: list[Any] = Field(default_factory=list, sa_column=Column(JSON))
    overage_host: str | None = None
    applied: bool = False
    skipped: list[Any] = Field(default_factory=list, sa_column=Column(JSON))
