"""Personal notifications: one row per receiver, read, snooze and preferences
(issue #683; ``docs/contexts/platform.md``, Personal notifications).

A producer calls ``notify`` in its own transaction with the company, the
people it targets, the kind and what it is about. ``notify`` writes one
``PersonalNotification`` per person who is still a member of that company
and has not turned the kind off, and, where the deployment has SMTP and the
person asked for it, queues one ``notification_email`` outbox row
(``services/integrations/person_notices.py`` sends it). The row is the
caller's to commit, so a change that rolls back notifies no one.

The rules, decided by the strictest standard that applies to each:

- **Data minimisation and purpose limitation** (GDPR Art. 5(1)(b), (c)): a
  row holds the receiver, the company, the kind, a reference to the subject
  and the integer ids its link needs (``KINDS[kind].context``), never a copy
  of a task's title, a reason or an address. What it points at is read
  again through the routes the receiver may already call, so a person who
  lost access sees nothing more than a kind and a date. Rows are kept for
  ``RETENTION_NOTIFICATIONS_DAYS`` and deleted once the receiver leaves the
  company (``backend/janitor/retention.py``). The email says only that a
  notice of that kind is waiting.
- **Incident communication** (ISO/IEC 27001:2022 A.5.24–A.5.26): a security
  notice (``Kind.security``) is always written to the app; its preference
  can turn the email off, never the app copy, and it can be snoozed for at
  most ``SECURITY_SNOOZE_MAX``.
- **Authorization**: a person reads and changes only their own rows; another
  person's row answers 404 (``NotificationRefused``), as if it did not exist.

The design follows Plane's notification model (``Notification``,
``UserNotificationPreference``, its inbox's snooze; AGPL-3.0-only, design
only: no code taken).
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from typing import Any

from logging_config import setup_logger
from models import CompanyMember, OutboxEvent
from models.notification import NotificationPreference, PersonalNotification
from sqlalchemy import or_
from sqlmodel import Session, select

from services.platform import mail

logger = setup_logger("fabrique-notifications")

EMAIL_EVENT = "notification_email"
SNOOZE_MAX = timedelta(days=30)
SECURITY_SNOOZE_MAX = timedelta(hours=24)
LIST_LIMIT = 200
STATES = ("unread", "snoozed", "all")


@dataclass(frozen=True)
class Kind:
    """A kind of notice: whether it is a security notice (never muted in the
    app), what its subject is, and the integer ids its link may carry."""

    security: bool
    subject_type: str
    context: tuple[str, ...] = ()
    # The email's default once the person has SMTP: on for security notices.
    email_default: bool = False


KINDS: dict[str, Kind] = {
    # SSO break-glass: an Owner took a person out of an SSO-managed team, or
    # lifted that exclusion (``sso_team_control``, R27, R28): the other Owners.
    "sso_break_glass_exclusion": Kind(
        True, "sso_team_exclusion", ("team_id", "user_id"), True
    ),
    "sso_break_glass_lifted": Kind(
        True, "sso_team_exclusion", ("team_id", "user_id"), True
    ),
    # A verified SSO domain stopped being verified without an Owner asking
    # (a re-check's definite negative, or the 14-day unconfirmed lapse of
    # docs/design/SSO.md decision 5): the Owners.
    "sso_domain_lapsed": Kind(True, "sso_domain", (), True),
    # A delivery approved by QA waits for a person to accept it (a
    # ``risk_hold`` task event): the members who may accept it (Manager+).
    "review_requested": Kind(False, "task", ("sprint_id",)),
    # A sprint's integration stopped and waits for a person (conflict,
    # failing suite, missing branch, daemon unreachable or refused): Manager+.
    "integration_waiting": Kind(False, "sprint_integration", ("sprint_id",)),
}


class NotificationRefused(ValueError):
    def __init__(self, status: int, code: str):
        super().__init__(code)
        self.status, self.code = status, code


def _now() -> datetime:
    return datetime.now(UTC).replace(tzinfo=None)


def _naive(value: datetime) -> datetime:
    return value.astimezone(UTC).replace(tzinfo=None) if value.tzinfo else value


# --- preferences -------------------------------------------------------------


def preferences(session: Session, user_id: int) -> dict[str, dict[str, Any]]:
    """Each kind's preference for *user_id*, with the defaults filled in."""
    stored = {
        row.kind: row
        for row in session.execute(
            select(NotificationPreference).where(
                NotificationPreference.user_id == user_id
            )
        )
        .scalars()
        .all()
    }
    out: dict[str, dict[str, Any]] = {}
    for name, kind in KINDS.items():
        row = stored.get(name)
        out[name] = {
            "kind": name,
            "security": kind.security,
            # A stored "off" never mutes a security notice in the app.
            "in_app": True if kind.security else (row.in_app if row else True),
            "email": row.email if row else kind.email_default,
            "in_app_locked": kind.security,
        }
    return out


