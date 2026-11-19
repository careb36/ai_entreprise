"""Approvals in Slack or Teams: what is posted, when, and what a click may still decide.

Two approval points are wired. The risk hold: QA approved a delivery that
waits for a person's acceptance (a high risk tier, or every approval at
autonomy level 1; ``services/loop/worker/qa_review.py`` writes the
``risk_hold`` task event); Approve or Send back. The triage block: a task
blocked until a person acts, without a hold (the inbox's blocked task); Send
back only, as on the board. A ``before_flush`` listener (``install``) sees a
new ``risk_hold`` event or an event that blocks a task and queues one
``chat_approval_post`` outbox row per enabled connection of the company, in
the same transaction, so a rolled-back change posts nothing. The outbox
processor calls ``deliver``, which reads the point again from the record
(``current_point``: still waiting, the run and its latest version, the
Verifier's verdict and the checks that did not pass) and posts it; a block
the worker still retries or reviews on its own posts nothing. Updates after a decision and Slack's ephemeral refusals go through the
same outbox (``chat_approval_update``, ``chat_ephemeral``) and are retried
like any delivery.

A click decides only what the message was posted for: ``claim`` compares the
hold event's chain hash and the run's version stored on the message (and
carried in the button) with the record now, and takes the message with a
conditional update from ``posted``, so two clicks never both act. The
decision itself is the web's (``services/loop/run_review.submit_review``),
called by ``routers/chat_interactions.py``, which this context cannot import.
"""

from __future__ import annotations

import secrets
from datetime import UTC, datetime, timedelta
from typing import Any

from logging_config import logger
from models import (
    AgentRun,
    BacklogItem,
    ChatApprovalMessage,
    ChatConnection,
    OutboxEvent,
    Sprint,
    TaskEvent,
    Verification,
)
from models.agent_run import TASK_WORKER_PRODUCER
from sqlalchemy import event, update
from sqlalchemy.orm import Session as OrmSession
from sqlmodel import Session, select

from services.integrations import chat_client, chat_connections, chat_messages
from services.tasks import task_events, task_projection

POST = "chat_approval_post"
UPDATE = "chat_approval_update"
EPHEMERAL = "chat_ephemeral"
EVENT_TYPES = (POST, UPDATE, EPHEMERAL)
# The approval points: what a message was posted for, read from the record.
RISK_HOLD = "risk_hold"
TRIAGE = "triage"
PASSING = {"pass", "passed", "verified", "skipped", "ok"}
CODE_ALPHABET = "ABCDEFGHJKLMNPQRSTUVWXYZ23456789"
# A claim not finished by then (the process stopped between the claim and the
# commit of the decision) may be taken again; the record check decides afresh.
CLAIM_TIMEOUT = timedelta(minutes=2)


class StaleApproval(Exception):
    """The message no longer matches the record; the message is safe to show in chat."""


def _now() -> datetime:
    return datetime.now(UTC).replace(tzinfo=None)


def _app_url() -> str:
    from config import settings

    return settings.CHAT_APP_URL.rstrip("/")


# ---------------------------------------------------------------------------
# The approval point, as the record has it now
# ---------------------------------------------------------------------------


def task_run(session: Session, company_id: int, task_id: int) -> AgentRun | None:
    """The task's latest in-house run (its highest version)."""
    return session.exec(
        select(AgentRun)
        .where(
            AgentRun.company_id == company_id,
            AgentRun.external_ref == f"task-{task_id}",
            AgentRun.producer == TASK_WORKER_PRODUCER,
        )
        .order_by(AgentRun.version.desc(), AgentRun.id.desc())
    ).first()


def _failing_checks(verification: Verification | None) -> list[dict[str, str]]:
    failing = []
    for check in (verification.checks if verification else None) or []:
        if not isinstance(check, dict):
            continue
        outcome = str(check.get("outcome") or "").lower()
        if outcome and outcome not in PASSING:
            failing.append(
                {"name": str(check.get("name") or "check"), "outcome": outcome}
            )
    return failing


