"""DNS verification of a company's SSO domains (``docs/design/SSO.md``, T16–T22).

Listing a domain in ``allowed_domains`` is the Owner's claim; it proves
nothing, since any Owner can type any domain. For each listed domain the
backend issues a random token (``sync``); the Owner publishes
``la-fabrique-domain-verification=<token>`` as a TXT record at
``_la-fabrique-verify.<domain>``; ``check`` asks DNS (``dns_client``, the
adapter) and records what it answered as an ``SsoDomainCheck`` row: when,
why, the outcome and the strings seen. The domain is verified only by an
answer holding the token; a definite negative (no such name, no matching
string) unverifies it; a transient failure changes nothing.

Only a domain this company may use (``usable``: verified, or a legacy domain
in its grace period that no other company has verified) lets SSO create or
link an account (``sso_flow.resolve_identity``). A domain verified by one
company is held by it: another cannot list it, and a newer proof by another
company first re-checks the older claim (``trigger="conflict"``), which
releases it only on a definite negative. ``recheck_due`` looks every domain
up again each ``SSO_DOMAIN_RECHECK_HOURS``; ``run_rechecks`` is its loop,
one replica at a time. Each re-check, and each cycle of that loop, is
counted for ``/metrics`` (``sso_metrics``).

A verified domain that stops being verified without an Owner asking (a
re-check or a conflict's definite negative) is a lapse: every Owner of the
company gets a personal security notice (``notify_lapsed``, issue #683).
The 14-day ``lapsed_unconfirmed`` rule of docs/design/SSO.md decision 5 is
not built yet (#684); when it is, it calls ``notify_lapsed`` with that
reason in the same transaction as the status change.
"""

from __future__ import annotations

import asyncio
import hmac
import secrets
from datetime import UTC, datetime, timedelta
from typing import Any

from logging_config import setup_logger
from models import SsoDomain, SsoDomainCheck
from sqlalchemy import or_
from sqlmodel import Session, select

from services.platform import dns_client, locks, notifications, sso_metrics
from services.platform.agent_service import AgentService

logger = setup_logger("fabrique-sso-domains")

RECORD_PREFIX = "_la-fabrique-verify"
VALUE_PREFIX = "la-fabrique-domain-verification="
TOKEN_BYTES = 32
RECHECK_BATCH = 200
RECHECK_WAKE_SECONDS = 900
MAX_DETAIL_CHARS = 200
DEFINITE_NEGATIVES = ("not_found", "no_match")


def _now() -> datetime:
    return datetime.now(UTC).replace(tzinfo=None)


def _settings():
    from config import settings

    return settings


def new_token() -> str:
    return secrets.token_urlsafe(TOKEN_BYTES)


def record_name(domain: str) -> str:
    return f"{RECORD_PREFIX}.{domain}"


def record_value(row: SsoDomain) -> str:
    return f"{VALUE_PREFIX}{row.token}"


def rows(session: Session, company_id: int) -> list[SsoDomain]:
    return list(
        session.exec(
            select(SsoDomain)
            .where(SsoDomain.company_id == company_id)
            .order_by(SsoDomain.domain)
        ).all()
    )


def find(session: Session, company_id: int, domain: str) -> SsoDomain | None:
    return session.exec(
        select(SsoDomain).where(
            SsoDomain.company_id == company_id, SsoDomain.domain == domain
        )
    ).first()


def sync(session: Session, company_id: int, domains: list[str]) -> None:
    """Give each listed domain a record with a fresh token; drop the records
    of domains no longer listed (their checks stay). Not committed."""
    existing = {row.domain: row for row in rows(session, company_id)}
    for domain, row in existing.items():
        if domain not in domains:
            session.delete(row)
    for domain in domains:
        if domain not in existing:
            session.add(
                SsoDomain(company_id=company_id, domain=domain, token=new_token())
            )


def drop_all(session: Session, company_id: int) -> None:
    for row in rows(session, company_id):
        session.delete(row)


def verified_elsewhere(
    session: Session, company_id: int, domain: str
) -> SsoDomain | None:
    """Another company's verified record of *domain*, if any."""
    return session.exec(
        select(SsoDomain).where(
            SsoDomain.domain == domain,
            SsoDomain.company_id != company_id,
            SsoDomain.status == "verified",
        )
    ).first()


