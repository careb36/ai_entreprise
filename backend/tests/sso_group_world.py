"""Shared data for the SSO group mapping tests (issue #703;
``docs/design/SSO_GROUP_MAPPING.md``): a company with an SSO connection that
maps groups, SSO-managed teams, pairs and observations, built through the
models as the routes would leave them. No secret is a literal: the fake
provider's key and the signing key are generated per test (``sso_fake_idp``).
"""

from __future__ import annotations

import secrets
from datetime import UTC, datetime

from models import (
    AuditLog,
    SsoConnection,
    SsoGroupMapping,
    SsoGroupObservation,
    Team,
    TeamMember,
    TeamMembershipPeriod,
)
from sqlmodel import select

GROUP = "engineering"
OTHER_GROUP = "design"
UNMAPPED = "payroll-confidential"


def now() -> datetime:
    return datetime.now(UTC).replace(tzinfo=None)


def groups_on(
    session, conn: SsoConnection, cap: str = "Viewer", claim: str = "groups"
) -> SsoConnection:
    conn.groups_enabled, conn.group_role_cap, conn.groups_claim = True, cap, claim
    session.add(conn)
    session.commit()
    return conn


def team(
    session,
    company_id: int,
    role: str = "Viewer",
    source: str = "sso",
    members: tuple[int, ...] = (),
) -> Team:
    row = Team(
        company_id=company_id,
        name=f"team-{secrets.token_hex(3)}",
        role=role,
        membership_source=source,
    )
    session.add(row)
    session.commit()
    for user_id in members:
        session.add(TeamMember(company_id=company_id, team_id=row.id, user_id=user_id))
    session.commit()
    return row


def pair(session, conn: SsoConnection, value: str, team_row: Team) -> SsoGroupMapping:
    row = SsoGroupMapping(
        company_id=conn.company_id,
        connection_id=conn.id,
        group_value=value,
        team_id=team_row.id,
    )
    session.add(row)
    session.commit()
    return row


def observe(
    session, conn: SsoConnection, user_id: int, matched: list[str], applied=True
) -> SsoGroupObservation:
    row = SsoGroupObservation(
        company_id=conn.company_id,
        connection_id=conn.id,
        user_id=user_id,
        token_iat=now(),
        claim_name=conn.groups_claim,
        state="present",
        value_count=len(matched),
        matched_values=matched,
        applied=applied,
    )
    session.add(row)
    session.commit()
    return row


def members_of(session, team_id: int) -> list[int]:
    session.expire_all()
    return sorted(
        session.exec(select(TeamMember.user_id).where(TeamMember.team_id == team_id))
    )


def periods(session, team_id: int, user_id: int) -> list[TeamMembershipPeriod]:
    session.expire_all()
    return list(
        session.exec(
            select(TeamMembershipPeriod)
            .where(
                TeamMembershipPeriod.team_id == team_id,
                TeamMembershipPeriod.user_id == user_id,
            )
            .order_by(TeamMembershipPeriod.id)
        )
    )


def audits(session, company_id: int, action: str) -> list[AuditLog]:
    session.expire_all()
    return list(
        session.exec(
            select(AuditLog)
            .where(AuditLog.company_id == company_id, AuditLog.action == action)
            .order_by(AuditLog.id)
        )
    )


def observations(session, conn_id: int) -> list[SsoGroupObservation]:
    session.expire_all()
    return list(
        session.exec(
            select(SsoGroupObservation)
            .where(SsoGroupObservation.connection_id == conn_id)
            .order_by(SsoGroupObservation.id)
        )
    )


def mappings_url(company_id: int, suffix: str = "") -> str:
    return f"/v1/companies/{company_id}/sso/group-mappings{suffix}"


def teams_url(company_id: int, suffix: str = "") -> str:
    return f"/v1/companies/{company_id}/teams{suffix}"