def _blocked_in(
    session: Session, item: BacklogItem | None, company_id: int
) -> Sprint | None:
    """*item*'s sprint when *item* is blocked in a sprint of *company_id*."""
    if item is None or item.id is None or item.status != "blocked":
        return None
    sprint = session.get(Sprint, item.sprint_id)
    if sprint is None or sprint.company_id != company_id:
        return None
    return sprint


def _point(
    session: Session,
    company_id: int,
    kind: str,
    item: BacklogItem,
    sprint: Sprint,
    run: AgentRun,
    event: TaskEvent,
    note: str,
) -> dict[str, Any]:
    verification = session.exec(
        select(Verification)
        .where(
            Verification.agent_run_id == run.id,
            Verification.company_id == company_id,
        )
        .order_by(Verification.id.desc())
    ).first()
    app = _app_url()
    return {
        "kind": kind,
        "company_id": company_id,
        "task_id": item.id,
        "title": item.title,
        "sprint_name": sprint.sprint_name,
        "run_id": run.id,
        "run_version": run.version,
        "hold_event_id": event.id,
        "head": event.chain_hash,
        "hold_note": note,
        "verdict": verification.verdict if verification else "not verified yet",
        "failing_checks": _failing_checks(verification),
        "task_url": f"{app}/sprints/{sprint.id}/tasks/{item.id}",
        "run_url": f"{app}/evidence/{run.id}",
    }


def approval_point(
    session: Session, item: BacklogItem | None, company_id: int
) -> dict[str, Any] | None:
    """What a person would accept for *item* now, or ``None`` when nothing waits."""
    sprint = _blocked_in(session, item, company_id)
    if sprint is None or item is None:
        return None
    events = task_events.for_task(session, item.id)
    hold = task_projection.risk_hold_from_events(events)
    if not isinstance(hold, TaskEvent) or not hold.chain_hash:
        return None
    if not task_projection.awaits_risk_acceptance(session, item):
        return None
    run = task_run(session, company_id, item.id)
    if run is None:
        return None
    note = (hold.payload or {}).get("text") or "Held for a person"
    return _point(session, company_id, RISK_HOLD, item, sprint, run, hold, note)


def triage_point(
    session: Session, item: BacklogItem | None, company_id: int
) -> dict[str, Any] | None:
    """A task blocked until a person acts, without a risk hold, or ``None``.

    The inbox's blocked task (``routers/inbox.py``): nothing the worker does
    on its own moves it, it is not frozen, not carried to another sprint and
    not in an archived sprint. The message is posted for the event that
    blocked it (the latest that set ``blocked``); a note added since leaves
    the message current, a new block or any other move makes it stale. A
    task with no in-house run stays in the web inbox.
    """
    sprint = _blocked_in(session, item, company_id)
    if sprint is None or item is None or sprint.archived_at is not None:
        return None
    events = task_events.for_task(session, item.id)
    record = task_projection.TaskRecord(item.comments, events, item.assignee)
    if record.next_step() is not None or record.carried("to"):
        return None
    if record.awaits_risk_acceptance():
        return None  # the risk hold's own message
    block = next(
        (e for e in reversed(events) if e.task_status == "blocked" and e.chain_hash),
        None,
    )
    run = task_run(session, company_id, item.id)
    if block is None or run is None:
        return None
    note = record.block_reason() or "Blocked until a person acts"
    return _point(session, company_id, TRIAGE, item, sprint, run, block, note)


def current_point(
    session: Session, item: BacklogItem | None, company_id: int
) -> dict[str, Any] | None:
    """What waits for a person on *item* now: a risk hold, a triage block or nothing."""
    return approval_point(session, item, company_id) or triage_point(
        session, item, company_id
    )


def kind_of(session: Session, message: ChatApprovalMessage) -> str:
    """The point *message* was posted for, from the event it names in the record."""
    event = session.get(TaskEvent, message.hold_event_id)
    return (
        RISK_HOLD if event is not None and event.event_type == "risk_hold" else TRIAGE
    )


