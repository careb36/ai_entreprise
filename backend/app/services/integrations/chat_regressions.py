"""Regressions posted to the company's Slack or Teams (issue #682, split from #512).

A regression here is something the factory observed, never an agent's claim:

- ``integration``: a sprint integration whose test run failed
  (``tests_failed``, decided from the workspace daemon's exit code) where the
  company's previous integration on the same repository and base with a test
  outcome passed (``tests_passed``) with the same test command;
- ``verification``: a refuted verification (the Verifier's verdict) of a run
  whose previous verification was ``verified``, while the run's task is done:
  a delivery that was accepted and now fails verification.

Listening: ``after_flush`` reads what the flush wrote (ids assigned, attribute
history still there) and decides; ``after_flush_postexec`` adds one
``chat_regression_post`` outbox row per enabled connection of the company, in
the same transaction, so a rolled-back record posts nothing. Each row is
keyed (``aggregate_id`` ``<connection>:<kind>:<subject id>``): a key already
queued is not queued again, and delivery skips a key another row already
delivered, so a regression is posted once per connection. Delivery reads the
regression again from the record and posts it with a link to the evidence;
the message carries no button.
"""

from __future__ import annotations

import asyncio
from typing import Any

from logging_config import logger
from models import (
    AgentRun,
    BacklogItem,
    ChatConnection,
    OutboxEvent,
    Sprint,
    Verification,
)
from models.integration import SprintIntegration
from sqlalchemy import event, inspect
from sqlalchemy.orm import Session as OrmSession
from sqlmodel import Session, select

from services.integrations import chat_client, chat_connections
from services.integrations import chat_regression_messages as messages

POST = "chat_regression_post"
AGGREGATE = "chat_regression"
EVENT_TYPES = (POST,)
DECIDED = ("tests_passed", "tests_failed")
PASSING = {"pass", "passed", "verified", "skipped", "ok"}
_PENDING = "chat_regressions_pending"


def key(connection_id: int, kind: str, subject_id: int) -> str:
    """The outbox key of one regression for one connection."""
    return f"{connection_id}:{kind}:{subject_id}"


def _app_url() -> str:
    from config import settings

    return settings.CHAT_APP_URL.rstrip("/")


def _first(session: Any, query: Any) -> Any:
    # ``execute`` rather than ``exec``: the listeners get a plain ORM session.
    return session.execute(query).scalars().first()


def _short(commit: Any) -> str:
    return str(commit or "")[:12] or "unknown"


# ---------------------------------------------------------------------------
# What counts as a regression, read from the record
# ---------------------------------------------------------------------------


def integration_regression(
    session: Session, row: SprintIntegration | None
) -> dict[str, Any] | None:
    """The facts of *row*'s regression, or ``None`` when it is not one."""
    if row is None or row.id is None or row.status != "tests_failed":
        return None
    query = select(SprintIntegration).where(
        SprintIntegration.company_id == row.company_id,
        SprintIntegration.base == row.base,
        SprintIntegration.id < row.id,
        SprintIntegration.status.in_(DECIDED),  # type: ignore[union-attr]
    )
    if row.repository is None:
        query = query.where(SprintIntegration.repository.is_(None))  # type: ignore[union-attr]
    else:
        query = query.where(SprintIntegration.repository == row.repository)
    previous = _first(session, query.order_by(SprintIntegration.id.desc()))
    if previous is None or previous.status != "tests_passed":
        return None
    now_run, before_run = row.test_run or {}, previous.test_run or {}
    command = now_run.get("command")
    if not command or command != before_run.get("command"):
        return None  # another suite: nothing to compare
    sprint = session.get(Sprint, row.sprint_id)
    exit_code = now_run.get("exit_code")
    outcome = "timed out" if now_run.get("timed_out") else f"exit code {exit_code}"
    now_at = _short(row.tested_commit or row.commit)
    before_at = _short(previous.tested_commit or previous.commit)
    return {
        "kind": "integration",
        "subject_id": row.id,
        "company_id": row.company_id,
        "headline": "Integration tests that passed before now fail",
        "title": f"{sprint.sprint_name if sprint else 'Sprint'} · {row.branch}",
        "facts": [
            (
                "Now",
                f"integration {row.id} failed ({outcome}) at {now_at}",
            ),
            (
                "Before",
                f"integration {previous.id} of sprint {previous.sprint_id} "
                + f"passed at {before_at}",
            ),
            ("Test command", command),
            ("Base", f"{row.base} · {row.repository or 'repository not reported'}"),
        ],
        "lines": [],
        "evidence_url": f"{_app_url()}/sprints/{row.sprint_id}",
        "evidence_label": f"Integration {row.id} test run",
    }


