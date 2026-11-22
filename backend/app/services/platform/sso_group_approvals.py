"""Four-eyes on a group pair that grants Manager (issue #703;
``docs/archive/design/SSO_GROUP_MAPPING.md``, R23–R25, G19; segregation of duties,
#543).

A pair to a Manager team waits as a pending ``sso_manager_approvals`` row
(``sso_group_config.propose``). ``approve`` creates the pair only when the
approver, the signed-in Owner of the session, is not the proposer (matched as
``sod.same_person`` matches: account id, else email), the proposal has not
expired (7 days) and the ``proposal_sha256`` recomputed now (connection,
value, team, team role, cap) equals the stored one; approving one's own pair
is a 403 and a committed ``sod_refused`` row. Any Owner rejects; the proposer
withdraws.

``confirm_single_owner`` is the explicit exception (R25): honoured only while
the company has exactly one Owner and no Owner was removed or demoted in the
previous 30 days (read from the audit chain), with the team's name typed back
and a reason of 10 to 500 characters; the pair stays flagged in the access
review until a second Owner confirms it there (``reviewed_in``).
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

from models import (
    AccessReview,
    AuditLog,
    CompanyMember,
    SsoConnection,
    SsoGroupMapping,
    SsoManagerApproval,
    Team,
    User,
)
from sqlmodel import Session, func, select

from services.platform import sod, sso_group_sync
from services.platform import sso_groups as rules
from services.platform.sso_group_config import audit

OWNER_CHANGE_WINDOW = timedelta(days=30)
REASON_RANGE = (10, 500)


class ApprovalRefused(ValueError):
    def __init__(self, status: int, detail: str):
        super().__init__(detail)
        self.status, self.detail = status, detail


def _now() -> datetime:
    return datetime.now(UTC).replace(tzinfo=None)


def find(session: Session, conn: SsoConnection, approval_id: int) -> SsoManagerApproval:
    row = session.get(SsoManagerApproval, approval_id)
    if row is None or row.company_id != conn.company_id or row.connection_id != conn.id:
        raise ApprovalRefused(404, "Proposal not found")
    return row


def _decide(row: SsoManagerApproval, state: str, by: int, reason: str | None = None):
    row.state, row.decided_by, row.decided_at = state, by, _now()
    if reason is not None:
        row.reason = reason


def _pending(session: Session, row: SsoManagerApproval) -> None:
    """Refuse a proposal already decided, or one past its expiry (marked)."""
    if row.state != "pending":
        raise ApprovalRefused(409, f"The proposal is {row.state}")
    if row.expires_at <= _now():
        row.state = "expired"
        session.add(row)
        session.commit()
        raise ApprovalRefused(409, "The proposal has expired; propose it again")


def _unchanged(session: Session, conn: SsoConnection, row: SsoManagerApproval) -> Team:
    """The team, if the proposal still describes what would be granted (G19)."""
    team = session.get(Team, row.team_id)
    if (
        team is None
        or team.company_id != conn.company_id
        or team.membership_source != "sso"
        or rules.proposal_sha256(
            conn.id, row.group_value, team.id, team.role, conn.group_role_cap
        )
        != row.proposal_sha256
    ):
        raise ApprovalRefused(
            409,
            "The team, its role or the cap changed since the proposal; "
            "the proposer withdraws it and proposes again",
        )
    if len(sso_group_sync.mappings_of(session, conn.id)) >= rules.MAX_PAIRS:
        raise ApprovalRefused(409, f"mappings: at most {rules.MAX_PAIRS}")
    return team


def _create_pair(session: Session, conn: SsoConnection, row: SsoManagerApproval):
    exists = session.exec(
        select(SsoGroupMapping).where(
            SsoGroupMapping.connection_id == conn.id,
            SsoGroupMapping.group_value == row.group_value,
            SsoGroupMapping.team_id == row.team_id,
        )
    ).first()
    if exists is None:
        session.add(
            SsoGroupMapping(
                company_id=conn.company_id,
                connection_id=conn.id,
                group_value=row.group_value,
                team_id=row.team_id,
                created_by=row.requested_by,
                approval_id=row.id,
            )
        )


def _subject(row: SsoManagerApproval, team: Team | None = None) -> dict:
    return {
        "approval_id": row.id,
        "group_value": row.group_value,
        "team_id": row.team_id,
        "team_name": team.name if team else None,
        "team_role": row.team_role,
        "group_role_cap": row.group_role_cap,
        "proposal_sha256": row.proposal_sha256,
    }


def approve(
    session: Session, conn: SsoConnection, actor: User, approval_id: int
) -> SsoManagerApproval:
    """A second Owner approves a pending pair (R24)."""
    row = find(session, conn, approval_id)
    if row.state != "pending":
        raise ApprovalRefused(409, f"The proposal is {row.state}")
    me = sod.actor_of(session, actor)
    proposer = sod.person_of_user_id(session, row.requested_by)
    if me is None or sod.same_person(me, proposer):
        audit(
            session,
            conn,
            actor,
            "sod_refused",
            action_refused="approve_sso_manager_pair",
            subject=_subject(row),
            actor_user_id=actor.id,
            requested_by=row.requested_by,
            outcome="refused",
            conflict="proposed the pair",
        )
        session.commit()
        raise ApprovalRefused(
            403,
            "Segregation of duties: you proposed this pair, so another Owner "
            "approves it",
        )
    _pending(session, row)
    team = _unchanged(session, conn, row)
    _create_pair(session, conn, row)
    _decide(row, "approved", actor.id)
    session.add(row)
    audit(session, conn, actor, "sso_manager_pair_approved", **_subject(row, team))
    session.commit()
    return row


def reject(
    session: Session,
    conn: SsoConnection,
    actor: User,
    approval_id: int,
    reason: str | None,
) -> SsoManagerApproval:
    """Any Owner rejects a pending pair (R24)."""
    row = find(session, conn, approval_id)
    if row.state != "pending":
        raise ApprovalRefused(409, f"The proposal is {row.state}")
    clean = " ".join((reason or "").split())[: REASON_RANGE[1]] or None
    _decide(row, "rejected", actor.id, clean)
    session.add(row)
    audit(
        session, conn, actor, "sso_manager_pair_rejected", reason=clean, **_subject(row)
    )
    session.commit()
    return row


def withdraw(
    session: Session, conn: SsoConnection, actor: User, approval_id: int
) -> SsoManagerApproval:
    """The proposer withdraws their pending pair (R24)."""
    row = find(session, conn, approval_id)
    if row.state != "pending":
        raise ApprovalRefused(409, f"The proposal is {row.state}")
    me = sod.actor_of(session, actor)
    if me is None or not sod.same_person(
        me, sod.person_of_user_id(session, row.requested_by)
    ):
        raise ApprovalRefused(403, "Only the Owner who proposed it withdraws it")
    _decide(row, "withdrawn", actor.id)
    session.add(row)
    audit(session, conn, actor, "sso_manager_pair_withdrawn", **_subject(row))
    session.commit()
    return row


def owner_count(session: Session, company_id: int) -> int:
    return session.exec(
        select(func.count(CompanyMember.id)).where(
            CompanyMember.company_id == company_id,
            CompanyMember.human_role == "Owner",
        )
    ).one()


def recent_owner_change(session: Session, company_id: int, now: datetime) -> bool:
    """Whether an Owner was removed or demoted in the last 30 days, read from
    the audit chain: a ``member_removed`` of an Owner, a role change from
    Owner, or an access review that removed an Owner (G19)."""
    rows = session.exec(
        select(AuditLog).where(
            AuditLog.company_id == company_id,
            AuditLog.created_at >= now - OWNER_CHANGE_WINDOW,
            AuditLog.action.in_(
                ("member_removed", "member_role_changed", "access_review_completed")
            ),
        )
    ).all()
    for row in rows:
        result = row.result if isinstance(row.result, dict) else {}
        details = row.details if isinstance(row.details, dict) else {}
        if row.action == "member_removed" and result.get("human_role") == "Owner":
            return True
        if row.action == "member_role_changed" and result.get("from") == "Owner":
            return True
        if row.action == "access_review_completed" and result.get("removed_user_ids"):
            review = session.get(AccessReview, details.get("review_id") or 0)
            items = review.items if review and isinstance(review.items, list) else []
            if any(
                i.get("kind") == "member"
                and i.get("decision") == "remove"
                and i.get("own_role") == "Owner"
                for i in items
                if isinstance(i, dict)
            ):
                return True
    return False


def confirm_single_owner(
    session: Session,
    conn: SsoConnection,
    actor: User,
    approval_id: int,
    confirmation: str,
    reason: str,
) -> SsoManagerApproval:
    """The only Owner confirms their own pending pair (R25)."""
    row = find(session, conn, approval_id)
    if row.state != "pending":
        raise ApprovalRefused(409, f"The proposal is {row.state}")
    me = sod.actor_of(session, actor)
    if me is None or not sod.same_person(
        me, sod.person_of_user_id(session, row.requested_by)
    ):
        raise ApprovalRefused(
            403, "Another Owner approves this pair; only its proposer confirms it alone"
        )
    if owner_count(session, conn.company_id) != 1:
        raise ApprovalRefused(
            409, "not_single_owner: the company has another Owner, who approves it"
        )
    now = _now()
    if recent_owner_change(session, conn.company_id, now):
        raise ApprovalRefused(
            409,
            "recent_owner_change: an Owner was removed or demoted in the last "
            "30 days; another Owner approves it",
        )
    clean = " ".join((reason or "").split())
    if not (REASON_RANGE[0] <= len(clean) <= REASON_RANGE[1]):
        raise ApprovalRefused(422, "reason: 10 to 500 characters")
    _pending(session, row)
    team = _unchanged(session, conn, row)
    if confirmation != team.name:
        raise ApprovalRefused(422, "confirmation: type the team's name exactly")
    _create_pair(session, conn, row)
    _decide(row, "confirmed_single_owner", actor.id, clean)
    session.add(row)
    audit(
        session,
        conn,
        actor,
        "sso_manager_pair_single_owner",
        reason=clean,
        flagged_for_access_review=True,
        **_subject(row, team),
    )
    session.commit()
    return row
