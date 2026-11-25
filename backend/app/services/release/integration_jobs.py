"""A closed sprint's integration as a job that survives a restart (issue #314).

When a sprint closes the loop emits ``sprint.closed`` (``platform.hooks``),
and ``install`` registers ``on_sprint_closed`` for it in every process that
closes sprints. With the Git workflow on, ``schedule_after_close`` keeps
a ``running`` ``SprintIntegration`` before anything is asked of the daemon,
then integrates in the background (``services/release/integration.py``). The attempt
fills that row in place: with what the daemon answered, or as ``pending`` (no
daemon answered; tried again) or ``error`` (the daemon refused; a person
integrates again). ``retry_loop`` tries every sprint whose latest row is
``pending`` or ``running`` again: a ``running`` row nobody holds is an attempt
a restart cut short. Each attempt holds ``integration.sprint_lock`` (across
replicas, a Postgres advisory lock) and reads the row again once it holds
it, so two replicas never integrate the same sprint, and an attempt another
finished meanwhile is not repeated.
"""

from __future__ import annotations

import asyncio
from typing import Any

from config import settings
from logging_config import logger
from models import Company, Sprint
from models.integration import SprintIntegration
from sqlmodel import Session, select

from services.platform import hooks
from services.release import auto_publish, integration
from services.release.integration import (
    IntegrationRefused,
    _audit,
    attempt,
    integration_spec,
    latest,
    sprint_lock,
)
from services.workspace import workspace_daemon

# How often an integration the daemon could not take at close is tried again.
RETRY_SECONDS = float(settings.INTEGRATION_RETRY_SECONDS)
# Latest statuses that mean the sprint still has to be integrated on its own.
OPEN = ("pending", "running")
# The background integrations started when a sprint closed, kept until done.
_RUNNING: set[asyncio.Task] = set()


def _open_job(
    session: Session, sprint_id: int, *, starting: bool
) -> SprintIntegration | None:
    """The sprint's open job, kept before the daemon is asked; None when nothing is to integrate.

    *starting*: an attempt begins now (counted); otherwise the job is only kept.
    """
    row = latest(session, sprint_id)
    if row is not None and row.status in OPEN:
        if starting:
            details = dict(row.details or {})
            details["attempts"] = int(details.get("attempts") or 0) + 1
            row.status, row.details = "running", details
            session.add(row)
            session.commit()
            session.refresh(row)
        return row
    try:
        spec, context = integration_spec(session, sprint_id)
    except IntegrationRefused as refused:
        logger.info("Sprint %s not integrated: %s", sprint_id, refused.detail)
        return None
    job = SprintIntegration(
        company_id=context["company_id"],
        sprint_id=sprint_id,
        trigger="sprint_closed",
        requested_by="task_worker",
        status="running",
        branch=spec["branch"],
        base=spec["base"],
        tasks=[{**t, "state": "not_attempted"} for t in context["tasks"]],
        details={"attempts": int(starting), "left_out": context["left_out"]},
    )
    _audit(session, job, "sprint_integration_queued")
    return job


def _hold(engine: Any, job_id: int, status: str, reason: str) -> None:
    """The attempt ended without an answer to record: the job becomes pending or error."""
    with Session(engine) as session:
        job = session.get(SprintIntegration, job_id)
        if job is None:
            return
        details = dict(job.details or {})
        first = details.get("held") != status
        details.update(reason=reason, held=status)
        job.status, job.details = status, details
        if first:
            _audit(session, job, f"sprint_integration_{status}")
        else:
            session.add(job)
            session.commit()


