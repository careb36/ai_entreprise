"""Applying a connection's group mapping at an SSO sign-in (issue #703;
``docs/archive/design/SSO_GROUP_MAPPING.md``, R1–R14).

``apply_sign_in`` runs inside ``sso_flow.finish_login``, after the identity is
resolved and before the ``sso_sign_in`` audit row commits the sign-in: the
memberships, their periods, the observation and the ``sso_group_sync`` audit
row are committed together with it, or rolled back with it (R13). It reads the
groups only from the ID token ``oidc.validate_id_token`` has just validated and
makes no network request (R2). Under the advisory lock ``sso-groups`` of the
company and person, two sign-ins never interleave (R10).

The observation table is append-only: an update or a delete is refused
before the database is touched, as the membership history's is (#711).
"""

from __future__ import annotations

from datetime import UTC, datetime

from models import (
    CompanyMember,
    SsoConnection,
    SsoGroupMapping,
    SsoGroupObservation,
    SsoManualException,
    SsoTeamExclusion,
    Team,
    TeamMember,
    User,
)
from sqlalchemy import event
from sqlmodel import Session, select

from services.platform import locks, teams
from services.platform import sso_groups as rules
from services.platform.agent_service import AgentService


class ObservationRefused(RuntimeError):
    """An update or delete of a group observation: they are append-only."""


@event.listens_for(SsoGroupObservation, "before_update")
def _refuse_updates(_mapper, _connection, _row) -> None:
    raise ObservationRefused("a group observation is never edited")


@event.listens_for(SsoGroupObservation, "before_delete")
def _refuse_deletes(_mapper, _connection, _row) -> None:
    raise ObservationRefused("a group observation is never deleted")


def actor(connection_id: int) -> str:
    """Who the mapping's changes are written by, in periods and audit rows."""
    return f"sso-mapping:{connection_id}"


def _now() -> datetime:
    return datetime.now(UTC).replace(tzinfo=None)


def _iat(claims: dict) -> datetime | None:
    value = claims.get("iat")
    if isinstance(value, bool) or not isinstance(value, int | float):
        return None
    return datetime.fromtimestamp(value, UTC).replace(tzinfo=None)


def mappings_of(session: Session, connection_id: int) -> list[SsoGroupMapping]:
    return list(
        session.exec(
            select(SsoGroupMapping)
            .where(SsoGroupMapping.connection_id == connection_id)
            .order_by(SsoGroupMapping.team_id, SsoGroupMapping.group_value)
        ).all()
    )


def team_infos(session: Session, team_ids) -> dict[int, rules.TeamInfo]:
    """The teams by id, whatever their company: ``evaluate`` checks it again."""
    ids = sorted(set(team_ids))
    if not ids:
        return {}
    return {
        t.id: rules.TeamInfo(t.id, t.company_id, t.name, t.role, t.membership_source)
        for t in session.exec(select(Team).where(Team.id.in_(ids))).all()
    }


def in_force(session: Session, model, company_id: int, user_id: int) -> set[int]:
    """The teams where *user_id* has an exclusion or a manual exception in force."""
    ended = model.lifted_at if model is SsoTeamExclusion else model.ended_at
    return set(
        session.exec(
            select(model.team_id).where(
                model.company_id == company_id,
                model.user_id == user_id,
                ended.is_(None),
            )
        ).all()
    )


def last_applied(
    session: Session, connection_id: int, user_id: int
) -> SsoGroupObservation | None:
    """The person's latest applied sign-in observation on the connection."""
    return session.exec(
        select(SsoGroupObservation)
        .where(
            SsoGroupObservation.connection_id == connection_id,
            SsoGroupObservation.user_id == user_id,
            SsoGroupObservation.source == "sign_in",
            SsoGroupObservation.applied == True,
        )
        .order_by(SsoGroupObservation.token_iat.desc(), SsoGroupObservation.id.desc())
    ).first()