# ---------------------------------------------------------------------------
# Watching the session for a new hold
# ---------------------------------------------------------------------------


def _post_pending(session: OrmSession, connection_id: int, task_id: int) -> bool:
    """Whether a post for *task_id* to *connection_id* already waits to be delivered."""
    rows = list(
        session.execute(
            select(OutboxEvent).where(
                OutboxEvent.aggregate_id == str(connection_id),
                OutboxEvent.event_type == POST,
                OutboxEvent.status == "pending",
            )
        ).scalars()
    ) + [o for o in session.new if isinstance(o, OutboxEvent) and o.event_type == POST]
    return any(
        (r.payload or {}).get("connection_id") == connection_id
        and (r.payload or {}).get("task_id") == task_id
        for r in rows
    )


def _before_flush(session: OrmSession, _context: Any, _instances: Any) -> None:
    try:
        with session.no_autoflush:
            # A hold, or any event that blocks a task: whether it waits for a
            # person is read again from the record when the post is delivered.
            holds = [
                obj
                for obj in session.new
                if isinstance(obj, TaskEvent)
                and (obj.event_type == "risk_hold" or obj.task_status == "blocked")
                and obj.task_id is not None
            ]
            for hold in holds:
                connections = (
                    session.execute(
                        select(ChatConnection).where(
                            ChatConnection.company_id == hold.company_id,
                            ChatConnection.enabled.is_(True),  # type: ignore[union-attr]
                        )
                    )
                    .scalars()
                    .all()
                )
                for connection in connections:
                    if _post_pending(session, connection.id, hold.task_id):
                        continue  # its delivery reads the record as it is then
                    session.add(
                        OutboxEvent(
                            aggregate_type="chat_connection",
                            aggregate_id=str(connection.id),
                            event_type=POST,
                            payload={
                                "connection_id": connection.id,
                                "task_id": hold.task_id,
                            },
                        )
                    )
    except Exception:  # noqa: BLE001 - A notification never stops the change it reports.
        logger.warning("Chat approvals: could not queue a post", exc_info=True)


_installed = False


def install() -> None:
    """Watch every session for a new risk hold or block (idempotent)."""
    global _installed
    if not _installed:
        event.listen(OrmSession, "before_flush", _before_flush)
        _installed = True


def queue(session: Session, kind: str, connection_id: int, **payload: Any) -> None:
    """Add an update or an ephemeral reply to the outbox in the caller's transaction."""
    session.add(
        OutboxEvent(
            aggregate_type="chat_connection",
            aggregate_id=str(connection_id),
            event_type=kind,
            payload={"connection_id": connection_id, **payload},
        )
    )


# ---------------------------------------------------------------------------
# Deciding: what a click may still act on
# ---------------------------------------------------------------------------


def message_for(
    session: Session,
    connection: ChatConnection,
    *,
    message_id: int | None = None,
    short_code: str | None = None,
) -> ChatApprovalMessage | None:
    query = select(ChatApprovalMessage).where(
        ChatApprovalMessage.connection_id == connection.id,
        ChatApprovalMessage.company_id == connection.company_id,
    )
    if message_id is not None:
        query = query.where(ChatApprovalMessage.id == message_id)
    elif short_code:
        query = query.where(
            ChatApprovalMessage.short_code == short_code.strip().upper()
        )
    else:
        return None
    return session.exec(query).first()


def mark_stale(session: Session, message: ChatApprovalMessage, outcome: str) -> None:
    message.status, message.outcome = "stale", outcome[:300]
    session.add(message)
    queue(session, UPDATE, message.connection_id, message_id=message.id)


