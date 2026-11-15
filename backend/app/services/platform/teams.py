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
"""

from __future__ import annotations

from collections.abc import Iterable

from models import CompanyMember, Team, TeamMember
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
