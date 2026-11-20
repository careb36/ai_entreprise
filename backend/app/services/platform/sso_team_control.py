"""An Owner's hand on SSO-managed teams (issue #703;
``docs/design/SSO_GROUP_MAPPING.md``, R16, R18, R19, R26–R28).

- **Conversion** (R19, G22): a team with members becomes SSO-managed only
  after a preview (the members, kept as manual exceptions; the backend's
  hash of the member set and role; 15 minutes; single use) and a ``PATCH``
  that carries it with ``confirm: true`` while the hash is unchanged. Back to
  Owner-managed, the members stay, the manual exceptions end and the
  exclusions are lifted; refused while a pair or a pending proposal targets
  the team.
- **Member routes** (R18): adding by hand to an SSO-managed team is refused
  (``team_managed_by_sso``); removing ends a manual exception, any other
  removal is refused (``use_break_glass``).
- **Break-glass exclusion** (R27, R28, G17, G20): an Owner takes a person out
  of an SSO-managed team at once with a reason; the mapping never adds them
  back while it is in force; every other Owner is notified through the
  outbox in the same transaction; any Owner lifts it with a reason, and
  lifting re-adds nobody. The other Owners also get a personal notification
  each (issue #683, ``notifications``), a security notice they cannot mute.
- **Role** (R16): a mapped team's role is never raised; its pairs go first.

Each change writes an audit row left for the caller's commit, with the
membership change and its history period.
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from typing import Any

from models import (
    CompanyMember,
    OutboxEvent,
    SsoGroupMapping,
    SsoManagerApproval,
    SsoManualException,
    SsoTeamExclusion,
    Team,
    TeamMember,
    TeamMembershipSourcePreview,
    User,
)
from sqlmodel import Session, select

from services.platform import notifications, teams
from services.platform import sso_groups as rules
from services.platform.agent_service import AgentService

PREVIEW_TTL = timedelta(minutes=15)
REASON_RANGE = (10, 500)
NOTICE_SUBJECT = "company.owner_notice"
BREAK_GLASS_ROUTE = "POST /v1/companies/{company_id}/teams/{team_id}/exclusions"
# The personal notification each notice to the Owners becomes (issue #683).
PERSONAL_KINDS = {
    "sso_team_exclusion_created": "sso_break_glass_exclusion",
    "sso_team_exclusion_lifted": "sso_break_glass_lifted",
}


class ControlRefused(ValueError):
    def __init__(self, status: int, detail: str):
        super().__init__(detail)
        self.status, self.detail = status, detail


def _now() -> datetime:
    return datetime.now(UTC).replace(tzinfo=None)


def _iso(value: datetime | None) -> str | None:
    return value.isoformat() if value else None


def owner_by(actor: User) -> str:
    """Who an Owner's change to an SSO-managed team is written by (R26, R27)."""
    return f"owner:{actor.id}"


def clean_reason(reason: str | None) -> str:
    clean = " ".join((reason or "").split())
    if not (REASON_RANGE[0] <= len(clean) <= REASON_RANGE[1]):
        raise ControlRefused(422, "reason: 10 to 500 characters")
    return clean


def _audit(session: Session, team: Team, actor: User, action: str, **result) -> None:
    AgentService().audit_interaction(
        session=session,
        company_id=team.company_id,
        interaction_id=f"teams:{team.company_id}",
        agent_id=f"human:{actor.email}",
        action=action,
        details={"company_id": team.company_id, "team_id": team.id},
        result=result,
        decision_signature=None,
        cost_tokens=0,
        commit=False,
    )


def notify_owners(
    session: Session, company_id: int, actor: User, kind: str, data: dict
) -> list[int]:
    """Queue a notice to every other Owner of the company in the outbox, and
    write each of them a personal notification, in the caller's transaction
    (R27, R28; issue #683); the Owners notified."""
    owners = sorted(
        session.exec(
            select(CompanyMember.user_id).where(
                CompanyMember.company_id == company_id,
                CompanyMember.human_role == "Owner",
                CompanyMember.user_id != actor.id,
            )
        ).all()
    )
    if owners:
        session.add(
            OutboxEvent(
                aggregate_type="company",
                aggregate_id=str(company_id),
                event_type="nats_publish",
                payload={
                    "subject": NOTICE_SUBJECT,
                    "message": {
                        "company_id": company_id,
                        "kind": kind,
                        "recipients": owners,
                        "by_user_id": actor.id,
                        **data,
                    },
                },
            )
        )
        personal = PERSONAL_KINDS.get(kind)
        if personal and data.get("exclusion_id") is not None:
            notifications.notify(
                session,
                company_id,
                owners,
                personal,
                subject_id=data["exclusion_id"],
                context=data,
                dedupe_key=f"{personal}:{data['exclusion_id']}",
            )
    return owners


def _membership(session: Session, team: Team, user_id: int) -> TeamMember | None:
    return session.exec(
        select(TeamMember).where(
            TeamMember.team_id == team.id, TeamMember.user_id == user_id
        )
    ).first()


