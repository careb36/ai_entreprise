"""Teams and the effective role (issue #512, phase 5; ``docs/design/TEAMS_AND_USAGE.md``).

A company's Owner groups its members in teams; a team carries a role scope,
``Viewer`` or ``Manager``, that applies to its members within that company.
A member's **effective role** there is the higher of their own membership
role and the roles of the teams they belong to:

- a team never makes anyone a member: without a ``company_members`` row the
  effective role is ``None``, whatever team rows remain;
- a team never grants ``Owner``: a team row carrying any role outside
  ``TEAM_ROLES`` (a database edited by hand) grants nothing;
- only teams of the same company count (``TeamMember.company_id`` and
  ``Team.company_id`` both checked).

``auth_policy.require_company_role`` reads it through ``member_role``; so do
the API token cap and the brief's recipients. The design follows Aixle Flow's
project teams (Apache-2.0, design only: ``docs/design/project-teams.md``, a
team is a named group of members of the same tenant, managed by its admins,
and a member must already belong before joining), with a role scope where Flow
has a run-owner pick.

**Membership history** (issue #702): every change to a membership also
writes ``team_membership_periods``: ``open_membership`` when a member joins,
``close_memberships`` when they leave the team, the team is deleted or they
leave the company (``drop_memberships``). The backend writes it at the moment
of the change, with who made it; a closed period is never edited and none is
deleted (``HistoryRefused``, raised before the database is touched), so a
person who joins again gets a new period. The usage report reads it to give
each piece of work the teams of its time (``membership_periods``,
``history_started_at``).
"""

from __future__ import annotations

from collections.abc import Iterable
from datetime import UTC, datetime

from models import (
    CompanyMember,
    Team,
    TeamHistoryStart,
    TeamMember,
    TeamMembershipPeriod,
)
from sqlalchemy import event
from sqlmodel import Session, select

ROLE_RANK = {"Viewer": 1, "Manager": 2, "Owner": 3}
TEAM_ROLES = ("Viewer", "Manager")  # never "Owner"
NAME_MAX = 80
DESCRIPTION_MAX = 500


def highest(roles: Iterable[str | None]) -> str | None:
    """The highest-ranked known role of *roles*, ``None`` when none is known."""
    best: str | None = None
    for role in roles:
        if role in ROLE_RANK and (best is None or ROLE_RANK[role] > ROLE_RANK[best]):
            best = role
    return best


def effective_role(own: str | None, team_roles: Iterable[str | None]) -> str | None:
    """Pure: the member's own role raised by their teams' roles.

    ``own`` ``None`` (not a member) stays ``None``; a team role outside
    ``TEAM_ROLES`` is ignored, so a team never yields ``Owner``.
    """
    if own not in ROLE_RANK:
        return None
    return highest([own, *(r for r in team_roles if r in TEAM_ROLES)])


def team_roles(session: Session, company_id: int, user_id: int) -> list[str]:
    """The roles of the teams *user_id* belongs to in *company_id*, sorted."""
    rows = session.exec(
        select(Team.role)
        .join(TeamMember, TeamMember.team_id == Team.id)
        .where(
            TeamMember.user_id == user_id,
            TeamMember.company_id == company_id,
            Team.company_id == company_id,
        )
    ).all()
    return sorted(str(r) for r in rows)


def drop_memberships(
    session: Session, company_id: int, user_id: int, by: str | None = None
) -> int:
    """Remove *user_id* from every team of *company_id*; the number removed.

    Called when a person leaves the company, so team rows never outlive the
    membership: invited again later, they start with no team. Their open
    periods are closed, by *by*.
    """
    close_memberships(session, company_id, user_id=user_id, by=by)
    rows = session.exec(
        select(TeamMember).where(
            TeamMember.company_id == company_id, TeamMember.user_id == user_id
        )
    ).all()
    for row in rows:
        session.delete(row)
    return len(rows)


def own_role(session: Session, company_id: int, user_id: int) -> str | None:
    member = session.exec(
        select(CompanyMember).where(
            CompanyMember.user_id == user_id, CompanyMember.company_id == company_id
        )
    ).first()
    return member.human_role if member else None


def member_role(session: Session, company_id: int, user_id: int) -> str | None:
    """The effective role of *user_id* in *company_id*; ``None``: not a member."""
    own = own_role(session, company_id, user_id)
    if own is None:
        return None
    return effective_role(own, team_roles(session, company_id, user_id))


def members_with_role(
    session: Session, company_id: int, minimum: str
) -> dict[int, str]:
    """``{user_id: effective role}`` of the members whose effective role is at
    least *minimum*."""
    own = {
        m.user_id: m.human_role
        for m in session.exec(
            select(CompanyMember).where(CompanyMember.company_id == company_id)
        ).all()
    }
    granted: dict[int, list[str]] = {}
    for user_id, role in session.exec(
        select(TeamMember.user_id, Team.role)
        .join(Team, Team.id == TeamMember.team_id)
        .where(TeamMember.company_id == company_id, Team.company_id == company_id)
    ).all():
        granted.setdefault(user_id, []).append(role)
    out: dict[int, str] = {}
    for user_id, role in own.items():
        effective = effective_role(role, granted.get(user_id, []))
        if effective and ROLE_RANK[effective] >= ROLE_RANK[minimum]:
            out[user_id] = effective
    return out