async def _after_close(nats_client: Any, engine: Any, sprint_id: int) -> bool:
    """One attempt at the sprint's open job; whether it was attempted here."""
    lock = integration._LOCKS.get(sprint_id)
    if lock is not None and lock.locked():
        return False  # this process is integrating it now
    async with sprint_lock(engine, sprint_id) as owned:
        if not owned:
            return False  # another replica is
        with Session(engine) as session:
            current = latest(session, sprint_id)
            if current is None or current.status not in OPEN:
                return False  # finished meanwhile
            job = _open_job(session, sprint_id, starting=True)
            if job is None:
                return False
            session.expunge(job)
        try:
            await attempt(
                nats_client,
                engine,
                sprint_id,
                trigger="sprint_closed",
                requested_by="task_worker",
                job=job,
            )
        except IntegrationRefused as refused:
            _hold(engine, job.id, "error", refused.detail)
        except workspace_daemon.DaemonUnavailable as exc:
            # Kept as pending and tried again (retry_pending) until a daemon answers.
            logger.info("Sprint %s integration pending: %s", sprint_id, exc)
            _hold(engine, job.id, "pending", "the workspace daemon did not answer")
        except workspace_daemon.DaemonError as exc:
            _hold(engine, job.id, "error", exc.message)
        except Exception as exc:  # noqa: BLE001 - Integration never holds a closed sprint back.
            logger.warning("Integration of sprint %s failed: %s", sprint_id, exc)
            _hold(engine, job.id, "error", f"{type(exc).__name__}: {exc}"[:300])
            return True
    # Outside the sprint's lock: a passed integration opens its draft request.
    try:
        await auto_publish.after_integration(nats_client, engine, sprint_id)
    except Exception as exc:  # noqa: BLE001 - Publishing never holds an integration back.
        logger.warning("Draft request of sprint %s failed: %s", sprint_id, exc)
    return True


def schedule_after_close(nats_client: Any, engine: Any, sprint_id: int) -> None:
    """Keep the closed sprint's integration as a job, then run it in the background.

    Only when its company uses the Git workflow. The job is written before
    the background task starts, so a restart before the daemon answers still
    leaves it for ``retry_loop``.
    """
    try:
        with Session(engine) as session:
            sprint = session.get(Sprint, sprint_id)
            company = session.get(Company, sprint.company_id) if sprint else None
            if company is None or not company.git_workflow:
                return
            if _open_job(session, sprint_id, starting=False) is None:
                return
        task = asyncio.get_running_loop().create_task(
            _after_close(nats_client, engine, sprint_id)
        )
    except Exception as exc:  # noqa: BLE001 - Integration never holds a closed sprint back.
        logger.warning("Integration of sprint %s not started: %s", sprint_id, exc)
        return
    _RUNNING.add(task)
    task.add_done_callback(_RUNNING.discard)


def on_sprint_closed(*, nats_client: Any, engine: Any, sprint_id: int) -> None:
    """The ``sprint.closed`` handler: the closed sprint's integration (issue #314)."""
    schedule_after_close(nats_client, engine, sprint_id)


def install() -> None:
    """Start each closed sprint's integration from the loop's ``sprint.closed`` event."""
    hooks.on(hooks.SPRINT_CLOSED, on_sprint_closed)


async def retry_pending(nats_client: Any, engine: Any) -> int:
    """Try again each sprint whose latest integration is open; how many were tried here."""
    with Session(engine) as session:
        # An archived sprint's open integration waits until it is unarchived.
        rows = session.exec(
            select(SprintIntegration)
            .join(Sprint, Sprint.id == SprintIntegration.sprint_id)
            .where(Sprint.archived_at.is_(None))
            .order_by(SprintIntegration.created_at.desc(), SprintIntegration.id.desc())
        ).all()
        latest_of: dict[int, str] = {}
        for row in rows:
            latest_of.setdefault(row.sprint_id, row.status)
    tried = 0
    for sprint_id, status in latest_of.items():
        if status in OPEN:
            tried += await _after_close(nats_client, engine, sprint_id)
    return tried


async def retry_loop(nats_client: Any, engine: Any) -> None:
    """The background worker that retries open integrations every ``RETRY_SECONDS``."""
    while True:
        await asyncio.sleep(RETRY_SECONDS)
        try:
            await retry_pending(nats_client, engine)
        except Exception as exc:  # noqa: BLE001 - Keep retrying on the next round.
            logger.warning("Pending integrations not retried: %s", exc)
