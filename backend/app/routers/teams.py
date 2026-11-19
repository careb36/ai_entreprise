"""A company's teams, managed by its Owners (issue #512, phase 5;
``docs/design/TEAMS_AND_USAGE.md``).

A team has a name, a description and a role scope (Viewer or Manager, never
Owner) that applies to its members within the company; a member's effective
role is the higher of their own and their teams' (``services/platform/teams.py``,
read by ``auth_policy.require_company_role``). Only a signed-in human Owner
reads or changes teams: an API token is refused, since a team grants roles.
Every change is audited (``team_*``) with who made it, and every change to a
membership opens or closes a period of the membership history
(``teams.open_membership``, ``teams.close_memberships``; issue #702).

- ``GET    /v1/companies/{id}/teams``                          the teams and the members
- ``POST   /v1/companies/{id}/teams``                          create one
- ``PATCH  /v1/companies/{id}/teams/{team_id}``                rename, describe, change role
- ``DELETE /v1/companies/{id}/teams/{team_id}``                delete it and its memberships
- ``PUT    /v1/companies/{id}/teams/{team_id}/members/{uid}``  add a member of the company
- ``DELETE /v1/companies/{id}/teams/{team_id}/members/{uid}``  remove them

A team is Owner-managed (as above) or SSO-managed (issue #703;
``docs/design/SSO_GROUP_MAPPING.md``): its members come from the
identity provider's groups, so the member routes refuse to add to it and only
end a manual exception (R18); switching it needs a preview and a confirmation
(R19), and a mapped team's role is never raised (R16). The break-glass
exclusion and the preview are in ``routers/team_sso.py``.
"""

from __future__ import annotations

from datetime import UTC, datetime

from auth_policy import current_user_if_required, require_company_role
from database import get_session
from fastapi import APIRouter, Depends, HTTPException
from models import (
    CompanyMember,
    SsoGroupMapping,
    SsoManualException,
    SsoTeamExclusion,
    Team,
    TeamMember,
    User,
)
from pydantic import Field
from schemas import RequestModel
from services.platform import sso_team_control as control
from services.platform import teams
from services.platform.agent_service import AgentService
from sqlmodel import Session, delete, select

router = APIRouter(prefix="/v1", tags=["Company"])


class TeamCreate(RequestModel):
    name: str = Field(min_length=1, max_length=teams.NAME_MAX)
    description: str | None = Field(default=None, max_length=teams.DESCRIPTION_MAX)
    role: str = Field(default="Viewer", pattern="^(Viewer|Manager)$")


class TeamUpdate(RequestModel):
    name: str | None = Field(default=None, min_length=1, max_length=teams.NAME_MAX)
    description: str | None = Field(default=None, max_length=teams.DESCRIPTION_MAX)
    role: str | None = Field(default=None, pattern="^(Viewer|Manager)$")
    membership_source: str | None = Field(default=None, pattern="^(owner|sso)$")
    preview_id: int | None = None
    confirm: bool = False


def _now() -> datetime:
    return datetime.now(UTC).replace(tzinfo=None)


def _owner(session: Session, user: User | None, company_id: int) -> User:
    if user is None:
        raise HTTPException(status_code=401, detail="Not authenticated")
    if getattr(user, "_api_token_company_id", None) is not None:
        raise HTTPException(
            status_code=403, detail="Teams are changed by a person, not a token"
        )
    require_company_role(session, user, company_id, "Owner")
    return user


def _team(session: Session, company_id: int, team_id: int) -> Team:
    team = session.get(Team, team_id)
    if team is None or team.company_id != company_id:
        raise HTTPException(status_code=404, detail="Team not found")
    return team


def _refused(exc: teams.TeamRefused | control.ControlRefused) -> HTTPException:
    return HTTPException(status_code=exc.status, detail=exc.detail)


def _audit(session: Session, company_id: int, actor: User, action: str, **result):
    AgentService().audit_interaction(
        session=session,
        company_id=company_id,
        interaction_id=f"teams:{company_id}",
        agent_id=f"human:{actor.email}",
        action=action,
        details={"company_id": company_id},
        result=result,
        decision_signature=None,
        cost_tokens=0,
    )