def teams_of(session: Session, company_id: int) -> dict[int, list[int]]:
    """``{user_id: [team ids]}`` for the company's teams, ids sorted."""
    out: dict[int, list[int]] = {}
    for user_id, team_id in session.exec(
        select(TeamMember.user_id, TeamMember.team_id)
        .join(Team, Team.id == TeamMember.team_id)
        .where(TeamMember.company_id == company_id, Team.company_id == company_id)
        .order_by(TeamMember.user_id, TeamMember.team_id)
    ).all():
        out.setdefault(user_id, []).append(team_id)
    return out


class HistoryRefused(RuntimeError):
    """A write that would edit a closed period, rewrite one, or delete one."""


def _now() -> datetime:
    return datetime.now(UTC).replace(tzinfo=None)


def open_membership(
    session: Session,
    team: Team,
    user_id: int,
    by: str | None,
    at: datetime | None = None,
) -> TeamMembershipPeriod:
    """Open *user_id*'s period in *team* at *at* (now), written by *by*."""
    period = TeamMembershipPeriod(
        company_id=team.company_id,
        team_id=team.id,
        team_name=team.name,
        user_id=user_id,
        started_at=at or _now(),
        started_by=by,
    )
    session.add(period)
    return period


def close_memberships(
    session: Session,
    company_id: int,
    *,
    by: str | None,
    team_id: int | None = None,
    user_id: int | None = None,
    at: datetime | None = None,
) -> int:
    """Close the open periods of *company_id* (of *team_id*, of *user_id*, or
    both) at *at* (now); the number closed. A closed one is left as it is."""
    query = select(TeamMembershipPeriod).where(
        TeamMembershipPeriod.company_id == company_id,
        TeamMembershipPeriod.ended_at.is_(None),
    )
    if team_id is not None:
        query = query.where(TeamMembershipPeriod.team_id == team_id)
    if user_id is not None:
        query = query.where(TeamMembershipPeriod.user_id == user_id)
    rows = session.exec(query).all()
    moment = at or _now()
    for period in rows:
        period.ended_at = max(moment, period.started_at)
        period.ended_by = by
        session.add(period)
    return len(rows)


def history_started_at(session: Session) -> datetime | None:
    """When the membership history started (migration 0052); ``None``: never."""
    return session.exec(
        select(TeamHistoryStart.started_at).order_by(TeamHistoryStart.started_at)
    ).first()


def membership_periods(
    session: Session, company_id: int, user_ids: Iterable[int]
) -> list[TeamMembershipPeriod]:
    """The periods of *user_ids* in *company_id*, oldest first."""
    ids = sorted(set(user_ids))
    if not ids:
        return []
    return list(
        session.exec(
            select(TeamMembershipPeriod)
            .where(
                TeamMembershipPeriod.company_id == company_id,
                TeamMembershipPeriod.user_id.in_(ids),
            )
            .order_by(TeamMembershipPeriod.started_at, TeamMembershipPeriod.id)
        ).all()
    )


_FIXED_FIELDS = (
    "company_id",
    "team_id",
    "team_name",
    "user_id",
    "started_at",
    "started_by",
)


@event.listens_for(TeamMembershipPeriod, "before_update")
def _refuse_edits(_mapper, connection, period: TeamMembershipPeriod) -> None:
    """Only an open period's end is written, once; read back from the
    database, not from the session, so an expired attribute hides nothing."""
    table = TeamMembershipPeriod.__table__
    stored = (
        connection.execute(table.select().where(table.c.id == period.id))
        .mappings()
        .first()
    )
    if stored is None:
        return
    if stored["ended_at"] is not None:
        raise HistoryRefused("a closed membership period is never edited")
    for field in _FIXED_FIELDS:
        if getattr(period, field) != stored[field]:
            raise HistoryRefused(f"a membership period's {field} is never rewritten")


@event.listens_for(TeamMembershipPeriod, "before_delete")
def _refuse_deletes(_mapper, _connection, _period: TeamMembershipPeriod) -> None:
    raise HistoryRefused("a membership period is never deleted")


class TeamRefused(ValueError):
    """A team change that cannot be made, with the HTTP status that says why."""

    def __init__(self, status: int, detail: str):
        super().__init__(detail)
        self.status, self.detail = status, detail


def check_fields(name: str | None, description: str | None, role: str | None):
    """The cleaned ``(name, description, role)``; raises ``TeamRefused`` (422)."""
    clean_name = None if name is None else " ".join(name.split())
    if clean_name is not None and not (1 <= len(clean_name) <= NAME_MAX):
        raise TeamRefused(422, f"A team's name has 1 to {NAME_MAX} characters.")
    clean_description = None if description is None else description.strip()
    if clean_description is not None and len(clean_description) > DESCRIPTION_MAX:
        raise TeamRefused(
            422, f"A team's description has at most {DESCRIPTION_MAX} characters."
        )
    if role is not None and role not in TEAM_ROLES:
        raise TeamRefused(422, "A team's role is Viewer or Manager, never Owner.")
    return clean_name, clean_description, role


def name_taken(
    session: Session, company_id: int, name: str, except_id: int | None = None
) -> bool:
    rows = session.exec(select(Team).where(Team.company_id == company_id)).all()
    return any(t.name.casefold() == name.casefold() and t.id != except_id for t in rows)


def serialize(team: Team, member_ids: list[int]) -> dict:
    return {
        "id": team.id,
        "company_id": team.company_id,
        "name": team.name,
        "description": team.description,
        "role": team.role,
        "members": sorted(member_ids),
        "created_by": team.created_by,
        "created_at": team.created_at.isoformat() if team.created_at else None,
        "updated_at": team.updated_at.isoformat() if team.updated_at else None,
    }
