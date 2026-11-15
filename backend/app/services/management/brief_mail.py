"""The client's brief by mail (issue #331), where the deployment configures it.

The brief (``services/management/manager_brief.py``) is deterministic: every number comes
from the board, the Manager agent's decision rows and the audit log. Here it
is rendered as plain text and sent, once per ``MANAGER_BRIEF_INTERVAL_HOURS``,
to the Owners and Managers of each company that configured the Manager agent
(a ``ManagerPolicy`` row): the client reads what got done, what the agent
decided alone and why, what waits for them and the cost, without opening the
product.

Nothing is sent unless ``SMTP_HOST`` is set. Each send is audited as
``manager_brief_sent`` with the window it covers and how many people it went
to (never their addresses), and the next brief starts where the last one that
went out ended, so a failed send is retried on the next cycle and no window is
skipped. One replica sends at a time (``locks.MANAGER_BRIEF``).
"""

from __future__ import annotations

import asyncio
import logging
import smtplib
import ssl
from collections.abc import Callable
from datetime import UTC, datetime, timedelta
from email.message import EmailMessage
from typing import Any

from config import settings
from models import AuditLog, Company
from models.company import User
from models.manager import ManagerPolicy
from sqlmodel import Session, select

from services.management import manager_brief
from services.platform import locks, teams
from services.platform.agent_service import AgentService

logger = logging.getLogger(__name__)

ACTION = "manager_brief_sent"
AGENT_ID = "system:manager-brief"
LIST_LIMIT = 10
HISTORY_LIMIT = 200
RETRY_AFTER = timedelta(hours=1)

Sender = Callable[[EmailMessage], None]


def _naive(moment: datetime) -> datetime:
    return moment.astimezone(UTC).replace(tzinfo=None) if moment.tzinfo else moment


def enabled() -> bool:
    return bool(settings.SMTP_HOST.strip())


def recipients(session: Session, company_id: int) -> list[str]:
    """The account emails of the company's Owners and Managers, sorted, once each."""
    # The effective role: a member a team makes a Manager receives it too.
    people = teams.members_with_role(session, company_id, "Manager")
    if not people:
        return []
    rows = session.exec(select(User.email).where(User.id.in_(list(people)))).all()
    return sorted({e.strip() for e in rows if e and "@" in e})


def _when(value: Any) -> datetime | None:
    try:
        return _naive(datetime.fromisoformat(value)) if value else None
    except (TypeError, ValueError):
        return None


def history(session: Session, company_id: int) -> dict[str, datetime | None]:
    """Where the last brief that went out ended, and the failed attempts since.

    ``sent_until``: the end of the last sent window. ``pending_since``: the
    start of the oldest attempt that failed after it, so a retry covers the
    whole window. ``last_attempt``: when the latest failed attempt was made.
    """
    out: dict[str, datetime | None] = {
        "sent_until": None,
        "pending_since": None,
        "last_attempt": None,
    }
    rows = session.exec(
        select(AuditLog)
        .where(AuditLog.company_id == company_id, AuditLog.action == ACTION)
        .order_by(AuditLog.id.desc())
        .limit(HISTORY_LIMIT)
    ).all()
    for row in rows:
        result = row.result if isinstance(row.result, dict) else {}
        if result.get("sent"):
            out["sent_until"] = _when(result.get("until"))
            if out["sent_until"] is not None:
                break
            continue
        if out["last_attempt"] is None:
            out["last_attempt"] = _when(result.get("until"))
        since = _when(result.get("since"))
        if since is not None:
            out["pending_since"] = since
    return out


def last_sent(session: Session, company_id: int) -> datetime | None:
    """The end of the window of the last brief that went out to this company."""
    return history(session, company_id)["sent_until"]


def due(session: Session, company_id: int, now: datetime) -> datetime | None:
    """Where the next brief starts, when one is due; ``None`` otherwise.

    A failed attempt is retried from the same start, at most once per
    ``RETRY_AFTER`` (or the interval, when shorter).
    """
    interval = timedelta(hours=settings.MANAGER_BRIEF_INTERVAL_HOURS)
    seen = history(session, company_id)
    if seen["pending_since"] is not None:
        wait = min(interval, RETRY_AFTER)
        last = seen["last_attempt"]
        return seen["pending_since"] if last is None or now - last >= wait else None
    last = seen["sent_until"]
    if last is None:
        return now - interval
    return last if now - last >= interval else None


def _lines(title: str, rows: list[str], total: int) -> list[str]:
    out = [f"{title}: {total}"]
    out += [f"  - {r}" for r in rows[:LIST_LIMIT]]
    if total > min(len(rows), LIST_LIMIT):
        out.append(f"  ... and {total - min(len(rows), LIST_LIMIT)} more")
    return out