def claim(
    session: Session,
    connection: ChatConnection,
    message: ChatApprovalMessage | None,
    *,
    head: str | None = None,
    run_version: int | None = None,
) -> tuple[AgentRun, BacklogItem, str]:
    """Take *message* for one decision, or raise ``StaleApproval``; commits the claim.

    Returns the run, the task and the point's kind (``RISK_HOLD`` or ``TRIAGE``).
    """
    if message is None:
        raise StaleApproval("This approval is not known here.")
    now = _now()
    abandoned = (
        message.status == "deciding"
        and message.decided_at is not None
        and message.decided_at < now - CLAIM_TIMEOUT
    )
    if message.status != "posted" and not abandoned:
        raise StaleApproval("This approval was already decided or is out of date.")
    claimed_status, claimed_at = message.status, message.decided_at
    if (head is not None and head != chat_messages.head_tag(message.head_hash)) or (
        run_version is not None and run_version != message.run_version
    ):
        raise StaleApproval("This button does not match the approval it belongs to.")
    item = session.get(BacklogItem, message.task_id)
    point = current_point(session, item, connection.company_id)
    if (
        point is None
        or point["hold_event_id"] != message.hold_event_id
        or point["head"] != message.head_hash
        or point["run_id"] != message.run_id
        or point["run_version"] != message.run_version
    ):
        mark_stale(
            session, message, "Out of date: the task changed since this message."
        )
        session.commit()
        raise StaleApproval(
            "The task changed since this message (a new delivery or another "
            "decision). Nothing was changed; open La Fabrique to see it now."
        )
    taken = session.exec(
        update(ChatApprovalMessage)
        .where(
            ChatApprovalMessage.id == message.id,
            ChatApprovalMessage.status == claimed_status,
            ChatApprovalMessage.decided_at.is_(None)  # type: ignore[union-attr]
            if claimed_at is None
            else ChatApprovalMessage.decided_at == claimed_at,
        )
        .values(status="deciding", decided_at=now)
    )
    session.commit()
    if taken.rowcount != 1:
        raise StaleApproval("Someone is deciding this approval right now.")
    session.refresh(message)
    run = session.get(AgentRun, message.run_id)
    if run is None or item is None:  # approval_point found both a moment ago
        release(session, message)
        raise StaleApproval("This approval is not known here.")
    return run, item, point["kind"]


def release(session: Session, message: ChatApprovalMessage) -> None:
    """Give a claimed message back after a refused decision, so it can be decided again."""
    session.exec(
        update(ChatApprovalMessage)
        .where(
            ChatApprovalMessage.id == message.id,
            ChatApprovalMessage.status == "deciding",
        )
        .values(status="posted", decided_at=None)
    )
    session.commit()


def finish(
    session: Session,
    message: ChatApprovalMessage,
    *,
    decision: str,
    user_id: int,
    outcome_text: str,
) -> None:
    """Record the decision on the message and queue its update; the caller commits.

    The same hold posted to the other platform is marked out of date with the
    outcome, so its buttons say what happened instead of offering a decision.
    """
    message.status, message.decision = "decided", decision
    message.decided_by_user_id, message.decided_at = user_id, _now()
    message.outcome = outcome_text[:300]
    session.add(message)
    queue(session, UPDATE, message.connection_id, message_id=message.id)
    siblings = session.exec(
        select(ChatApprovalMessage).where(
            ChatApprovalMessage.task_id == message.task_id,
            ChatApprovalMessage.hold_event_id == message.hold_event_id,
            ChatApprovalMessage.id != message.id,
            ChatApprovalMessage.status == "posted",
        )
    ).all()
    for sibling in siblings:
        mark_stale(session, sibling, outcome_text)


# ---------------------------------------------------------------------------
# Delivery (the outbox processor)
# ---------------------------------------------------------------------------


def _short_code(session: Session) -> str:
    while True:
        code = "".join(secrets.choice(CODE_ALPHABET) for _ in range(8))
        taken = session.exec(
            select(ChatApprovalMessage.id).where(ChatApprovalMessage.short_code == code)
        ).first()
        if taken is None:
            return code