def apply_sign_in(
    session: Session, conn: SsoConnection, user: User, claims: dict
) -> SsoGroupObservation | None:
    """Evaluate the mapping for *user*'s sign-in on *conn* (R1); the
    observation written, ``None`` when the mapping is off (R22)."""
    if not conn.groups_enabled:
        return None
    company_id = conn.company_id
    locks.in_transaction(session, locks.SSO_GROUPS, f"{company_id}:{user.id}")
    reading = rules.read_claim(
        claims,
        conn.groups_claim,
        lower=rules.compares_lower(conn.preset, conn.groups_claim),
    )
    pairs = mappings_of(session, conn.id)
    infos = team_infos(session, [p.team_id for p in pairs])
    member = (
        session.exec(
            select(CompanyMember.id).where(
                CompanyMember.company_id == company_id,
                CompanyMember.user_id == user.id,
            )
        ).first()
        is not None
    )
    current = set(
        session.exec(
            select(TeamMember.team_id).where(
                TeamMember.company_id == company_id, TeamMember.user_id == user.id
            )
        ).all()
    )
    token_iat = _iat(claims)
    previous = last_applied(session, conn.id, user.id)
    outcome = rules.evaluate(
        reading,
        company_id=company_id,
        cap=conn.group_role_cap,
        mappings=[(p.group_value, p.team_id) for p in pairs],
        teams=infos,
        current=current,
        excluded=in_force(session, SsoTeamExclusion, company_id, user.id),
        exceptions=in_force(session, SsoManualException, company_id, user.id),
        is_member=member,
        is_stale=rules.stale(token_iat, previous.token_iat if previous else None),
    )
    observation = SsoGroupObservation(
        company_id=company_id,
        connection_id=conn.id,
        user_id=user.id,
        source="sign_in",
        observed_at=_now(),
        token_iat=token_iat,
        claim_name=conn.groups_claim,
        state=reading.state,
        value_count=reading.count,
        values_sha256=reading.sha256,
        matched_values=outcome.matched,
        overage_host=reading.overage_host,
        applied=outcome.applied,
        skipped=outcome.skipped,
    )
    session.add(observation)
    session.flush()
    by = actor(conn.id)
    changes = []
    for team_id in outcome.add:
        team = session.get(Team, team_id)
        session.add(
            TeamMember(
                company_id=company_id, team_id=team_id, user_id=user.id, added_by=by
            )
        )
        teams.open_membership(
            session, team, user.id, by=by, observation_id=observation.id
        )
        changes.append(_change(infos[team_id], "added", outcome.because[team_id]))
    for team_id in outcome.remove:
        row = session.exec(
            select(TeamMember).where(
                TeamMember.team_id == team_id, TeamMember.user_id == user.id
            )
        ).first()
        if row is not None:
            session.delete(row)
        teams.close_memberships(
            session,
            company_id,
            team_id=team_id,
            user_id=user.id,
            by=by,
            observation_id=observation.id,
        )
        because = "claim_empty" if reading.state == rules.EMPTY else "not_in_claim"
        changes.append(_change(infos[team_id], "removed", because))
    for skip in outcome.skipped:
        if skip["team_id"] in infos:
            changes.append(_change(infos[skip["team_id"]], "skipped", skip["reason"]))
    if outcome.audited(reading.state):
        _audit(session, conn, user, claims, reading, outcome, observation, changes)
    session.flush()
    return observation


def _change(team: rules.TeamInfo, action: str, because) -> dict:
    return {
        "team_id": team.id,
        "team_name": team.name,
        "team_role": team.role,
        "action": action,
        "because": because,
    }


def _audit(session, conn, user, claims, reading, outcome, observation, changes):
    """The ``sso_group_sync`` row, committed with the sign-in (R13)."""
    AgentService().audit_interaction(
        session=session,
        company_id=conn.company_id,
        interaction_id=f"sso:{conn.id}",
        agent_id=actor(conn.id),
        action="sso_group_sync",
        details={
            "connection_id": conn.id,
            "user_id": user.id,
            "issuer": claims.get("iss"),
            "subject": claims.get("sub"),
            "claim_name": conn.groups_claim,
            "claim_state": reading.state,
            "matched_values": outcome.matched,
            "value_count": reading.count,
            "values_sha256": reading.sha256,
            "overage_host": reading.overage_host,
            "observation_id": observation.id,
        },
        result={
            "applied": outcome.applied,
            "changes": changes,
            "skipped": outcome.skipped,
        },
        decision_signature=None,
        cost_tokens=0,
        commit=False,
    )