def member_ids(session: Session, team: Team) -> list[int]:
    return sorted(
        session.exec(
            select(TeamMember.user_id).where(
                TeamMember.team_id == team.id,
                TeamMember.company_id == team.company_id,
            )
        ).all()
    )


def is_mapped(session: Session, team: Team, pending: bool = False) -> bool:
    """Whether a pair (or, with *pending*, a pending proposal) targets *team*."""
    if session.exec(
        select(SsoGroupMapping.id).where(SsoGroupMapping.team_id == team.id)
    ).first():
        return True
    return pending and bool(
        session.exec(
            select(SsoManagerApproval.id).where(
                SsoManagerApproval.team_id == team.id,
                SsoManagerApproval.state == "pending",
                SsoManagerApproval.expires_at > _now(),
            )
        ).first()
    )


def check_role(session: Session, team: Team, role: str | None) -> None:
    """Refuse raising a mapped team's role (R16, ``team_mapped``)."""
    if role is None or rules.ROLE_RANK.get(role, 99) <= rules.ROLE_RANK.get(
        team.role, 0
    ):
        return
    if is_mapped(session, team, pending=True):
        raise ControlRefused(
            409,
            "team_mapped: SSO group pairs target this team; remove them before "
            "raising its role",
        )


def check_add(team: Team) -> None:
    if team.membership_source == "sso":
        raise ControlRefused(
            409,
            "team_managed_by_sso: its members come from the identity provider's groups",
        )


def remove_by_hand(session: Session, team: Team, actor: User, user_id: int) -> None:
    """An Owner removes a member of an SSO-managed team: only a manual
    exception ends this way (R18, R26)."""
    row = _membership(session, team, user_id)
    if row is None:
        raise ControlRefused(404, "Not in this team")
    exception = session.exec(
        select(SsoManualException).where(
            SsoManualException.team_id == team.id,
            SsoManualException.user_id == user_id,
            SsoManualException.ended_at.is_(None),
        )
    ).first()
    if exception is None:
        raise ControlRefused(
            409,
            f"use_break_glass: the identity provider placed this member; take "
            f"them out with {BREAK_GLASS_ROUTE}",
        )
    exception.ended_by, exception.ended_at = actor.id, _now()
    session.add(exception)
    session.delete(row)
    teams.close_memberships(
        session, team.company_id, team_id=team.id, user_id=user_id, by=owner_by(actor)
    )
    _audit(
        session,
        team,
        actor,
        "sso_manual_exception_ended",
        manual_exception_id=exception.id,
        user_id=user_id,
        role=team.role,
    )


def create_exclusion(
    session: Session, team: Team, actor: User, user_id: int, reason: str
) -> SsoTeamExclusion:
    """The break-glass exclusion (R27)."""
    if team.membership_source != "sso":
        raise ControlRefused(
            409, "team_not_sso_managed: remove the member through the team instead"
        )
    clean = clean_reason(reason)
    if teams.own_role(session, team.company_id, user_id) is None:
        raise ControlRefused(404, "Not a member of this company")
    if session.exec(
        select(SsoTeamExclusion.id).where(
            SsoTeamExclusion.team_id == team.id,
            SsoTeamExclusion.user_id == user_id,
            SsoTeamExclusion.lifted_at.is_(None),
        )
    ).first():
        raise ControlRefused(409, "An exclusion is already in force for this person")
    row = SsoTeamExclusion(
        company_id=team.company_id,
        team_id=team.id,
        user_id=user_id,
        created_by=actor.id,
        created_at=_now(),
        reason=clean,
    )
    session.add(row)
    session.flush()
    removed = False
    membership = _membership(session, team, user_id)
    if membership is not None:
        session.delete(membership)
        teams.close_memberships(
            session,
            team.company_id,
            team_id=team.id,
            user_id=user_id,
            by=owner_by(actor),
            exclusion_id=row.id,
        )
        removed = True
    # A manual exception would keep a person the Owner has just taken out.
    _, ended = teams.end_sso_holds(
        session,
        team.company_id,
        user_id,
        actor.id,
        "excluded",
        team_id=team.id,
        lift_exclusions=False,
    )
    notified = notify_owners(
        session,
        team.company_id,
        actor,
        "sso_team_exclusion_created",
        {"team_id": team.id, "user_id": user_id, "exclusion_id": row.id},
    )
    _audit(
        session,
        team,
        actor,
        "sso_team_exclusion_created",
        exclusion_id=row.id,
        user_id=user_id,
        team_name=team.name,
        role=team.role,
        reason=clean,
        removed_from_team=removed,
        manual_exceptions_ended=ended,
        owners_notified=notified,
    )
    return row


def lift_exclusion(
    session: Session, team: Team, actor: User, exclusion_id: int, reason: str
) -> SsoTeamExclusion:
    """Any Owner lifts an exclusion; nobody is added back by it (R28)."""
    row = session.get(SsoTeamExclusion, exclusion_id)
    if row is None or row.team_id != team.id or row.company_id != team.company_id:
        raise ControlRefused(404, "Exclusion not found")
    if row.lifted_at is not None:
        raise ControlRefused(409, "The exclusion was already lifted")
    clean = clean_reason(reason)
    row.lifted_by, row.lifted_at, row.lift_reason = actor.id, _now(), clean
    session.add(row)
    notified = notify_owners(
        session,
        team.company_id,
        actor,
        "sso_team_exclusion_lifted",
        {"team_id": team.id, "user_id": row.user_id, "exclusion_id": row.id},
    )
    _audit(
        session,
        team,
        actor,
        "sso_team_exclusion_lifted",
        exclusion_id=row.id,
        user_id=row.user_id,
        reason=clean,
        owners_notified=notified,
    )
    return row


