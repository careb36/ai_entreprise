"""What a company's standards bring back to the periodic access review
(issue #700; ``docs/archive/design/COMPANY_STANDARDS.md`` §12 decisions 1 and 8).

The review lists, for an Owner to confirm or end:

- every exception to a standard in force (``standard_exception``), with its
  standard, reason, compensating control and expiry; one the only Owner
  confirmed alone is ``flagged`` until a second Owner confirms it. Removing
  it revokes it.
- the standards version in force when the only Owner activated their own
  import and no second Owner has confirmed it yet (``standards_single_owner``,
  flagged). Confirming it by another Owner sets ``reviewed_in``; removing it
  deactivates the set, leaving the catalogue alone in force.

``access_review.complete`` calls ``apply`` after the review row exists and
before its audit row commits everything together.
"""

from __future__ import annotations

from datetime import UTC, datetime
from typing import Any

from models import CompanyStandardException, CompanyStandardSet, User
from sqlmodel import Session

from services.platform import company_standards_setting as standards
from services.platform import sod
from services.platform import standard_exceptions as exceptions

KINDS = ("standard_exception", "standards_single_owner")


def _iso(value: datetime | None) -> str | None:
    return value.isoformat() if value else None


def listing(session: Session, company_id: int, now: datetime) -> list[dict[str, Any]]:
    out: list[dict[str, Any]] = []
    for row in exceptions.in_force(session, company_id, now):
        out.append(
            {
                "kind": "standard_exception",
                "id": row.id,
                "label": row.standard_id,
                "standard_id": row.standard_id,
                "reason": row.reason,
                "compensating_control": row.compensating_control,
                "expires_at": _iso(row.expires_at),
                "decided_by": sod.person_of_user_id(session, row.decided_by).label,
                "decided_by_user_id": row.decided_by,
                "flagged": row.state == "confirmed_single_owner"
                and row.reviewed_in is None,
                "inactive": False,
            }
        )
    active = standards.in_force(session, company_id)
    if active is not None and active.single_owner and active.reviewed_in is None:
        out.append(
            {
                "kind": "standards_single_owner",
                "id": active.id,
                "label": f"v{active.version}",
                "version": active.version,
                "sha256": active.sha256,
                "decided_by": active.activated_by,
                "decided_by_user_id": active.activated_by_user_id,
                "decided_at": _iso(active.activated_at),
                "reason": active.single_owner_reason,
                "flagged": True,
                "inactive": False,
            }
        )
    return out


def _another(me: sod.Person | None, session: Session, user_id: int | None) -> bool:
    return me is not None and not sod.same_person(
        me, sod.person_of_user_id(session, user_id)
    )


def apply(
    session: Session, company_id: int, by: User, items: list[dict], review_id: int
) -> dict[str, list[int]]:
    """Apply the review's decisions on these items; what each decision did."""
    done: dict[str, list[int]] = {
        "standard_exceptions_revoked": [],
        "standard_exceptions_confirmed": [],
        "standards_confirmed": [],
        "standards_deactivated": [],
    }
    me = sod.actor_of(session, by)
    now = datetime.now(UTC).replace(tzinfo=None)
    for item in items:
        if item.get("kind") not in KINDS:
            continue
        remove = item["decision"] == "remove"
        if item["kind"] == "standard_exception":
            row = session.get(CompanyStandardException, item["id"])
            if (
                row is None
                or row.company_id != company_id
                or not exceptions.is_in_force(row, now)
            ):
                continue
            if remove:
                row.state, row.revoked_by, row.revoked_at = "revoked", by.id, now
                done["standard_exceptions_revoked"].append(row.id)
            elif row.state == "confirmed_single_owner" and _another(
                me, session, row.decided_by
            ):
                row.reviewed_in = review_id
                done["standard_exceptions_confirmed"].append(row.id)
            session.add(row)
        else:
            row = session.get(CompanyStandardSet, item["id"])
            if row is None or row.company_id != company_id or row.status != "active":
                continue
            if remove:
                row.status, row.ended_at, row.ended_by = "superseded", now, by.email
                done["standards_deactivated"].append(row.id)
            elif _another(me, session, row.activated_by_user_id):
                # A second Owner confirms it; the Owner who activated it alone
                # leaves it flagged.
                row.reviewed_in = review_id
                done["standards_confirmed"].append(row.id)
            session.add(row)
    session.flush()
    return done