def _member_ids(session: Session, team: Team) -> list[int]:
    return list(
        session.exec(
            select(TeamMember.user_id).where(
                TeamMember.team_id == team.id,
                TeamMember.company_id == team.company_id,
            )
        ).all()
    )


@router.get("/companies/{company_id}/teams")
async def list_teams(
    company_id: int,
    session: Session = Depends(get_session),  # noqa: B008 - FastAPI dependency marker.
    user: User | None = Depends(current_user_if_required),  # noqa: B008 - FastAPI dependency marker.
):
    """The company's teams with their members, and every member of the company
    with their own role, effective role and teams (what the Owner picks from)."""
    _owner(session, user, company_id)
    rows = session.exec(
        select(Team).where(Team.company_id == company_id).order_by(Team.name, Team.id)
    ).all()
    of_user = teams.teams_of(session, company_id)
    held = control.holds(session, company_id)
    members = session.exec(
        select(CompanyMember, User)
        .join(User, User.id == CompanyMember.user_id)
        .where(CompanyMember.company_id == company_id)
        .order_by(User.full_name, User.id)
    ).all()
    return {
        "company_id": company_id,
        "roles": list(teams.TEAM_ROLES),
        "teams": [
            {
                **teams.serialize(t, _member_ids(session, t)),
                **held.get(t.id, {"exclusions": [], "manual_exceptions": []}),
            }
            for t in rows
        ],
        "members": [
            {
                "user_id": person.id,
                "full_name": person.full_name,
                "email": person.email,
                "human_role": member.human_role,
                "effective_role": teams.member_role(session, company_id, person.id),
                "team_ids": of_user.get(person.id, []),
            }
            for member, person in members
        ],
    }


@router.post("/companies/{company_id}/teams", status_code=201)
async def create_team(
    company_id: int,
    body: TeamCreate,
    session: Session = Depends(get_session),  # noqa: B008 - FastAPI dependency marker.
    user: User | None = Depends(current_user_if_required),  # noqa: B008 - FastAPI dependency marker.
):
    actor = _owner(session, user, company_id)
    try:
        name, description, role = teams.check_fields(
            body.name, body.description, body.role
        )
    except teams.TeamRefused as exc:
        raise _refused(exc) from exc
    if teams.name_taken(session, company_id, name):
        raise HTTPException(status_code=409, detail="A team with that name exists")
    team = Team(
        company_id=company_id,
        name=name,
        description=description or None,
        role=role,
        created_by=actor.email,
    )
    session.add(team)
    session.flush()
    _audit(
        session,
        company_id,
        actor,
        "team_created",
        team_id=team.id,
        name=name,
        role=role,
    )
    session.commit()
    session.refresh(team)
    return teams.serialize(team, [])


@router.patch("/companies/{company_id}/teams/{team_id}")
async def update_team(
    company_id: int,
    team_id: int,
    body: TeamUpdate,
    session: Session = Depends(get_session),  # noqa: B008 - FastAPI dependency marker.
    user: User | None = Depends(current_user_if_required),  # noqa: B008 - FastAPI dependency marker.
):
    actor = _owner(session, user, company_id)
    team = _team(session, company_id, team_id)
    try:
        name, description, role = teams.check_fields(
            body.name, body.description, body.role
        )
    except teams.TeamRefused as exc:
        raise _refused(exc) from exc
    changed: dict[str, dict] = {}
    if name is not None and name != team.name:
        if teams.name_taken(session, company_id, name, except_id=team.id):
            raise HTTPException(status_code=409, detail="A team with that name exists")
        changed["name"] = {"from": team.name, "to": name}
        team.name = name
    if description is not None and (description or None) != team.description:
        changed["description"] = {"from": team.description, "to": description or None}
        team.description = description or None
    extra: dict = {}
    try:
        if role is not None and role != team.role:
            control.check_role(session, team, role)
            changed["role"] = {"from": team.role, "to": role}
            team.role = role
        if body.membership_source is not None:
            extra = (
                control.switch_source(
                    session,
                    team,
                    actor,
                    body.membership_source,
                    body.preview_id,
                    body.confirm,
                )
                or {}
            )
    except control.ControlRefused as exc:
        session.rollback()
        raise _refused(exc) from exc
    if "membership_source" in extra:
        changed["membership_source"] = extra.pop("membership_source")
    if changed:
        team.updated_at = _now()
        session.add(team)
        _audit(
            session,
            company_id,
            actor,
            "team_updated",
            team_id=team.id,
            changed=changed,
            **extra,
        )
        session.commit()
        session.refresh(team)
    return teams.serialize(team, _member_ids(session, team))


