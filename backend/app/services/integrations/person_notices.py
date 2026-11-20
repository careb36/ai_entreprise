"""The company events that target a person, as personal notifications
(issue #683; ``services/platform/notifications.py`` holds the rows).

Watched in one place, over every session (``install``), as the webhook and
the chat approvals are, so a new place that writes one of these changes
notifies without knowing about notifications:

- a new ``risk_hold`` task event (QA approved a high-risk delivery that
  waits for a person to accept it): ``review_requested`` to the members whose
  effective role may accept it (Manager or Owner);
- a ``SprintIntegration`` that is written with, or changes to, a status that
  waits for a person (``WAITS_FOR_A_PERSON``): ``integration_waiting`` to the
  same members.

The changes are read in ``after_flush`` (the ids are assigned and the
attribute history is still there) and the notices written in
``after_flush_postexec``, so they join the same transaction and are flushed
by its commit; a change that rolls back notifies no one. A failure here is
logged and never stops the change it reports.

``deliver_email`` is the outbox's ``notification_email``: one plain-text
mail that says a notice of that kind is waiting, without the subject's text,
the company's name or anyone's address beyond the receiver's own. It is
sent only while SMTP is configured, the person is still a member and still
wants the email; otherwise it is dropped, never retried.
"""

from __future__ import annotations

import asyncio
from collections.abc import Callable
from email.message import EmailMessage
from typing import Any

from logging_config import logger
from models import CompanyMember, SprintIntegration, TaskEvent, User
from models.integration import WAITS_FOR_A_PERSON
from sqlalchemy import event, inspect, select
from sqlalchemy.orm import Session as OrmSession

from services.platform import mail, notifications, teams

EVENT_TYPES = (notifications.EMAIL_EVENT,)
_PENDING = "person_notices_pending"
# The subject line of each kind's email: what it is, never what it is about.
EMAIL_SUBJECTS = {
    "sso_break_glass_exclusion": "Security notice: a break-glass team exclusion",
    "sso_break_glass_lifted": "Security notice: a break-glass exclusion was lifted",
    "sso_domain_lapsed": "Security notice: an SSO domain is no longer verified",
    "review_requested": "A delivery waits for a person to accept it",
    "integration_waiting": "A sprint's integration waits for a person",
}


def _status_changed(obj: Any) -> str | None:
    history = inspect(obj).attrs["status"].history
    if not history.added:
        return None
    new = history.added[0]
    return new if new not in (history.deleted or ()) else None


def _found(session: OrmSession) -> list[tuple[int, str, int, dict, str]]:
    """``(company_id, kind, subject_id, context, dedupe_key)`` of this flush."""
    out: list[tuple[int, str, int, dict, str]] = []
    for obj in list(session.new):
        if (
            isinstance(obj, TaskEvent)
            and obj.event_type == "risk_hold"
            and obj.task_id is not None
            and obj.id is not None
        ):
            out.append(
                (
                    obj.company_id,
                    "review_requested",
                    obj.task_id,
                    {"sprint_id": obj.sprint_id},
                    f"review_requested:{obj.id}",
                )
            )
    for obj in list(session.new) + list(session.dirty):
        if not isinstance(obj, SprintIntegration) or obj.id is None:
            continue
        status = obj.status if obj in session.new else _status_changed(obj)
        if status in WAITS_FOR_A_PERSON:
            out.append(
                (
                    obj.company_id,
                    "integration_waiting",
                    obj.id,
                    {"sprint_id": obj.sprint_id},
                    f"integration_waiting:{obj.id}:{status}",
                )
            )
    return out


def _after_flush(session: OrmSession, _context: Any) -> None:
    try:
        found = _found(session)
        if found:
            session.info.setdefault(_PENDING, []).extend(found)
    except Exception:  # noqa: BLE001 - A notice never stops the change it reports.
        logger.warning("Personal notices: could not read the flush", exc_info=True)


def _after_flush_postexec(session: OrmSession, _context: Any) -> None:
    pending = session.info.pop(_PENDING, None)
    if not pending:
        return
    try:
        with session.no_autoflush:
            for company_id, kind, subject_id, context, dedupe in pending:
                receivers = teams.members_with_role(session, company_id, "Manager")
                notifications.notify(
                    session,
                    company_id,
                    set(receivers),
                    kind,
                    subject_id,
                    context=context,
                    dedupe_key=dedupe,
                )
    except Exception:  # noqa: BLE001 - A notice never stops the change it reports.
        logger.warning("Personal notices: could not write a notice", exc_info=True)


_installed = False


def install() -> None:
    """Watch every session for the events that target a person (idempotent)."""
    global _installed
    if not _installed:
        event.listen(OrmSession, "after_flush", _after_flush)
        event.listen(OrmSession, "after_flush_postexec", _after_flush_postexec)
        _installed = True


# --- the email copy (the outbox processor) ------------------------------------


def render(kind: str, address: str) -> EmailMessage:
    """The mail for one notice of *kind*: what it is and where to read it."""
    message = EmailMessage()
    message["Subject"] = "La Fabrique: " + EMAIL_SUBJECTS.get(
        kind, "a notification is waiting"
    )
    message["From"] = mail.sender()
    message["To"] = address
    spec = notifications.KINDS.get(kind)
    message.set_content(
        "A notification is waiting for you in La Fabrique.\n\n"
        + (
            "It is a security notice: it stays in your notifications until you read it.\n"
            if spec and spec.security
            else ""
        )
        + "Sign in and open Notifications to read it. This mail carries no "
        "detail on purpose.\n"
    )
    return message


async def deliver_email(
    session: Any,
    payload: dict[str, Any],
    send: Callable[[EmailMessage], None] | None = None,
) -> bool:
    """Send one queued notice by mail; whether it was sent. Raises on a mail
    server failure, so the outbox retries it."""
    if not mail.enabled():
        return False
    user_id, company_id, kind = (
        payload.get("user_id"),
        payload.get("company_id"),
        payload.get("kind"),
    )
    if kind not in notifications.KINDS:
        return False
    member = session.execute(
        select(CompanyMember.id).where(
            CompanyMember.user_id == user_id, CompanyMember.company_id == company_id
        )
    ).first()
    if member is None or not notifications.preferences(session, user_id)[kind]["email"]:
        return False
    user = session.get(User, user_id)
    address = (getattr(user, "email", "") or "").strip()
    if "@" not in address:
        return False
    await asyncio.to_thread(send or mail.smtp_send, render(kind, address))
    return True
