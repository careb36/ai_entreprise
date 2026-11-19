"""What the SSO group mapping brings back to the periodic access review
(issue #703; ``docs/design/SSO_GROUP_MAPPING.md``, R25, R26, R29).

The review lists, for an Owner to confirm or end:

- every break-glass exclusion in force, with its creator, reason and age
  (``exclusion``; removing it lifts it);
- every manual exception in force (``manual_exception``; removing it ends it
  and takes the person out of the team);
- every Manager pair a single Owner confirmed alone that no second Owner has
  confirmed yet (``manager_pair``, flagged; confirming it by another Owner
  sets ``reviewed_in``, removing it removes the pair and what it alone
  justified).

``access_review.complete`` calls ``apply`` after the review row exists and
before its audit row commits everything together.
"""

from __future__ import annotations

from datetime import UTC, datetime
from typing import Any

from models import (
    SsoConnection,
    SsoGroupMapping,
    SsoManagerApproval,
    SsoManualException,
    SsoTeamExclusion,
    Team,
    User,
)
from sqlmodel import Session, select

from services.platform import sod, sso_group_config, sso_group_sync
from services.platform import sso_team_control as control

KINDS = ("exclusion", "manual_exception", "manager_pair")
LIFT_REASON = "Ended in the periodic access review"


def _iso(value: datetime | None) -> str | None:
    return value.isoformat() if value else None


def _teams(session: Session, company_id: int) -> dict[int, Team]:
    return {
        t.id: t
        for t in session.exec(select(Team).where(Team.company_id == company_id)).all()
    }


def _person(session: Session, user_id: int | None, cache: dict) -> str:
    if user_id is None:
        return ""
    if user_id not in cache:
        user = session.get(User, user_id)
        cache[user_id] = (user.full_name or user.email) if user else str(user_id)
    return cache[user_id]


def _age(now: datetime, since: datetime | None) -> int | None:
    return (now - since).days if since else None


def listing(session: Session, company_id: int, now: datetime) -> list[dict[str, Any]]:
    """The exclusions, manual exceptions and single-Owner pairs to review."""
    teams, people = _teams(session, company_id), {}
    out: list[dict[str, Any]] = []
    for row in session.exec(
        select(SsoTeamExclusion)
        .where(
            SsoTeamExclusion.company_id == company_id,
            SsoTeamExclusion.lifted_at.is_(None),
        )
        .order_by(SsoTeamExclusion.created_at)
    ).all():
        team = teams.get(row.team_id)
        out.append(
            {
                "kind": "exclusion",
                "id": row.id,
                "label": _person(session, row.user_id, people),
                "user_id": row.user_id,
                "team_id": row.team_id,
                "team_name": team.name if team else None,
                "created_by": _person(session, row.created_by, people),
                "created_at": _iso(row.created_at),
                "age_days": _age(now, row.created_at),
                "reason": row.reason,
                "inactive": False,
            }
        )
    for row in session.exec(
        select(SsoManualException)
        .where(
            SsoManualException.company_id == company_id,
            SsoManualException.ended_at.is_(None),
        )
        .order_by(SsoManualException.created_at)
    ).all():
        team = teams.get(row.team_id)
        out.append(
            {
                "kind": "manual_exception",
                "id": row.id,
                "label": _person(session, row.user_id, people),
                "user_id": row.user_id,
                "team_id": row.team_id,
                "team_name": team.name if team else None,
                "created_by": _person(session, row.created_by, people),
                "created_at": _iso(row.created_at),
                "age_days": _age(now, row.created_at),
                "inactive": False,
            }
        )
    for approval, pair in session.exec(
        select(SsoManagerApproval, SsoGroupMapping)
        .join(SsoGroupMapping, SsoGroupMapping.approval_id == SsoManagerApproval.id)
        .where(
            SsoManagerApproval.company_id == company_id,
            SsoManagerApproval.state == "confirmed_single_owner",
            SsoManagerApproval.reviewed_in.is_(None),
        )
        .order_by(SsoManagerApproval.decided_at)
    ).all():
        team = teams.get(pair.team_id)
        out.append(
            {
                "kind": "manager_pair",
                "id": approval.id,
                "label": approval.group_value,
                "group_value": approval.group_value,
                "team_id": pair.team_id,
                "team_name": team.name if team else None,
                "decided_by": _person(session, approval.decided_by, people),
                "decided_by_user_id": approval.decided_by,
                "decided_at": _iso(approval.decided_at),
                "reason": approval.reason,
                "flagged": True,
                "inactive": False,
            }
        )
    return out


def _remove_pair(
    session: Session, approval: SsoManagerApproval, by: User
) -> list[dict[str, int]]:
    conn = session.get(SsoConnection, approval.connection_id)
    pair = session.exec(
        select(SsoGroupMapping).where(SsoGroupMapping.approval_id == approval.id)
    ).first()
    if conn is None or pair is None:
        return []
    still: dict[int, set[str]] = {}
    for other in sso_group_sync.mappings_of(session, conn.id):
        if other.id != pair.id:
            still.setdefault(other.team_id, set()).add(other.group_value)
    removed = sso_group_config.remove_pairs(session, conn, [pair], still)
    sso_group_config.audit(
        session,
        conn,
        by,
        "sso_group_mapping_updated",
        changed=[],
        pairs_added=[],
        pairs_removed=[{"group_value": pair.group_value, "team_id": pair.team_id}],
        proposals=[],
        memberships_removed=removed,
        in_access_review=True,
    )
    return removed


def apply(
    session: Session, company_id: int, by: User, items: list[dict], review_id: int
) -> dict[str, list[int]]:
    """Apply the review's decisions on these items; what each decision did."""
    done: dict[str, list[int]] = {
        "exclusions_lifted": [],
        "manual_exceptions_ended": [],
        "manager_pairs_confirmed": [],
        "manager_pairs_removed": [],
    }
    me = sod.actor_of(session, by)
    for item in items:
        if item["kind"] not in KINDS:
            continue
        remove = item["decision"] == "remove"
        team = session.get(Team, item["team_id"])
        if item["kind"] == "exclusion" and remove and team is not None:
            control.lift_exclusion(session, team, by, item["id"], LIFT_REASON)
            done["exclusions_lifted"].append(item["id"])
        elif item["kind"] == "manual_exception" and remove and team is not None:
            row = session.get(SsoManualException, item["id"])
            if row is not None and row.ended_at is None:
                try:
                    control.remove_by_hand(session, team, by, row.user_id)
                except control.ControlRefused:
                    row.ended_by = by.id
                    row.ended_at = datetime.now(UTC).replace(tzinfo=None)
                    session.add(row)
                done["manual_exceptions_ended"].append(item["id"])
        elif item["kind"] == "manager_pair":
            approval = session.get(SsoManagerApproval, item["id"])
            if approval is None or approval.company_id != company_id:
                continue
            if remove:
                _remove_pair(session, approval, by)
                done["manager_pairs_removed"].append(approval.id)
            elif me is not None and not sod.same_person(
                me, sod.person_of_user_id(session, approval.decided_by)
            ):
                # A second Owner confirms it; the Owner who confirmed it alone
                # leaves it flagged.
                approval.reviewed_in = review_id
                session.add(approval)
                done["manager_pairs_confirmed"].append(approval.id)
    session.flush()
    return done