def render(company: Company, data: dict[str, Any]) -> tuple[str, str]:
    """The subject and plain-text body of *data*, a ``manager_brief.brief``."""
    done = data.get("done") or {}
    agent = data.get("agent") or {}
    waiting = data.get("waiting") or {}
    cost = data.get("cost") or {}
    record = data.get("record") or {}
    tasks = done.get("tasks") or []
    decided = agent.get("decisions") or []
    subject = (
        f"La Fabrique: {company.company_name}: {done.get('count', len(tasks))} done, "
        f"{sum(int(v or 0) for v in waiting.values())} waiting for you"
    )
    body = [
        f"Brief for {company.company_name}",
        f"From {data.get('since')} to {data.get('until')} (UTC)",
        f"Autonomy level: {data.get('autonomy_level')}",
        "",
        *_lines(
            "Tasks done",
            [f"#{t.get('id')} {t.get('title')}" for t in tasks],
            int(done.get("count", len(tasks)) or 0),
        ),
        "",
        (
            f"Manager agent: {agent.get('suggested', 0)} suggestions, "
            f"{agent.get('taken_alone', 0)} decided alone, "
            f"{agent.get('overridden', 0)} overridden by you"
        ),
        *[
            f"  - {d.get('category')}: {d.get('decision')} "
            f"(rule {d.get('rule')}): {d.get('reason') or ''}".rstrip(": ")
            for d in decided[:LIST_LIMIT]
            if d.get("decided_at")
        ],
        "",
        "Waiting for you:",
        *[f"  - {k.replace('_', ' ')}: {v}" for k, v in sorted(waiting.items()) if v],
        *(["  - nothing"] if not any(waiting.values()) else []),
        "",
        f"Tokens in this window: {cost.get('tokens_since', 0)}"
        + (
            f" (quota used {cost.get('quota_used')} of {cost.get('quota_limit')})"
            if cost.get("quota_limit")
            else ""
        ),
        *(
            ["Over budget: the agent decides nothing alone."]
            if cost.get("over_budget")
            else []
        ),
        *(
            [f"Rules suspended after overrides: {', '.join(record['flagged_rules'])}"]
            if record.get("flagged_rules")
            else []
        ),
        "",
        (
            "Every number here was read from the board, the agent's decision "
            "records and the audit log; no model wrote this brief."
        ),
    ]
    return subject, "\n".join(body) + "\n"


def smtp_send(message: EmailMessage) -> None:
    """Send *message* through the configured SMTP server."""
    host, port = settings.SMTP_HOST.strip(), settings.SMTP_PORT
    timeout = settings.SMTP_TIMEOUT_SECONDS
    if settings.SMTP_SSL:
        server: smtplib.SMTP = smtplib.SMTP_SSL(
            host, port, timeout=timeout, context=ssl.create_default_context()
        )
    else:
        server = smtplib.SMTP(host, port, timeout=timeout)
    with server:
        if settings.SMTP_STARTTLS and not settings.SMTP_SSL:
            server.starttls(context=ssl.create_default_context())
        if settings.SMTP_USER:
            server.login(settings.SMTP_USER, settings.SMTP_PASSWORD)
        server.send_message(message)


def _audit(session: Session, company_id: int, result: dict[str, Any]) -> None:
    AgentService().audit_interaction(
        session=session,
        company_id=company_id,
        interaction_id=f"company:{company_id}:brief",
        agent_id=AGENT_ID,
        action=ACTION,
        details={"company_id": str(company_id)},
        result=result,
        decision_signature=None,
        cost_tokens=0,
    )


def send_company(
    session: Session, company: Company, since: datetime, now: datetime, send: Sender
) -> bool:
    """Send the brief of *company* for ``[since, now)``; audited either way."""
    to = recipients(session, company.id)
    window = {"since": since.isoformat(), "until": now.isoformat()}
    if not to:
        _audit(
            session,
            company.id,
            {**window, "sent": False, "error": "no Owner or Manager with an email"},
        )
        session.commit()
        return False
    data = manager_brief.brief(session, company, since=since)
    data["until"] = now.isoformat()
    subject, text = render(company, data)
    message = EmailMessage()
    message["Subject"] = subject
    message["From"] = (
        settings.SMTP_FROM or settings.SMTP_USER or "la-fabrique@localhost"
    )
    message["To"] = ", ".join(to)
    message.set_content(text)
    try:
        send(message)
    except (OSError, smtplib.SMTPException) as exc:
        # The reason, never an address: SMTP errors can echo recipients.
        error = type(exc).__name__
        logger.warning("Brief for company %s not sent: %s", company.id, error)
        _audit(session, company.id, {**window, "sent": False, "error": error})
        session.commit()
        return False
    _audit(session, company.id, {**window, "sent": True, "recipients": len(to)})
    session.commit()
    return True


def brief_cycle(engine, send: Sender = smtp_send, now: datetime | None = None) -> int:
    """Send every brief that is due; how many went out."""
    if not enabled():
        return 0
    moment = _naive(now) if now else datetime.now(UTC).replace(tzinfo=None)
    sent = 0
    with Session(engine) as session:
        company_ids = session.exec(select(ManagerPolicy.company_id)).all()
        for company_id in sorted(set(company_ids)):
            company = session.get(Company, company_id)
            if company is None:
                continue
            since = due(session, company_id, moment)
            if since is None:
                continue
            if send_company(session, company, since, moment, send):
                sent += 1
    return sent


async def run_brief_mail(engine) -> None:
    """Background loop: send the briefs that are due (started with the Manager agent)."""
    logger.info(
        "Manager brief mail started (every %sh)", settings.MANAGER_BRIEF_INTERVAL_HOURS
    )
    while True:
        try:
            count = await asyncio.to_thread(
                locks.run_exclusive, engine, locks.MANAGER_BRIEF, brief_cycle, engine
            )
            if count:
                logger.info("Sent %s Manager brief(s)", count)
        except Exception as exc:  # noqa: BLE001 - Keep the loop alive.
            logger.warning("Manager brief cycle failed: %s", exc)
        await asyncio.sleep(settings.MANAGER_BRIEF_CHECK_SECONDS)