def set_preference(
    session: Session, user_id: int, kind: str, in_app: bool, email: bool
) -> dict[str, Any]:
    """Store *user_id*'s choice for *kind*; commits. A security notice cannot
    leave the app (409 ``security_notice_in_app``); an email is a copy of the
    app's notice, so it needs the app copy (422 ``email_needs_in_app``)."""
    spec = KINDS.get(kind)
    if spec is None:
        raise NotificationRefused(404, "unknown_kind")
    if spec.security and not in_app:
        raise NotificationRefused(409, "security_notice_in_app")
    if email and not in_app:
        raise NotificationRefused(422, "email_needs_in_app")
    row = session.exec(
        select(NotificationPreference).where(
            NotificationPreference.user_id == user_id,
            NotificationPreference.kind == kind,
        )
    ).first() or NotificationPreference(user_id=user_id, kind=kind)
    row.in_app, row.email, row.updated_at = bool(in_app), bool(email), _now()
    session.add(row)
    session.commit()
    return preferences(session, user_id)[kind]


# --- writing ------------------------------------------------------------------


def _clean_context(spec: Kind, context: dict[str, Any] | None) -> dict[str, int]:
    """The allowlisted integer ids of *context*; anything else is dropped."""
    out: dict[str, int] = {}
    for key in spec.context:
        value = (context or {}).get(key)
        if isinstance(value, int) and not isinstance(value, bool):
            out[key] = value
    return out


def _members(session: Session, company_id: int, user_ids: set[int]) -> set[int]:
    if not user_ids:
        return set()
    return set(
        session.execute(
            select(CompanyMember.user_id).where(
                CompanyMember.company_id == company_id,
                CompanyMember.user_id.in_(sorted(user_ids)),
            )
        )
        .scalars()
        .all()
    )


def _already(session: Session, user_id: int, dedupe_key: str) -> bool:
    return (
        session.execute(
            select(PersonalNotification.id).where(
                PersonalNotification.user_id == user_id,
                PersonalNotification.dedupe_key == dedupe_key,
            )
        ).first()
        is not None
    )


def notify(
    session: Session,
    company_id: int,
    user_ids: list[int] | set[int],
    kind: str,
    subject_id: int,
    context: dict[str, Any] | None = None,
    dedupe_key: str | None = None,
) -> list[int]:
    """Write one notice of *kind* per receiver in the caller's transaction;
    the user ids that got one, sorted. Never commits or flushes, so it may be
    called from a ``before_flush`` listener."""
    spec = KINDS.get(kind)
    if spec is None:
        raise ValueError(f"unknown notification kind {kind!r}")
    clean = _clean_context(spec, context)
    receivers = _members(session, company_id, {int(u) for u in user_ids})
    written: list[int] = []
    for user_id in sorted(receivers):
        choice = preferences(session, user_id)[kind]
        if not choice["in_app"]:
            continue
        if dedupe_key and _already(session, user_id, dedupe_key):
            continue
        session.add(
            PersonalNotification(
                user_id=user_id,
                company_id=company_id,
                kind=kind,
                subject_type=spec.subject_type,
                subject_id=int(subject_id),
                context=clean,
                dedupe_key=dedupe_key,
                created_at=_now(),
            )
        )
        if choice["email"] and mail.enabled():
            session.add(
                OutboxEvent(
                    aggregate_type="person",
                    aggregate_id=str(user_id),
                    event_type=EMAIL_EVENT,
                    # Who and which kind: the address is read when it is sent.
                    payload={
                        "user_id": user_id,
                        "company_id": company_id,
                        "kind": kind,
                    },
                )
            )
        written.append(user_id)
    return written


# --- reading ------------------------------------------------------------------


def _member_companies(session: Session, user_id: int) -> list[int]:
    return list(
        session.exec(
            select(CompanyMember.company_id).where(CompanyMember.user_id == user_id)
        ).all()
    )


def _mine(session: Session, user_id: int, company_id: int | None):
    companies = _member_companies(session, user_id)
    if company_id is not None:
        companies = [c for c in companies if c == company_id]
    return select(PersonalNotification).where(
        PersonalNotification.user_id == user_id,
        PersonalNotification.company_id.in_(companies or [-1]),
    )


def _unread_clause(now: datetime):
    return (
        PersonalNotification.read_at.is_(None),
        or_(
            PersonalNotification.snoozed_until.is_(None),
            PersonalNotification.snoozed_until <= now,
        ),
    )


