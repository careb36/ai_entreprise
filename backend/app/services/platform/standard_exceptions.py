"""Time-boxed exceptions to a standard (issue #700;
``docs/design/COMPANY_STANDARDS.md`` §12 decision 1).

A company never relaxes or waives a catalogue standard, and its file can
only add standards. A real need goes through an exception instead, a risk
accepted by named people (ISO/IEC 27001:2022 clause 6.1.3 and A.5.36):

- an Owner **requests** it for one standard (a catalogue ``STD-`` id, or an
  id of the company's set in force) with a reason, a compensating control
  and a duration of at most ``MAX_DAYS`` (90);
- a **second Owner approves** it (``sod.same_person`` keeps the requester
  out: a 403 and a ``sod_refused`` row). Any Owner rejects it; the requester
  withdraws it; a request no one decided in ``PENDING_DAYS`` expires;
- a company with exactly one Owner, and no Owner removed or demoted in the
  last 30 days, may **confirm alone** with a reason; it is then flagged in
  the access review until a second Owner confirms it there;
- it **lapses on its own** at ``expires_at`` (approval time plus its days),
  and any Owner may revoke it earlier. Every step is audited.

``in_force`` answers which exceptions hold now; the Verifier's reading of
them (an excepted finding stays visible, marked ``excepted``) comes with the
company standards check.
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from typing import Any

from models import CompanyStandardException, User
from sqlmodel import Session, select

from services.contracts import standards_catalogue
from services.platform import company_standards_setting as standards
from services.platform import sod
from services.platform import sso_group_approvals as owners

MAX_DAYS = 90
PENDING_DAYS = 7
TEXT_RANGE = (10, 1000)
REASON_RANGE = (10, 500)
IN_FORCE_STATES = ("approved", "confirmed_single_owner")


class ExceptionRefused(ValueError):
    def __init__(self, status: int, detail: str):
        super().__init__(detail)
        self.status, self.detail = status, detail


def _now() -> datetime:
    return datetime.now(UTC).replace(tzinfo=None)


def _iso(value: datetime | None) -> str | None:
    return value.isoformat() if value else None


def _clean(value: str | None, name: str, bounds: tuple[int, int]) -> str:
    text = " ".join((value or "").split())
    if not bounds[0] <= len(text) <= bounds[1]:
        raise ExceptionRefused(422, f"{name}: {bounds[0]} to {bounds[1]} characters")
    return text


def is_in_force(row: CompanyStandardException, now: datetime) -> bool:
    """Pure: approved (or confirmed alone), not revoked, not yet lapsed."""
    return (
        row.state in IN_FORCE_STATES
        and row.revoked_at is None
        and row.expires_at is not None
        and now < row.expires_at
    )


def known_standard(session: Session, company_id: int, standard_id: str) -> bool:
    """A catalogue id, or an id of the company's set in force."""
    if any(s["id"] == standard_id for s in standards_catalogue.STANDARDS):
        return True
    row = standards.in_force(session, company_id)
    return bool(row) and any(
        s.get("id") == standard_id for s in (row.parsed or {}).get("standards") or []
    )


def _audit(session, company_id: int, actor: User, action: str, row, **extra) -> None:
    standards.audit(
        session,
        company_id,
        actor.email,
        action,
        exception_id=row.id,
        standard_id=row.standard_id,
        days=row.days,
        expires_at=_iso(row.expires_at),
        **extra,
    )


def find(
    session: Session, company_id: int, exception_id: int
) -> CompanyStandardException:
    row = session.get(CompanyStandardException, exception_id)
    if row is None or row.company_id != company_id:
        raise ExceptionRefused(404, "Exception not found")
    return row


def request(
    session: Session,
    company_id: int,
    actor: User,
    *,
    standard_id: str,
    reason: str,
    compensating_control: str,
    days: int,
) -> CompanyStandardException:
    if not known_standard(session, company_id, standard_id):
        raise ExceptionRefused(
            422,
            f"standard_id: {standard_id!r} is not a catalogue standard nor one in force",
        )
    if isinstance(days, bool) or not isinstance(days, int) or not 1 <= days <= MAX_DAYS:
        raise ExceptionRefused(422, f"days: 1 to {MAX_DAYS}")
    row = CompanyStandardException(
        company_id=company_id,
        standard_id=standard_id,
        reason=_clean(reason, "reason", TEXT_RANGE),
        compensating_control=_clean(
            compensating_control, "compensating_control", TEXT_RANGE
        ),
        days=days,
        requested_by=actor.id,
    )
    session.add(row)
    session.flush()
    _audit(
        session,
        company_id,
        actor,
        "standard_exception_requested",
        row,
        reason=row.reason,
        compensating_control=row.compensating_control,
    )
    session.commit()
    session.refresh(row)
    return row


def _pending(session: Session, row: CompanyStandardException, now: datetime) -> None:
    if row.state != "pending":
        raise ExceptionRefused(409, f"The exception is {row.state}")
    if row.requested_at + timedelta(days=PENDING_DAYS) <= now:
        row.state = "expired"
        session.add(row)
        session.commit()
        raise ExceptionRefused(409, "The request has expired; request it again")