def grace_until(row: SsoDomain) -> datetime | None:
    """When a legacy domain's grace ends, or ``None`` when it has none."""
    settings = _settings()
    if not row.legacy or not settings.SSO_LEGACY_DOMAIN_GRACE or row.created_at is None:
        return None
    return row.created_at + timedelta(
        days=max(0, int(settings.SSO_LEGACY_DOMAIN_GRACE_DAYS))
    )


def status(session: Session, row: SsoDomain, now: datetime | None = None) -> str:
    """``verified``, ``grace`` (a legacy domain still usable) or ``unverified``."""
    if row.status == "verified":
        return "verified"
    until = grace_until(row)
    in_grace = until is not None and (now or _now()) < until
    if in_grace and verified_elsewhere(session, row.company_id, row.domain) is None:
        return "grace"
    return "unverified"


def usable(session: Session, company_id: int, domain: str) -> bool:
    """Whether SSO may create or link accounts in *domain* for this company."""
    row = find(session, company_id, domain)
    return row is not None and status(session, row) in ("verified", "grace")


def serialize(session: Session, row: SsoDomain) -> dict[str, Any]:
    until = grace_until(row)
    return {
        "domain": row.domain,
        "status": status(session, row),
        "record_name": record_name(row.domain),
        "record_value": record_value(row),
        "legacy": row.legacy,
        "grace_until": until.isoformat() if until else None,
        "verified_at": row.verified_at.isoformat() if row.verified_at else None,
        "last_checked_at": row.last_checked_at.isoformat()
        if row.last_checked_at
        else None,
        "last_outcome": row.last_outcome,
    }


def _nameservers() -> list[str]:
    raw = str(_settings().SSO_DNS_NAMESERVERS or "")
    return [entry.strip() for entry in raw.split(",") if entry.strip()]


def nameserver_warning() -> str | None:
    """A startup warning when ``SSO_DNS_NAMESERVERS`` names something that is
    not an IP address: every lookup would then be ``unavailable``."""
    import ipaddress

    bad = []
    for entry in _nameservers():
        try:
            ipaddress.ip_address(entry)
        except ValueError:
            bad.append(entry)
    if not bad:
        return None
    return (
        "SSO_DNS_NAMESERVERS holds entries that are not IP addresses "
        f"({', '.join(bad)}): every SSO domain check will answer 'unavailable'."
    )


def _lookup(domain: str) -> tuple[str, list[str], str | None]:
    """What DNS answers at the domain's record name: outcome-to-be, strings, detail."""
    try:
        answer = dns_client.txt_records(
            record_name(domain),
            timeout=float(_settings().SSO_DNS_TIMEOUT_SECONDS),
            nameservers=_nameservers(),
        )
    except dns_client.DnsNameNotFound:
        return "not_found", [], "no such name"
    except dns_client.DnsUnavailable as exc:
        return "unavailable", [], str(exc)[:MAX_DETAIL_CHARS]
    return "answered", answer, None


def _holds(answer: list[str], row: SsoDomain) -> bool:
    expected = record_value(row).encode()
    return any(
        hmac.compare_digest(value.strip().encode(), expected) for value in answer
    )


def _audit(
    session: Session, row: SsoDomain, action: str, trigger: str, actor: str | None
):
    AgentService().audit_interaction(
        session=session,
        company_id=row.company_id,
        interaction_id=f"sso-domain:{row.company_id}:{row.domain}",
        agent_id=f"human:{actor}" if actor else "system:sso-domain-recheck",
        action=action,
        details={"company_id": row.company_id, "record_name": record_name(row.domain)},
        result={"domain": row.domain, "trigger": trigger, "outcome": row.last_outcome},
        decision_signature=None,
        cost_tokens=0,
    )


LAPSE_REASONS = ("definite_negative", "lapsed_unconfirmed")


def notify_lapsed(session: Session, row: SsoDomain, reason: str) -> list[int]:
    """Tell every Owner of *row*'s company that the domain lapsed, in the
    caller's transaction (a security notice: never muted in the app); the
    Owners notified. The notice carries the domain row's id, never the
    domain's name: the Owners read it on the SSO page."""
    if reason not in LAPSE_REASONS:
        raise ValueError(f"unknown lapse reason {reason!r}")
    return notifications.notify(
        session,
        row.company_id,
        notifications.owners(session, row.company_id),
        "sso_domain_lapsed",
        subject_id=row.id,
        dedupe_key=f"sso_domain_lapsed:{row.id}:{reason}:{_now().date().isoformat()}",
    )