@router.delete("/companies/{company_id}/teams/{team_id}", status_code=204)
async def delete_team(
    company_id: int,
    team_id: int,
    session: Session = Depends(get_session),  # noqa: B008 - FastAPI dependency marker.
    user: User | None = Depends(current_user_if_required),  # noqa: B008 - FastAPI dependency marker.
):
    actor = _owner(session, user, company_id)
    team = _team(session, company_id, team_id)
    members = sorted(_member_ids(session, team))
    teams.close_memberships(
        session, company_id, team_id=team.id, by=f"human:{actor.email}"
    )
    session.exec(delete(TeamMember).where(TeamMember.team_id == team.id))
    # What the foreign keys cascade on Postgres, also where they are not enforced.
    for model in (SsoGroupMapping, SsoTeamExclusion, SsoManualException):
        session.exec(delete(model).where(model.team_id == team.id))
    session.delete(team)
    _audit(
        session,
        company_id,
        actor,
        "team_deleted",
        team_id=team_id,
        name=team.name,
        role=team.role,
        members=members,
    )
    session.commit()


@router.put("/companies/{company_id}/teams/{team_id}/members/{user_id}")
async def add_team_member(
    company_id: int,
    team_id: int,
    user_id: int,
    session: Session = Depends(get_session),  # noqa: B008 - FastAPI dependency marker.
    user: User | None = Depends(current_user_if_required),  # noqa: B008 - FastAPI dependency marker.
):
    """Add a member of the company to the team; adding one already in it changes nothing."""
    actor = _owner(session, user, company_id)
    team = _team(session, company_id, team_id)
    try:
        control.check_add(team)
    except control.ControlRefused as exc:
        raise _refused(exc) from exc
    if teams.own_role(session, company_id, user_id) is None:
        # A team never makes anyone a member: invite them first.
        raise HTTPException(status_code=404, detail="Not a member of this company")
    existing = session.exec(
        select(TeamMember).where(
            TeamMember.team_id == team.id, TeamMember.user_id == user_id
        )
    ).first()
    if existing is None:
        session.add(
            TeamMember(
                company_id=company_id,
                team_id=team.id,
                user_id=user_id,
                added_by=actor.email,
            )
        )
        teams.open_membership(session, team, user_id, by=f"human:{actor.email}")
        _audit(
            session,
            company_id,
            actor,
            "team_member_added",
            team_id=team.id,
            user_id=user_id,
            role=team.role,
        )
        session.commit()
    return teams.serialize(team, _member_ids(session, team))


@router.delete("/companies/{company_id}/teams/{team_id}/members/{user_id}")
async def remove_team_member(
    company_id: int,
    team_id: int,
    user_id: int,
    session: Session = Depends(get_session),  # noqa: B008 - FastAPI dependency marker.
    user: User | None = Depends(current_user_if_required),  # noqa: B008 - FastAPI dependency marker.
):
    actor = _owner(session, user, company_id)
    team = _team(session, company_id, team_id)
    if team.membership_source == "sso":
        try:
            control.remove_by_hand(session, team, actor, user_id)
        except control.ControlRefused as exc:
            session.rollback()
            raise _refused(exc) from exc
        session.commit()
        return teams.serialize(team, _member_ids(session, team))
    existing = session.exec(
        select(TeamMember).where(
            TeamMember.team_id == team.id, TeamMember.user_id == user_id
        )
    ).first()
    if existing is None:
        raise HTTPException(status_code=404, detail="Not in this team")
    session.delete(existing)
    teams.close_memberships(
        session,
        company_id,
        team_id=team.id,
        user_id=user_id,
        by=f"human:{actor.email}",
    )
    _audit(
        session,
        company_id,
        actor,
        "team_member_removed",
        team_id=team.id,
        user_id=user_id,
        role=team.role,
    )
    session.commit()
    return teams.serialize(team, _member_ids(session, team))