def serialize(row: PersonalNotification, now: datetime | None = None) -> dict:
    spec = KINDS.get(row.kind)
    moment = now or _now()
    return {
        "id": row.id,
        "company_id": row.company_id,
        "kind": row.kind,
        "security": bool(spec and spec.security),
        "subject": {"type": row.subject_type, "id": row.subject_id},
        "context": dict(row.context or {}),
        "created_at": row.created_at.isoformat() if row.created_at else None,
        "read_at": row.read_at.isoformat() if row.read_at else None,
        "snoozed_until": (
            row.snoozed_until.isoformat()
            if row.snoozed_until and row.snoozed_until > moment
            else None
        ),
    }


def unread_count(session: Session, user_id: int, company_id: int | None = None) -> int:
    now = _now()
    return len(
        session.exec(
            _mine(session, user_id, company_id).where(*_unread_clause(now))
        ).all()
    )


def list_mine(
    session: Session,
    user_id: int,
    company_id: int | None = None,
    state: str = "unread",
    limit: int = 50,
) -> dict[str, Any]:
    """*user_id*'s own notices in the companies they are still a member of,
    newest first: ``unread`` (not read, not snoozed), ``snoozed`` or ``all``."""
    if state not in STATES:
        raise NotificationRefused(422, "state: unread, snoozed or all")
    now = _now()
    query = _mine(session, user_id, company_id)
    if state == "unread":
        query = query.where(*_unread_clause(now))
    elif state == "snoozed":
        query = query.where(
            PersonalNotification.read_at.is_(None),
            PersonalNotification.snoozed_until > now,
        )
    rows = session.exec(
        query.order_by(
            PersonalNotification.created_at.desc(), PersonalNotification.id.desc()
        ).limit(max(1, min(int(limit), LIST_LIMIT)))
    ).all()
    return {
        "notifications": [serialize(row, now) for row in rows],
        "unread": unread_count(session, user_id, company_id),
        "kinds": sorted(KINDS),
    }


def _own(session: Session, user_id: int, notification_id: int) -> PersonalNotification:
    row = session.get(PersonalNotification, notification_id)
    if (
        row is None
        or row.user_id != user_id
        or row.company_id not in _member_companies(session, user_id)
    ):
        # Another person's notice, or one of a company left, does not exist here.
        raise NotificationRefused(404, "notification_not_found")
    return row


def mark_read(session: Session, user_id: int, notification_id: int, read: bool = True):
    row = _own(session, user_id, notification_id)
    row.read_at = (row.read_at or _now()) if read else None
    if read:
        row.snoozed_until = None
    session.add(row)
    session.commit()
    return serialize(row)


def mark_all_read(session: Session, user_id: int, company_id: int | None) -> int:
    """Mark every unread notice of *user_id* read (snoozed ones included); how many."""
    now = _now()
    rows = session.exec(
        _mine(session, user_id, company_id).where(
            PersonalNotification.read_at.is_(None)
        )
    ).all()
    for row in rows:
        row.read_at, row.snoozed_until = now, None
        session.add(row)
    session.commit()
    return len(rows)


def snooze(
    session: Session, user_id: int, notification_id: int, until: datetime | None
) -> dict:
    """Hide a notice until *until* (``None`` brings it back now). At most
    ``SNOOZE_MAX`` ahead, ``SECURITY_SNOOZE_MAX`` for a security notice."""
    row = _own(session, user_id, notification_id)
    if until is None:
        row.snoozed_until = None
    else:
        moment, now = _naive(until), _now()
        spec = KINDS.get(row.kind)
        limit = SECURITY_SNOOZE_MAX if spec and spec.security else SNOOZE_MAX
        if moment <= now:
            raise NotificationRefused(422, "snooze_in_the_past")
        if moment - now > limit + timedelta(minutes=1):
            raise NotificationRefused(
                422,
                "security_snooze_too_long"
                if limit == SECURITY_SNOOZE_MAX
                else "snooze_too_long",
            )
        if row.read_at is not None:
            raise NotificationRefused(409, "already_read")
        row.snoozed_until = moment
    session.add(row)
    session.commit()
    return serialize(row)


# --- producers' helpers --------------------------------------------------------


def owners(session: Session, company_id: int, besides: int | None = None) -> list[int]:
    """The company's Owners (their own role: a team never makes an Owner)."""
    query = select(CompanyMember.user_id).where(
        CompanyMember.company_id == company_id, CompanyMember.human_role == "Owner"
    )
    if besides is not None:
        query = query.where(CompanyMember.user_id != besides)
    return sorted(session.execute(query).scalars().all())