def _failing_checks(verification: Verification) -> list[str]:
    failing = []
    for check in verification.checks or []:
        if not isinstance(check, dict):
            continue
        outcome = str(check.get("outcome") or "").lower()
        if outcome and outcome not in PASSING:
            failing.append(f"{check.get('name') or 'check'}: {outcome}")
    return failing


def verification_regression(
    session: Session, verification: Verification | None
) -> dict[str, Any] | None:
    """The facts of an accepted delivery's refutation, or ``None``."""
    if (
        verification is None
        or verification.id is None
        or verification.verdict != "refuted"
    ):
        return None
    previous = _first(
        session,
        select(Verification)
        .where(
            Verification.agent_run_id == verification.agent_run_id,
            Verification.company_id == verification.company_id,
            Verification.id < verification.id,
        )
        .order_by(Verification.id.desc()),
    )
    if previous is None or previous.verdict != "verified":
        return None
    run = session.get(AgentRun, verification.agent_run_id)
    if run is None or run.company_id != verification.company_id:
        return None
    ref = str(run.external_ref or "")
    if not ref.startswith("task-") or not ref[5:].isdigit():
        return None
    item = session.get(BacklogItem, int(ref[5:]))
    if item is None or item.status != "done":
        return None  # not accepted: the delivery's own checks, not a regression
    sprint = session.get(Sprint, item.sprint_id)
    if sprint is None or sprint.company_id != verification.company_id:
        return None
    app = _app_url()
    return {
        "kind": "verification",
        "subject_id": verification.id,
        "company_id": verification.company_id,
        "headline": "An accepted delivery now fails verification",
        "title": f"{item.title} · {sprint.sprint_name}",
        "facts": [
            ("Verdict (Verifier)", f"refuted (verification {verification.id})"),
            ("Before", f"verified (verification {previous.id})"),
            ("Run", f"{run.id} v{run.version}"),
            ("Task", f"{app}/sprints/{sprint.id}/tasks/{item.id}"),
        ],
        "lines": _failing_checks(verification),
        "evidence_url": f"{app}/evidence/{run.id}",
        "evidence_label": f"Run {run.id} v{run.version} evidence",
    }


def regression_of(
    session: Session, kind: str, subject_id: int
) -> dict[str, Any] | None:
    if kind == "integration":
        return integration_regression(
            session, session.get(SprintIntegration, subject_id)
        )
    if kind == "verification":
        return verification_regression(session, session.get(Verification, subject_id))
    return None


# ---------------------------------------------------------------------------
# Watching the session
# ---------------------------------------------------------------------------


def _became_failed(obj: SprintIntegration, new: bool) -> bool:
    if obj.status != "tests_failed":
        return False
    if new:
        return True
    history = inspect(obj).attrs["status"].history
    return bool(history.added) and "tests_failed" not in (history.deleted or ())


def _after_flush(session: OrmSession, _context: Any) -> None:
    found: list[dict[str, Any]] = []
    try:
        with session.no_autoflush:
            for obj in list(session.new) + list(session.dirty):
                facts = None
                if isinstance(obj, SprintIntegration) and _became_failed(
                    obj, obj in session.new
                ):
                    facts = integration_regression(session, obj)  # type: ignore[arg-type]
                elif isinstance(obj, Verification) and obj in session.new:
                    facts = verification_regression(session, obj)  # type: ignore[arg-type]
                if facts:
                    found.append(facts)
    except Exception:  # noqa: BLE001 - A notification never stops the change it reports.
        logger.warning("Chat regressions: could not read a regression", exc_info=True)
        found = []
    session.info[_PENDING] = found


