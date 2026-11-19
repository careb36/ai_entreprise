"""Reading what the stakeholder register reads into the snapshot (``collect``).

One snapshot key, ``stakeholders``, observations the backend makes itself:

- ``teams``: each team of the company (``Team``) with the role it grants and
  the number of its members who are members of the company (a team never
  makes anyone one, ``services/platform/teams``). A team's name, description
  and author are left out: they are free text an Owner wrote and may name a
  person. ``updated_at`` is left out too, so a rename changes nothing here.
- ``approvals``: each approved specification and architecture version
  (``approved_by`` set, ``services/planning/document_approval``) with who gave
  the approval reduced to a code: the approving account's effective role in
  the company now (``Owner``, ``Manager``, ``Viewer``: its own role raised by
  its teams), ``agent`` (the Manager agent, under the company's policy),
  ``no_sign_in`` (a deployment without sign-in) or ``former`` (an account that
  is no longer a member). The address the record holds is matched here and
  never leaves this module. The pack's own approvals are not read, so
  approving a pack cannot make its register stale.

Every entry carries the sha256 the backend computed over it (``digest``).
Plain data only.
"""

from __future__ import annotations

from datetime import datetime
from typing import Any

from models import Architecture, CompanyMember, Specification, Team, TeamMember
from models.company import User
from sqlalchemy import func
from sqlmodel import Session, select

from services.planning.project_docs.refs import digest
from services.platform import teams as team_roles

LIMIT = 200
# What ``document_approval`` records for an approval that has no person's
# account: the Manager agent's name (``services/management``, which planning
# may not import) and the routes' actor without sign-in.
AGENT_ACTOR = "Manager agent"
NO_SIGN_IN_ACTOR = "local demo"
AGENT, NO_SIGN_IN, FORMER = "agent", "no_sign_in", "former"


def _iso(value: datetime | None) -> str | None:
    return value.isoformat() if value else None


def _hashed(entry: dict[str, Any]) -> dict[str, Any]:
    return {**entry, "sha256": digest(entry)}


def _teams(session: Session, company_id: int) -> list[dict[str, Any]]:
    counts = dict(
        session.exec(
            select(TeamMember.team_id, func.count())
            .join(
                CompanyMember,
                (CompanyMember.user_id == TeamMember.user_id)
                & (CompanyMember.company_id == company_id),
            )
            .where(TeamMember.company_id == company_id)
            .group_by(TeamMember.team_id)
        ).all()
    )
    rows = session.exec(
        select(Team).where(Team.company_id == company_id).order_by(Team.id)
    ).all()
    return [
        _hashed(
            {
                "id": row.id,
                "role": row.role,
                "members": int(counts.get(row.id) or 0),
                "created_at": _iso(row.created_at),
            }
        )
        for row in rows[-LIMIT:]
    ]


def _roles_by_address(
    session: Session, company_id: int, addresses: set[str]
) -> dict[str, str]:
    """The effective company role of each address that is a member now."""
    if not addresses:
        return {}
    users = session.exec(
        select(User.id, User.email).where(func.lower(User.email).in_(addresses))
    ).all()
    own = dict(
        session.exec(
            select(CompanyMember.user_id, CompanyMember.human_role).where(
                CompanyMember.company_id == company_id,
                CompanyMember.user_id.in_([u for u, _ in users]),
            )
        ).all()
    )
    out: dict[str, str] = {}
    for user_id, email in users:
        role = team_roles.effective_role(
            own.get(user_id), team_roles.team_roles(session, company_id, user_id)
        )
        if role:
            out[str(email).lower()] = role
    return out


def _by(actor: str, roles: dict[str, str]) -> str:
    if actor == AGENT_ACTOR:
        return AGENT
    if actor == NO_SIGN_IN_ACTOR:
        return NO_SIGN_IN
    return roles.get(actor.strip().lower(), FORMER)


def _approvals(session: Session, company_id: int) -> list[dict[str, Any]]:
    found: list[tuple[str, Any]] = []
    for kind, model in (
        ("specification", Specification),
        ("architecture", Architecture),
    ):
        rows = session.exec(
            select(model)
            .where(model.company_id == company_id, model.approved_by.is_not(None))
            .order_by(model.id.desc())
            .limit(LIMIT)
        ).all()
        found += [(kind, row) for row in sorted(rows, key=lambda r: r.id)]
    addresses = {
        str(row.approved_by).strip().lower()
        for _, row in found
        if row.approved_by not in (AGENT_ACTOR, NO_SIGN_IN_ACTOR)
    }
    roles = _roles_by_address(session, company_id, addresses)
    return [
        _hashed(
            {
                "kind": kind,
                "id": row.id,
                "version": row.version,
                "approved_at": _iso(row.approved_at),
                "by": _by(str(row.approved_by), roles),
            }
        )
        for kind, row in found
    ]


def stakeholders(session: Session, company_id: int) -> dict[str, Any]:
    """The company's teams and approvals, without a name or an address."""
    return {
        "teams": _teams(session, company_id),
        "approvals": _approvals(session, company_id),
    }