def _grant(
    row: CompanyStandardException,
    state: str,
    actor: User,
    now: datetime,
    reason: str | None = None,
):
    row.state, row.decided_by, row.decided_at = state, actor.id, now
    row.expires_at = now + timedelta(days=row.days)
    row.decision_reason = reason


def approve(
    session: Session, company_id: int, exception_id: int, actor: User
) -> CompanyStandardException:
    """A second Owner approves; the requester never does."""
    row = find(session, company_id, exception_id)
    now = _now()
    _pending(session, row, now)
    me = sod.actor_of(session, actor) or sod.Person(actor.id, actor.email)
    if sod.same_person(me, sod.person_of_user_id(session, row.requested_by)):
        _audit(
            session,
            company_id,
            actor,
            "sod_refused",
            row,
            refused="approve_standard_exception",
        )
        session.commit()
        raise ExceptionRefused(
            403, "four_eyes: a second Owner approves the exception you requested"
        )
    _grant(row, "approved", actor, now)
    session.add(row)
    _audit(session, company_id, actor, "standard_exception_approved", row)
    session.commit()
    session.refresh(row)
    return row


def confirm_single_owner(
    session: Session, company_id: int, exception_id: int, actor: User, reason: str
) -> CompanyStandardException:
    """The only Owner confirms their own request; flagged in the access review."""
    row = find(session, company_id, exception_id)
    now = _now()
    me = sod.actor_of(session, actor) or sod.Person(actor.id, actor.email)
    if not sod.same_person(me, sod.person_of_user_id(session, row.requested_by)):
        raise ExceptionRefused(
            403, "Only the requester confirms alone; you can approve it"
        )
    if owners.owner_count(session, company_id) != 1:
        raise ExceptionRefused(
            409, "not_single_owner: the company has another Owner, who approves it"
        )
    if owners.recent_owner_change(session, company_id, now):
        raise ExceptionRefused(
            409,
            "recent_owner_change: an Owner was removed or demoted in the last 30 days; another Owner approves it",
        )
    clean = _clean(reason, "reason", REASON_RANGE)
    _pending(session, row, now)
    _grant(row, "confirmed_single_owner", actor, now, clean)
    session.add(row)
    _audit(
        session, company_id, actor, "standard_exception_single_owner", row, reason=clean
    )
    session.commit()
    session.refresh(row)
    return row


def reject(
    session: Session, company_id: int, exception_id: int, actor: User
) -> CompanyStandardException:
    row = find(session, company_id, exception_id)
    _pending(session, row, _now())
    me = sod.actor_of(session, actor) or sod.Person(actor.id, actor.email)
    own = sod.same_person(me, sod.person_of_user_id(session, row.requested_by))
    row.state, row.decided_by, row.decided_at = (
        ("withdrawn" if own else "rejected"),
        actor.id,
        _now(),
    )
    session.add(row)
    _audit(session, company_id, actor, f"standard_exception_{row.state}", row)
    session.commit()
    session.refresh(row)
    return row


def revoke(
    session: Session, company_id: int, exception_id: int, actor: User
) -> CompanyStandardException:
    row = find(session, company_id, exception_id)
    if not is_in_force(row, _now()):
        raise ExceptionRefused(409, "The exception is not in force")
    row.state, row.revoked_by, row.revoked_at = "revoked", actor.id, _now()
    session.add(row)
    _audit(session, company_id, actor, "standard_exception_revoked", row)
    session.commit()
    session.refresh(row)
    return row


def listing(session: Session, company_id: int) -> list[CompanyStandardException]:
    return list(
        session.exec(
            select(CompanyStandardException)
            .where(CompanyStandardException.company_id == company_id)
            .order_by(CompanyStandardException.requested_at.desc())
        ).all()
    )


def in_force(
    session: Session, company_id: int | None, now: datetime | None = None
) -> list[CompanyStandardException]:
    if company_id is None:
        return []
    now = now or _now()
    return [r for r in listing(session, company_id) if is_in_force(r, now)]


def serialize(
    row: CompanyStandardException, now: datetime | None = None
) -> dict[str, Any]:
    now = now or _now()
    return {
        "id": row.id,
        "standard_id": row.standard_id,
        "reason": row.reason,
        "compensating_control": row.compensating_control,
        "days": row.days,
        "state": row.state,
        "requested_by": row.requested_by,
        "requested_at": _iso(row.requested_at),
        "decided_by": row.decided_by,
        "decided_at": _iso(row.decided_at),
        "expires_at": _iso(row.expires_at),
        "in_force": is_in_force(row, now),
        "lapsed": bool(
            row.expires_at and now >= row.expires_at and row.revoked_at is None
        ),
        "flagged": row.state == "confirmed_single_owner" and row.reviewed_in is None,
        "revoked_at": _iso(row.revoked_at),
    }