def _after_flush_postexec(session: OrmSession, _context: Any) -> None:
    found = session.info.pop(_PENDING, None)
    if not found:
        return
    try:
        with session.no_autoflush:
            for facts in found:
                connections = (
                    session.execute(
                        select(ChatConnection).where(
                            ChatConnection.company_id == facts["company_id"],
                            ChatConnection.enabled.is_(True),  # type: ignore[union-attr]
                        )
                    )
                    .scalars()
                    .all()
                )
                for connection in connections:
                    name = key(connection.id, facts["kind"], facts["subject_id"])
                    queued = session.execute(
                        select(OutboxEvent.id).where(
                            OutboxEvent.aggregate_type == AGGREGATE,
                            OutboxEvent.aggregate_id == name,
                            OutboxEvent.event_type == POST,
                        )
                    ).first()
                    if queued is not None:
                        continue
                    session.add(
                        OutboxEvent(
                            aggregate_type=AGGREGATE,
                            aggregate_id=name,
                            event_type=POST,
                            payload={
                                "connection_id": connection.id,
                                "kind": facts["kind"],
                                "subject_id": facts["subject_id"],
                            },
                        )
                    )
    except Exception:  # noqa: BLE001 - A notification never stops the change it reports.
        logger.warning("Chat regressions: could not queue a post", exc_info=True)


def _after_rollback(session: OrmSession) -> None:
    session.info.pop(_PENDING, None)


_installed = False


def install() -> None:
    """Watch every session for a regression (idempotent)."""
    global _installed
    if not _installed:
        event.listen(OrmSession, "after_flush", _after_flush)
        event.listen(OrmSession, "after_flush_postexec", _after_flush_postexec)
        event.listen(
            OrmSession, "after_soft_rollback", lambda s, _t: _after_rollback(s)
        )
        _installed = True


# ---------------------------------------------------------------------------
# Delivery (the outbox processor)
# ---------------------------------------------------------------------------


def _delivered_already(session: Session, name: str, delivery_id: int) -> bool:
    return (
        session.exec(
            select(OutboxEvent.id).where(
                OutboxEvent.aggregate_type == AGGREGATE,
                OutboxEvent.aggregate_id == name,
                OutboxEvent.event_type == POST,
                OutboxEvent.status == "completed",
                OutboxEvent.id != delivery_id,
            )
        ).first()
        is not None
    )


async def _post(connection: ChatConnection, facts: dict[str, Any]) -> None:
    credential = chat_connections.credential_of(connection)
    if connection.provider == "slack":
        await chat_client.slack_call(
            "chat.postMessage",
            credential,
            {
                "channel": connection.channel,
                "text": messages.fallback_text(facts),
                "blocks": messages.slack_blocks(facts),
            },
            chat_connections.TIMEOUT_SECONDS,
        )
        return
    url = await asyncio.to_thread(chat_connections.check_teams_url, credential)
    status = await chat_client.teams_post(
        url, messages.teams_card(facts), chat_connections.TIMEOUT_SECONDS
    )
    if not 200 <= status < 300:
        raise chat_client.ChatPostError(f"Teams answered HTTP {status}")


async def deliver(session: Session, payload: dict[str, Any], delivery_id: int) -> None:
    """Post one queued regression; raises on failure so the outbox retries it."""
    connection = session.get(ChatConnection, payload.get("connection_id"))
    if connection is None or not connection.enabled:
        return  # removed or turned off since: nothing to send, nothing to retry
    kind = str(payload.get("kind") or "")
    subject_id = int(payload.get("subject_id") or 0)
    if _delivered_already(session, key(connection.id, kind, subject_id), delivery_id):
        return
    facts = regression_of(session, kind, subject_id)
    if facts is None or facts["company_id"] != connection.company_id:
        return
    try:
        await _post(connection, facts)
    except (chat_client.ChatPostError, chat_connections.InvalidConnection) as exc:
        chat_connections.record(connection, "error", f"{POST} #{delivery_id}: {exc}")
        session.add(connection)
        raise
    chat_connections.record(connection, "ok", None)
    session.add(connection)