async def _post(session: Session, connection: ChatConnection, task_id: int) -> None:
    item = session.get(BacklogItem, task_id)
    point = current_point(session, item, connection.company_id)
    if point is None:
        return  # decided or changed since: nothing waits, nothing to retry
    message = session.exec(
        select(ChatApprovalMessage).where(
            ChatApprovalMessage.connection_id == connection.id,
            ChatApprovalMessage.hold_event_id == point["hold_event_id"],
        )
    ).first()
    if message is not None and message.status != "queued":
        return  # posted already (a retry after the post succeeded)
    if message is None:
        message = ChatApprovalMessage(
            company_id=connection.company_id,
            connection_id=connection.id,
            provider=connection.provider,
            task_id=point["task_id"],
            run_id=point["run_id"],
            run_version=point["run_version"],
            hold_event_id=point["hold_event_id"],
            head_hash=point["head"],
            short_code=_short_code(session),
            channel=connection.channel,
        )
        session.add(message)
        session.flush()
    credential = chat_connections.credential_of(connection)
    if connection.provider == "slack":
        answer = await chat_client.slack_call(
            "chat.postMessage",
            credential,
            {
                "channel": connection.channel,
                "text": f"{chat_messages.heading(point)}: {point['title']}",
                "blocks": chat_messages.slack_blocks(
                    point, message.id, message.short_code
                ),
            },
            chat_connections.TIMEOUT_SECONDS,
        )
        message.message_ts = str(answer.get("ts") or "") or None
        message.channel = str(answer.get("channel") or connection.channel)
    else:
        await _teams(credential, chat_messages.teams_card(point, message.short_code))
    message.status = "posted"
    session.add(message)


async def _teams(credential: str, card: dict[str, Any]) -> None:
    import asyncio

    url = await asyncio.to_thread(chat_connections.check_teams_url, credential)
    status = await chat_client.teams_post(url, card, chat_connections.TIMEOUT_SECONDS)
    if not 200 <= status < 300:
        raise chat_client.ChatPostError(f"Teams answered HTTP {status}")


async def _update(
    session: Session, connection: ChatConnection, message_id: int
) -> None:
    message = session.get(ChatApprovalMessage, message_id)
    if (
        message is None
        or message.connection_id != connection.id
        or connection.provider != "slack"
        or not message.message_ts
    ):
        return  # Teams answers in the thread at once; nothing to edit
    item = session.get(BacklogItem, message.task_id)
    await chat_client.slack_call(
        "chat.update",
        chat_connections.credential_of(connection),
        {
            "channel": message.channel,
            "ts": message.message_ts,
            "text": message.outcome or "Decided",
            "blocks": chat_messages.slack_outcome_blocks(
                item.title if item else "Task",
                message.outcome or "Decided",
                f"{_app_url()}/evidence/{message.run_id}",
            ),
        },
        chat_connections.TIMEOUT_SECONDS,
    )


async def deliver(
    session: Session, kind: str, payload: dict[str, Any], delivery_id: int
) -> None:
    """Carry out one queued chat delivery; raises on failure so the outbox retries it."""
    connection = session.get(ChatConnection, payload.get("connection_id"))
    if connection is None or not connection.enabled:
        return  # removed or turned off since: nothing to send, nothing to retry
    try:
        if kind == POST:
            await _post(session, connection, int(payload.get("task_id") or 0))
        elif kind == UPDATE:
            await _update(session, connection, int(payload.get("message_id") or 0))
        elif kind == EPHEMERAL and connection.provider == "slack":
            await chat_client.slack_call(
                "chat.postEphemeral",
                chat_connections.credential_of(connection),
                {
                    "channel": str(payload.get("channel") or connection.channel),
                    "user": str(payload.get("user") or ""),
                    "text": chat_messages.slack_escape(payload.get("text"), 500),
                },
                chat_connections.TIMEOUT_SECONDS,
            )
    except (chat_client.ChatPostError, chat_connections.InvalidConnection) as exc:
        chat_connections.record(connection, "error", f"{kind} #{delivery_id}: {exc}")
        session.add(connection)
        raise
    chat_connections.record(connection, "ok", None)
    session.add(connection)