def create_preview(
    session: Session, team: Team, actor: User
) -> TeamMembershipSourcePreview:
    """What converting *team* to SSO would keep (R19, G22)."""
    if team.membership_source == "sso":
        raise ControlRefused(409, "The team is already managed by SSO")
    members = member_ids(session, team)
    row = TeamMembershipSourcePreview(
        company_id=team.company_id,
        team_id=team.id,
        created_by=actor.id,
        created_at=_now(),
        expires_at=_now() + PREVIEW_TTL,
        members_sha256=rules.members_sha256(members, team.role),
        members=members,
    )
    session.add(row)
    session.commit()
    session.refresh(row)
    return row


def _to_sso(
    session: Session, team: Team, actor: User, preview_id: int | None, confirm: bool
) -> dict[str, Any]:
    members = member_ids(session, team)
    preview = None
    if members:
        preview = (
            session.get(TeamMembershipSourcePreview, preview_id) if preview_id else None
        )
        if (
            not confirm
            or preview is None
            or preview.team_id != team.id
            or preview.company_id != team.company_id
            or preview.used_at is not None
            or preview.expires_at <= _now()
            or preview.members_sha256 != rules.members_sha256(members, team.role)
        ):
            raise ControlRefused(
                409,
                "preview_stale: ask for a new preview of the members kept as "
                "manual exceptions and confirm it",
            )
        preview.used_at = _now()
        session.add(preview)
    created = []
    for user_id in members:
        row = SsoManualException(
            company_id=team.company_id,
            team_id=team.id,
            user_id=user_id,
            created_by=actor.id,
            created_at=_now(),
            preview_id=preview.id if preview else None,
        )
        session.add(row)
        session.flush()
        created.append(row.id)
    return {"preview_id": preview.id if preview else None, "manual_exceptions": created}


def _to_owner(session: Session, team: Team, actor: User) -> dict[str, Any]:
    if is_mapped(session, team, pending=True):
        raise ControlRefused(
            409,
            "team_mapped: SSO group pairs or proposals target this team; remove "
            "them first",
        )
    lifted, ended = [], []
    for user_id in {
        *session.exec(
            select(SsoTeamExclusion.user_id).where(SsoTeamExclusion.team_id == team.id)
        ).all(),
        *session.exec(
            select(SsoManualException.user_id).where(
                SsoManualException.team_id == team.id
            )
        ).all(),
    }:
        some_lifted, some_ended = teams.end_sso_holds(
            session,
            team.company_id,
            user_id,
            actor.id,
            "team switched to owner",
            team_id=team.id,
        )
        lifted += some_lifted
        ended += some_ended
    return {
        "exclusions_lifted": sorted(lifted),
        "manual_exceptions_ended": sorted(ended),
    }


def switch_source(
    session: Session,
    team: Team,
    actor: User,
    source: str,
    preview_id: int | None,
    confirm: bool,
) -> dict[str, Any] | None:
    """Change *team*'s membership source (R19); what the audit row records,
    ``None`` when it is already *source*."""
    if source not in rules.SOURCES:
        raise ControlRefused(422, "membership_source: owner or sso")
    if source == team.membership_source:
        return None
    if source == "sso":
        extra = _to_sso(session, team, actor, preview_id, confirm)
    else:
        extra = _to_owner(session, team, actor)
    extra["membership_source"] = {"from": team.membership_source, "to": source}
    team.membership_source = source
    return extra


def holds(session: Session, company_id: int) -> dict[int, dict[str, list]]:
    """``{team_id: {exclusions, manual_exceptions}}`` in force, for the team page."""
    out: dict[int, dict[str, list]] = {}
    for row in session.exec(
        select(SsoTeamExclusion).where(
            SsoTeamExclusion.company_id == company_id,
            SsoTeamExclusion.lifted_at.is_(None),
        )
    ).all():
        out.setdefault(row.team_id, {"exclusions": [], "manual_exceptions": []})[
            "exclusions"
        ].append(
            {
                "id": row.id,
                "user_id": row.user_id,
                "created_by": row.created_by,
                "created_at": _iso(row.created_at),
                "reason": row.reason,
            }
        )
    for row in session.exec(
        select(SsoManualException).where(
            SsoManualException.company_id == company_id,
            SsoManualException.ended_at.is_(None),
        )
    ).all():
        out.setdefault(row.team_id, {"exclusions": [], "manual_exceptions": []})[
            "manual_exceptions"
        ].append(
            {
                "id": row.id,
                "user_id": row.user_id,
                "created_by": row.created_by,
                "created_at": _iso(row.created_at),
            }
        )
    return out
