"""A connection's group mapping, written by an Owner (issue #703;
``docs/archive/design/SSO_GROUP_MAPPING.md``, R15–R17, R20–R23).

``save`` takes the whole configuration: whether the mapping is on, the claim,
the cap and the list of ``(group value, team)`` pairs. A pair is refused on
save when its team is of another company, Owner-managed or above the cap
(R16); for Google it is refused until the Directory sync of #716 exists
(R17); for Entra's ``groups`` claim its value must be a GUID, stored
lower-cased (R17, G15). A new pair to a Manager team is not created: it
becomes a pending proposal that a second Owner decides
(``sso_group_approvals``, R23). A removed pair takes away, in the same
transaction, the memberships that it alone justified (``remove_pairs``, R20);
a new pair changes nothing until each person's next sign-in (R21); turning
the mapping off changes no membership (R22). One ``sso_group_mapping_updated``
audit row records the change, committed with it.
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from typing import Any

from models import (
    SsoConnection,
    SsoGroupMapping,
    SsoGroupObservation,
    SsoManagerApproval,
    SsoManualException,
    Team,
    TeamMember,
    User,
)
from sqlmodel import Session, select

from services.platform import sso_group_sync, teams
from services.platform import sso_groups as rules
from services.platform.agent_service import AgentService

APPROVAL_TTL = timedelta(days=7)
SETTINGS = ("groups_enabled", "groups_claim", "group_role_cap")


def _now() -> datetime:
    return datetime.now(UTC).replace(tzinfo=None)


def _iso(value: datetime | None) -> str | None:
    return value.isoformat() if value else None


def audit(
    session: Session, conn: SsoConnection, actor: User, action: str, **result
) -> None:
    """An audit row of the mapping's configuration, left for the caller's commit."""
    AgentService().audit_interaction(
        session=session,
        company_id=conn.company_id,
        interaction_id=f"sso-groups:{conn.company_id}",
        agent_id=f"human:{actor.email}",
        action=action,
        details={"company_id": conn.company_id, "connection_id": conn.id},
        result=result,
        decision_signature=None,
        cost_tokens=0,
        commit=False,
    )


def remove_pairs(
    session: Session,
    conn: SsoConnection,
    removed: list[SsoGroupMapping],
    still_mapped: dict[int, set[str]],
) -> list[dict[str, int]]:
    """Delete *removed* and, for each, take out of its team the people whose
    latest applied observation matched its value and no value still mapped
    to that team, unless a manual exception keeps them (R20); the
    memberships removed, as ``{team_id, user_id}``."""
    out: list[dict[str, int]] = []
    by = sso_group_sync.actor(conn.id)
    for pair in removed:
        team = session.get(Team, pair.team_id)
        session.delete(pair)
        if team is None or team.company_id != conn.company_id:
            continue
        members = session.exec(
            select(TeamMember).where(
                TeamMember.team_id == team.id,
                TeamMember.company_id == conn.company_id,
            )
        ).all()
        kept = set(
            session.exec(
                select(SsoManualException.user_id).where(
                    SsoManualException.team_id == team.id,
                    SsoManualException.ended_at.is_(None),
                )
            ).all()
        )
        for row in members:
            if row.user_id in kept:
                continue
            last = sso_group_sync.last_applied(session, conn.id, row.user_id)
            if last is None or not rules.justified_only_by(
                last.matched_values, pair.group_value, still_mapped.get(team.id, set())
            ):
                continue
            session.delete(row)
            teams.close_memberships(
                session, conn.company_id, team_id=team.id, user_id=row.user_id, by=by
            )
            out.append({"team_id": team.id, "user_id": row.user_id})
    session.flush()
    return out


def _clean_pairs(
    session: Session, conn: SsoConnection, claim: str, cap: str, pairs: list[dict]
) -> dict[tuple[str, int], Team]:
    if pairs and conn.preset == "google":
        raise rules.GroupConfigError(
            422,
            "groups_unavailable: Google's ID token carries no groups; they need "
            "the Directory sync, which is not enabled for this connection",
        )
    if len(pairs) > rules.MAX_PAIRS:
        raise rules.GroupConfigError(422, f"mappings: at most {rules.MAX_PAIRS}")
    out: dict[tuple[str, int], Team] = {}
    cap_rank = rules.ROLE_RANK[cap]
    for pair in pairs:
        value = rules.clean_value(pair.get("group_value"), conn.preset, claim)
        team = session.get(Team, int(pair.get("team_id") or 0))
        if team is None or team.company_id != conn.company_id:
            raise rules.GroupConfigError(422, "team_id: not a team of this company")
        if team.membership_source != "sso":
            raise rules.GroupConfigError(
                422,
                f"team_id: {team.name} is managed by an Owner; switch it to SSO first",
            )
        if rules.ROLE_RANK.get(team.role, 99) > cap_rank:
            raise rules.GroupConfigError(
                422, f"team_id: {team.name}'s role {team.role} is above the cap {cap}"
            )
        out[(value, team.id)] = team
    return out


def _pending(session: Session, conn: SsoConnection, value: str, team_id: int):
    return session.exec(
        select(SsoManagerApproval).where(
            SsoManagerApproval.connection_id == conn.id,
            SsoManagerApproval.group_value == value,
            SsoManagerApproval.team_id == team_id,
            SsoManagerApproval.state == "pending",
            SsoManagerApproval.expires_at > _now(),
        )
    ).first()


def propose(
    session: Session, conn: SsoConnection, actor: User, value: str, team: Team
) -> SsoManagerApproval:
    """A pending pair to a Manager team, hashed by the backend (R23, G19)."""
    row = SsoManagerApproval(
        company_id=conn.company_id,
        connection_id=conn.id,
        group_value=value,
        team_id=team.id,
        team_role=team.role,
        group_role_cap=conn.group_role_cap,
        proposal_sha256=rules.proposal_sha256(
            conn.id, value, team.id, team.role, conn.group_role_cap
        ),
        requested_by=actor.id,
        requested_at=_now(),
        expires_at=_now() + APPROVAL_TTL,
    )
    session.add(row)
    session.flush()
    audit(
        session,
        conn,
        actor,
        "sso_manager_pair_proposed",
        approval_id=row.id,
        group_value=value,
        team_id=team.id,
        team_name=team.name,
        team_role=team.role,
        group_role_cap=conn.group_role_cap,
        proposal_sha256=row.proposal_sha256,
        expires_at=_iso(row.expires_at),
    )
    return row


def save(
    session: Session,
    conn: SsoConnection,
    actor: User,
    *,
    enabled: bool,
    claim: str,
    cap: str,
    pairs: list[dict],
) -> dict[str, Any]:
    """Write the configuration (R15–R17, R20–R23); what changed. Raises
    ``GroupConfigError`` before anything is written."""
    claim = rules.clean_claim(claim)
    cap = rules.clean_cap(cap)
    desired = _clean_pairs(session, conn, claim, cap, pairs)
    before = {f: getattr(conn, f) for f in SETTINGS}
    conn.groups_enabled, conn.groups_claim, conn.group_role_cap = enabled, claim, cap
    existing = sso_group_sync.mappings_of(session, conn.id)
    have = {(p.group_value, p.team_id) for p in existing}
    still: dict[int, set[str]] = {}
    for value, team_id in desired:
        still.setdefault(team_id, set()).add(value)
    removed = [p for p in existing if (p.group_value, p.team_id) not in desired]
    removed_pairs = [
        {"group_value": p.group_value, "team_id": p.team_id} for p in removed
    ]
    memberships = remove_pairs(session, conn, removed, still)
    added, proposed = [], []
    for (value, team_id), team in sorted(desired.items()):
        if (value, team_id) in have:
            continue
        if team.role == "Manager":
            if _pending(session, conn, value, team_id) is None:
                proposed.append(propose(session, conn, actor, value, team).id)
            continue
        session.add(
            SsoGroupMapping(
                company_id=conn.company_id,
                connection_id=conn.id,
                group_value=value,
                team_id=team_id,
                created_by=actor.id,
            )
        )
        added.append({"group_value": value, "team_id": team_id})
    conn.updated_at = _now()
    session.add(conn)
    session.flush()
    changed = [f for f in SETTINGS if before[f] != getattr(conn, f)]
    summary = {
        "changed": changed,
        **{f: getattr(conn, f) for f in SETTINGS},
        "pairs_added": added,
        "pairs_removed": removed_pairs,
        "proposals": proposed,
        "memberships_removed": memberships,
    }
    if changed or added or removed_pairs or proposed:
        audit(session, conn, actor, "sso_group_mapping_updated", **summary)
    session.commit()
    return summary


def serialize_approval(row: SsoManagerApproval, now: datetime) -> dict[str, Any]:
    return {
        "id": row.id,
        "group_value": row.group_value,
        "team_id": row.team_id,
        "team_role": row.team_role,
        "group_role_cap": row.group_role_cap,
        "requested_by": row.requested_by,
        "requested_at": _iso(row.requested_at),
        "expires_at": _iso(row.expires_at),
        "state": (
            "expired" if row.state == "pending" and row.expires_at <= now else row.state
        ),
        "decided_by": row.decided_by,
        "decided_at": _iso(row.decided_at),
        "reason": row.reason,
        "reviewed_in": row.reviewed_in,
    }


def view(session: Session, conn: SsoConnection) -> dict[str, Any]:
    """What the Owner's page shows: the settings, the pairs, the proposals, the
    SSO-managed teams, and the last sign-in that hit Entra's overage (G4)."""
    now = _now()
    pairs = sso_group_sync.mappings_of(session, conn.id)
    approvals = session.exec(
        select(SsoManagerApproval)
        .where(SsoManagerApproval.connection_id == conn.id)
        .order_by(SsoManagerApproval.requested_at.desc(), SsoManagerApproval.id.desc())
        .limit(50)
    ).all()
    overage = session.exec(
        select(SsoGroupObservation)
        .where(
            SsoGroupObservation.connection_id == conn.id,
            SsoGroupObservation.state == rules.OVERAGE,
        )
        .order_by(SsoGroupObservation.observed_at.desc(), SsoGroupObservation.id.desc())
    ).first()
    managed = session.exec(
        select(Team)
        .where(Team.company_id == conn.company_id, Team.membership_source == "sso")
        .order_by(Team.name, Team.id)
    ).all()
    return {
        "connection_id": conn.id,
        "preset": conn.preset,
        "groups_enabled": conn.groups_enabled,
        "groups_claim": conn.groups_claim,
        "group_role_cap": conn.group_role_cap,
        "proposed_claim": rules.proposed_claim(conn.preset),
        "compares_lower": rules.compares_lower(conn.preset, conn.groups_claim),
        "groups_available": conn.preset != "google",
        "caps": list(rules.CAPS),
        "max_pairs": rules.MAX_PAIRS,
        "mappings": [
            {
                "id": p.id,
                "group_value": p.group_value,
                "team_id": p.team_id,
                "approval_id": p.approval_id,
                "created_by": p.created_by,
                "created_at": _iso(p.created_at),
            }
            for p in pairs
        ],
        "approvals": [serialize_approval(a, now) for a in approvals],
        "sso_teams": [{"id": t.id, "name": t.name, "role": t.role} for t in managed],
        "last_overage": (
            {"observed_at": _iso(overage.observed_at), "host": overage.overage_host}
            if overage
            else None
        ),
    }
