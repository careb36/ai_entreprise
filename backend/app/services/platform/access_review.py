"""The periodic access review (issue #543; ``docs/design/SEGREGATION_OF_DUTIES.md``).

An Owner reviews, every ``review_interval_days`` (90 by default, quarterly),
who can reach the company: every member with their own, team-derived and
effective role, their SSO and chat links and their last activity, and every
API token that still works. Each item is confirmed or removed; the review is
recorded (``access_reviews``) with what was listed and decided, and audited.

The schedule is a pure function of the records (``schedule``): the next review
is due ``interval`` days after the last one was completed, or after the
company's first audit row when none was; it is ``due`` from then for
``GRACE_DAYS`` and ``missed`` after, until one is completed. A review
completed after its grace is recorded as late (``on_time`` false).

Last activity is observed, never claimed: the person's latest browser session
use (``session_families``) or SSO sign-in (``sso_identities``); a token's
``last_used_at``. An account with none, or older than ``inactive_days``, is
flagged inactive.
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from typing import Any

from models import (
    AccessReview,
    ApiToken,
    AuditLog,
    ChatIdentity,
    CompanyMember,
    SessionFamily,
    SsoIdentity,
    Team,
    TeamMember,
    User,
)
from sqlalchemy import func
from sqlmodel import Session, select

from services.platform import api_tokens, sessions, sod, teams
from services.platform.agent_service import AgentService

GRACE_DAYS = 14
NOTE_MAX = 1000
DECISIONS = ("confirm", "remove")


class ReviewRefused(ValueError):
    def __init__(self, status: int, detail: Any):
        super().__init__(str(detail))
        self.status, self.detail = status, detail


def _now() -> datetime:
    return datetime.now(UTC).replace(tzinfo=None)


def _naive(value: datetime | None) -> datetime | None:
    if value is None:
        return None
    return value.astimezone(UTC).replace(tzinfo=None) if value.tzinfo else value


def _iso(value: datetime | None) -> str | None:
    return value.isoformat() if value else None


def schedule(
    anchor: datetime | None,
    last_completed: datetime | None,
    interval_days: int,
    now: datetime,
    grace_days: int = GRACE_DAYS,
) -> dict[str, Any]:
    """Pure: when the next review is due and whether it is ``scheduled``,
    ``due`` or ``missed``. With no anchor and no review, one is due now."""
    base = _naive(last_completed) or _naive(anchor)
    if base is None:
        return {"due_at": None, "state": "due", "grace_days": grace_days}
    due_at = base + timedelta(days=interval_days)
    if now < due_at:
        state = "scheduled"
    elif now <= due_at + timedelta(days=grace_days):
        state = "due"
    else:
        state = "missed"
    return {"due_at": due_at, "state": state, "grace_days": grace_days}


def inactive(last: datetime | None, threshold_days: int, now: datetime) -> bool:
    """Pure: no observed activity, or none within *threshold_days*."""
    last = _naive(last)
    return last is None or last < now - timedelta(days=threshold_days)


def _latest(*values: datetime | None) -> datetime | None:
    known = [_naive(v) for v in values if v is not None]
    return max(known) if known else None


def _last_review(session: Session, company_id: int) -> AccessReview | None:
    return session.exec(
        select(AccessReview)
        .where(AccessReview.company_id == company_id)
        .order_by(AccessReview.completed_at.desc(), AccessReview.id.desc())
    ).first()


def _anchor(session: Session, company_id: int) -> datetime | None:
    return session.exec(
        select(func.min(AuditLog.created_at)).where(AuditLog.company_id == company_id)
    ).one()


def schedule_of(session: Session, company_id: int, now: datetime | None = None):
    now = now or _now()
    policy = sod.policy_of(session, company_id)
    last = _last_review(session, company_id)
    out = schedule(
        _anchor(session, company_id),
        last.completed_at if last else None,
        policy.review_interval_days,
        now,
    )
    return {
        **out,
        "due_at": _iso(out["due_at"]),
        "interval_days": policy.review_interval_days,
        "inactive_days": policy.inactive_days,
        "last_completed_at": _iso(last.completed_at) if last else None,
        "last_on_time": last.on_time if last else None,
    }


def _members(session: Session, company_id: int, now: datetime, threshold: int):
    rows = session.exec(
        select(CompanyMember, User)
        .join(User, User.id == CompanyMember.user_id)
        .where(CompanyMember.company_id == company_id)
        .order_by(User.email)
    ).all()
    team_rows = session.exec(
        select(TeamMember.user_id, Team.name, Team.role)
        .join(Team, Team.id == TeamMember.team_id)
        .where(TeamMember.company_id == company_id, Team.company_id == company_id)
        .order_by(Team.name)
    ).all()
    out = []
    for member, user in rows:
        own_teams = [
            {"name": name, "role": role}
            for uid, name, role in team_rows
            if uid == user.id
        ]
        used = session.exec(
            select(
                func.max(SessionFamily.last_used_at), func.max(SessionFamily.created_at)
            ).where(SessionFamily.user_id == user.id)
        ).one()
        sso = session.exec(
            select(SsoIdentity).where(SsoIdentity.user_id == user.id)
        ).all()
        chats = session.exec(
            select(ChatIdentity.provider).where(
                ChatIdentity.user_id == user.id, ChatIdentity.company_id == company_id
            )
        ).all()
        last = _latest(used[0], used[1], *(s.last_login_at for s in sso))
        out.append(
            {
                "kind": "member",
                "id": user.id,
                "label": user.full_name or user.email,
                "email": user.email,
                "own_role": member.human_role,
                "teams": own_teams,
                "effective_role": teams.effective_role(
                    member.human_role, [t["role"] for t in own_teams]
                ),
                "sso_linked": bool(sso),
                "chat_links": sorted(set(chats)),
                "last_activity": _iso(last),
                "inactive": inactive(last, threshold, now),
            }
        )
    return out


def _tokens(session: Session, company_id: int, now: datetime, threshold: int):
    out = []
    for token in api_tokens.list_tokens(session, company_id):
        if token.revoked_at is not None or api_tokens.expired(token):
            continue
        last = _latest(token.last_used_at)
        out.append(
            {
                "kind": "token",
                "id": token.id,
                "label": token.name,
                "role": token.role,
                "created_by": token.created_by,
                "created_at": _iso(_naive(token.created_at)),
                "expires_at": _iso(_naive(token.expires_at)),
                "last_activity": _iso(last),
                "inactive": inactive(last or token.created_at, threshold, now),
            }
        )
    return out


def listing(session: Session, company_id: int, now: datetime | None = None):
    """What an Owner reviews now: members and working API tokens."""
    now = now or _now()
    threshold = sod.policy_of(session, company_id).inactive_days
    return {
        "members": _members(session, company_id, now, threshold),
        "tokens": _tokens(session, company_id, now, threshold),
    }


def history(session: Session, company_id: int, limit: int = 20) -> list[dict]:
    rows = session.exec(
        select(AccessReview)
        .where(AccessReview.company_id == company_id)
        .order_by(AccessReview.completed_at.desc(), AccessReview.id.desc())
        .limit(limit)
    ).all()
    return [
        {
            "id": r.id,
            "due_at": _iso(r.due_at),
            "completed_at": _iso(r.completed_at),
            "completed_by": r.completed_by,
            "on_time": r.on_time,
            "summary": r.summary,
            "note": r.note,
        }
        for r in rows
    ]


def _remove_member(
    session: Session, company_id: int, user_id: int, by: str | None = None
) -> str:
    member = session.exec(
        select(CompanyMember).where(
            CompanyMember.company_id == company_id, CompanyMember.user_id == user_id
        )
    ).first()
    if member is None:
        return "gone"
    if member.human_role == "Owner":
        owners = session.exec(
            select(CompanyMember).where(
                CompanyMember.company_id == company_id,
                CompanyMember.human_role == "Owner",
            )
        ).all()
        if len(owners) <= 1:
            raise ReviewRefused(409, "The company's last Owner cannot be removed")
    session.delete(member)
    teams.drop_memberships(session, company_id, user_id, by=by)
    return member.human_role


def complete(
    session: Session,
    company_id: int,
    by: User,
    decisions: list[dict[str, Any]],
    note: str | None,
    now: datetime | None = None,
) -> AccessReview:
    """Record the review: every listed item decided, removals applied, audited."""
    now = now or _now()
    current = listing(session, company_id, now)
    items = current["members"] + current["tokens"]
    chosen = {(d["kind"], int(d["id"])): d["decision"] for d in decisions}
    if any(v not in DECISIONS for v in chosen.values()):
        raise ReviewRefused(422, "Each decision is confirm or remove.")
    missing = [
        f"{i['kind']}:{i['id']}" for i in items if (i["kind"], i["id"]) not in chosen
    ]
    if missing:
        raise ReviewRefused(
            422, {"message": "Decide every listed item.", "missing": missing}
        )
    if chosen.get(("member", by.id)) == "remove":
        raise ReviewRefused(409, "An Owner does not remove themselves in a review.")
    state = schedule_of(session, company_id, now)
    due_at = datetime.fromisoformat(state["due_at"]) if state["due_at"] else None
    removed_users, removed_tokens = [], []
    for item in items:
        item["decision"] = chosen[(item["kind"], item["id"])]
        if item["decision"] != "remove":
            continue
        if item["kind"] == "member":
            _remove_member(session, company_id, item["id"], by=f"human:{by.email}")
            removed_users.append(item["id"])
        else:
            token = session.get(ApiToken, item["id"])
            if token is not None and token.company_id == company_id:
                token.revoked_at = datetime.now(UTC)
                session.add(token)
                removed_tokens.append(item["id"])
    review = AccessReview(
        company_id=company_id,
        due_at=due_at,
        completed_at=now,
        completed_by=by.email,
        completed_by_user_id=by.id,
        on_time=state["state"] != "missed",
        items=items,
        summary={
            "members": len(current["members"]),
            "tokens": len(current["tokens"]),
            "confirmed": sum(1 for i in items if i["decision"] == "confirm"),
            "removed": len(removed_users) + len(removed_tokens),
            "inactive": sum(1 for i in items if i["inactive"]),
        },
        note=" ".join((note or "").split())[:NOTE_MAX] or None,
    )
    session.add(review)
    session.flush()
    audit = AgentService().audit_interaction(
        session=session,
        company_id=company_id,
        interaction_id=f"access-review:{company_id}",
        agent_id=f"human:{by.email}",
        action="access_review_completed",
        details={"company_id": company_id, "review_id": review.id},
        result={
            **review.summary,
            "on_time": review.on_time,
            "due_at": state["due_at"],
            "removed_user_ids": removed_users,
            "revoked_token_ids": removed_tokens,
        },
        decision_signature=None,
        cost_tokens=0,
    )
    review.audit_log_id = audit.id
    session.add(review)
    session.commit()
    if removed_users:
        sessions.revoke_users(session, removed_users, "access_review_removed")
    session.refresh(review)
    return review