def check(
    session: Session, row: SsoDomain, *, trigger: str, requested_by: str | None = None
) -> dict[str, Any]:
    """Look *row*'s record up now, record the observation, update the status.

    Commits. A status change writes an audit row (``sso_domain_verified`` or
    ``sso_domain_unverified``)."""
    outcome, answer, detail = _lookup(row.domain)
    if outcome == "answered":
        outcome = "verified" if _holds(answer, row) else "no_match"
    if outcome == "verified" and trigger != "conflict":
        held = verified_elsewhere(session, row.company_id, row.domain)
        if held is not None:
            # A newer proof: the older claim is looked up again first, and
            # released only if its record is definitely gone.
            check(session, held, trigger="conflict")
            session.refresh(held)
            if held.status == "verified":
                outcome = "verified_elsewhere"
                detail = "another company's verified record of this domain is still published"
    now = _now()
    session.add(
        SsoDomainCheck(
            company_id=row.company_id,
            domain=row.domain,
            checked_at=now,
            trigger=trigger,
            requested_by=requested_by,
            outcome=outcome,
            answer=answer,
            detail=detail,
        )
    )
    before = row.status
    if outcome == "verified":
        row.status, row.verified_at = "verified", now
    elif outcome in DEFINITE_NEGATIVES or outcome == "verified_elsewhere":
        row.status = "unverified"
    row.last_checked_at, row.last_outcome = now, outcome
    session.add(row)
    sso_metrics.record_domain_recheck(trigger, outcome)
    if before == "verified" and row.status != "verified" and trigger != "owner":
        notify_lapsed(session, row, "definite_negative")
    if row.status != before:
        action = (
            "sso_domain_verified"
            if row.status == "verified"
            else "sso_domain_unverified"
        )
        _audit(session, row, action, trigger, requested_by)  # commits
    else:
        session.commit()
    return {"outcome": outcome, "answer": answer, "detail": detail}


def recheck_due(session: Session, now: datetime | None = None) -> int:
    """Look up again every domain last checked over ``SSO_DOMAIN_RECHECK_HOURS``
    ago (or never), at most ``RECHECK_BATCH`` per call; how many were."""
    hours = int(_settings().SSO_DOMAIN_RECHECK_HOURS)
    if hours <= 0:
        return 0
    cutoff = (now or _now()) - timedelta(hours=hours)
    due = session.exec(
        select(SsoDomain)
        .where(
            or_(SsoDomain.last_checked_at.is_(None), SsoDomain.last_checked_at < cutoff)
        )
        .order_by(SsoDomain.last_checked_at)
        .limit(RECHECK_BATCH)
    ).all()
    for row in due:
        check(session, row, trigger="recheck")
    return len(due)


def _recheck_cycle(engine) -> int:
    with Session(engine) as session:
        return recheck_due(session)


async def recheck_once(engine) -> int | None:
    """One cycle of the re-check under its lock: how many domains were looked
    up, or ``None`` when another replica holds the lock or the cycle failed
    (counted, logged, never raised)."""
    try:
        done = await asyncio.to_thread(
            locks.run_exclusive,
            engine,
            locks.SSO_DOMAIN_RECHECK,
            _recheck_cycle,
            engine,
        )
    except Exception as exc:  # noqa: BLE001 - Keep the loop alive.
        sso_metrics.record_recheck_cycle(ok=False)
        logger.warning("SSO domain re-check failed: %s", exc)
        return None
    if done is None:
        return None
    sso_metrics.record_recheck_cycle(ok=True)
    if done:
        logger.info("SSO domain re-check looked up %s domain(s)", done)
    return done


async def run_rechecks(engine) -> None:
    """Background loop: re-check the domains that are due, one replica at a time."""
    logger.info("SSO domain re-check started (every %ss)", RECHECK_WAKE_SECONDS)
    while True:
        await asyncio.sleep(RECHECK_WAKE_SECONDS)
        await recheck_once(engine)
