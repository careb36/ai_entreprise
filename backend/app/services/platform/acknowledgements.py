"""A company's recorded acknowledgements of a weaker guarantee (issue #766).

Two settings leave a delivery's guarantees weaker than the default, and the
deployment may have either on without anyone deciding it for this company:

- ``tests_off``: ``DELIVERY_TESTS_ENABLED`` is off, so no test run is ever
  observed and no code delivery can be ``verified``
  (``services/evidence/required_checks.py``);
- ``host_sandbox``: the company's sandbox is ``host``, so agent-written code
  runs on the daemon's computer with no isolation.

``required`` says which a configuration needs; ``active`` which an Owner has
recorded and not withdrawn, and that have not ended. A sprint whose company
lacks one does not start (``services/loop/preconditions.py``). A record has
a reason, the account, and an end, at most ``MAX_DAYS`` away: a decision
taken once is not a decision forever. ``record`` and ``withdraw`` are
audited; nothing here deletes a row.

``docs/design/target/01-verification.md`` and ``02-sandbox.md``.
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from typing import Any

from models import CompanyAcknowledgement
from sqlmodel import Session, select

from services.platform.agent_service import AgentService

TESTS_OFF = "tests_off"
HOST_SANDBOX = "host_sandbox"
KINDS = (TESTS_OFF, HOST_SANDBOX)
DEFAULT_DAYS = 30
MAX_DAYS = 90
MIN_REASON = 10
RECORDED = "acknowledgement_recorded"
WITHDRAWN = "acknowledgement_withdrawn"

WHAT = {
    TESTS_OFF: (
        "The deployment does not run the delivery's tests "
        "(DELIVERY_TESTS_ENABLED is off): no code delivery can be verified."
    ),
    HOST_SANDBOX: (
        "Agent-written code runs on the daemon's computer without isolation "
        "(sandbox mode host)."
    ),
}


class Refused(ValueError):
    """The request cannot be recorded; ``status`` is the HTTP answer."""

    def __init__(self, status: int, detail: str):
        super().__init__(detail)
        self.status = status
        self.detail = detail


def _now() -> datetime:
    return datetime.now(UTC).replace(tzinfo=None)


def required(tests_enabled: bool, sandbox_mode: str) -> list[str]:
    """The acknowledgements a configuration needs, in a fixed order."""
    kinds = []
    if not tests_enabled:
        kinds.append(TESTS_OFF)
    if str(sandbox_mode or "").strip().lower() == "host":
        kinds.append(HOST_SANDBOX)
    return kinds


def _in_force(row: CompanyAcknowledgement, now: datetime) -> bool:
    return row.withdrawn_at is None and row.expires_at > now


def active(
    session: Session, company_id: int, now: datetime | None = None
) -> dict[str, CompanyAcknowledgement]:
    """Each kind's latest acknowledgement in force for the company."""
    now = now or _now()
    found: dict[str, CompanyAcknowledgement] = {}
    for row in session.exec(
        select(CompanyAcknowledgement)
        .where(CompanyAcknowledgement.company_id == company_id)
        .order_by(CompanyAcknowledgement.id)
    ).all():
        if _in_force(row, now):
            found[row.kind] = row
    return found


def missing(
    session: Session,
    company_id: int,
    kinds: list[str],
    now: datetime | None = None,
) -> list[dict[str, str]]:
    """Each of *kinds* the company has not acknowledged, with what it means."""
    held = active(session, company_id, now)
    return [{"kind": kind, "means": WHAT[kind]} for kind in kinds if kind not in held]


def payload(row: CompanyAcknowledgement, now: datetime | None = None) -> dict[str, Any]:
    now = now or _now()
    return {
        "id": row.id,
        "company_id": row.company_id,
        "kind": row.kind,
        "means": WHAT.get(row.kind, ""),
        "reason": row.reason,
        "recorded_by": row.recorded_by,
        "recorded_at": row.recorded_at.isoformat(),
        "expires_at": row.expires_at.isoformat(),
        "withdrawn_by": row.withdrawn_by,
        "withdrawn_at": row.withdrawn_at.isoformat() if row.withdrawn_at else None,
        "in_force": _in_force(row, now),
    }


def _audit(
    session: Session, row: CompanyAcknowledgement, action: str, by: str, details: dict
) -> None:
    AgentService().audit_interaction(
        session=session,
        company_id=row.company_id,
        interaction_id=f"company:{row.company_id}:acknowledgement:{row.id}",
        agent_id=f"human:{by}",
        action=action,
        details={"company_id": str(row.company_id), **details},
        result={"kind": row.kind, "expires_at": row.expires_at.isoformat()},
        decision_signature=None,
        cost_tokens=0,
        commit=False,
    )


def record(
    session: Session,
    company_id: int,
    kind: str,
    reason: str,
    *,
    by: str,
    user_id: int | None,
    days: int = DEFAULT_DAYS,
    now: datetime | None = None,
) -> CompanyAcknowledgement:
    """Record an Owner's acknowledgement (the router checks the role); commits."""
    if kind not in KINDS:
        raise Refused(422, f"not an acknowledgement kind: {kind}")
    reason = (reason or "").strip()
    if len(reason) < MIN_REASON:
        raise Refused(422, f"say why, in at least {MIN_REASON} characters")
    if not 1 <= int(days) <= MAX_DAYS:
        raise Refused(422, f"an acknowledgement lasts 1 to {MAX_DAYS} days")
    now = now or _now()
    row = CompanyAcknowledgement(
        company_id=company_id,
        kind=kind,
        reason=reason,
        recorded_by=by,
        recorded_by_user_id=user_id,
        recorded_at=now,
        expires_at=now + timedelta(days=int(days)),
    )
    session.add(row)
    session.flush()
    _audit(session, row, RECORDED, by, {"user_id": user_id, "reason": reason})
    session.commit()
    session.refresh(row)
    return row


def withdraw(
    session: Session,
    company_id: int,
    acknowledgement_id: int,
    *,
    by: str,
    user_id: int | None,
    now: datetime | None = None,
) -> CompanyAcknowledgement:
    """Withdraw one; the row stays, with who withdrew it and when. Commits."""
    row = session.get(CompanyAcknowledgement, acknowledgement_id)
    if row is None or row.company_id != company_id:
        raise Refused(404, "Acknowledgement not found")
    if row.withdrawn_at is not None:
        return row
    row.withdrawn_by = by
    row.withdrawn_at = now or _now()
    session.add(row)
    _audit(session, row, WITHDRAWN, by, {"user_id": user_id})
    session.commit()
    session.refresh(row)
    return row


def history(session: Session, company_id: int) -> list[CompanyAcknowledgement]:
    return list(
        session.exec(
            select(CompanyAcknowledgement)
            .where(CompanyAcknowledgement.company_id == company_id)
            .order_by(CompanyAcknowledgement.id.desc())  # type: ignore[union-attr]
        ).all()
    )
