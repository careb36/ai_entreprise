"""The job of ``self_host_container_run`` (issue #701, ``docs/design/owner-actions.md``).

Runs the self-hosting harness's steps ``checkout, company, records, issue,
wait, sandbox`` with ``--container-mode`` from inside the backend, as the
Owner who started it, and records what the platform observed:

- ``checkout`` goes through the daemon (``workspace.self_host.checkout``):
  the daemon copies its own source into a folder under its own root; the
  backend sends only the folder's name and keeps the daemon's reply.
- The other steps are the harness's own functions (``scripts/self_host``),
  called with ``InProcessApi`` (no token, no socket) and, for ``records``,
  a reader of the snapshot that asks the daemon (``self_host_snapshot``).
- Before every API call the job checks the cancel request and, at most every
  ``BUDGET_CHECK_SECONDS``, the tokens the platform recorded for the
  self-hosting company since the run began (audit rows' ``cost_tokens``, the
  proxy's usage, never an agent's report). Over ``token_budget``, or on
  cancel, it stops the sprint it started and finishes the run.
- Right after ``company``, before any approval or model call, it reads the
  company's segregation-of-duties policy (``owner_action_sod.py``): when the
  rule would refuse an approval the harness performs as the Owner, the run
  stops ``blocked_by_sod`` with the facts in ``observations.sod``. It never
  grants, changes or bypasses the policy.

The run's observations are ids the platform's API returned, the daemon's
replies, the backend's clock and the exit status computed here: never a
producer's claim (POSITIONING rules 1, 2).
"""

from __future__ import annotations

import argparse
import asyncio
import logging
import threading
import time
from collections.abc import Callable
from dataclasses import dataclass, field
from datetime import datetime
from typing import Any

from models import AuditLog, OwnerActionRun
from sqlalchemy import func
from sqlmodel import Session, select

from services.management import owner_action_sod, owner_actions, self_host_snapshot
from services.management.in_process_api import InProcessApi
from services.platform.in_process_principal import InProcessPrincipal
from services.workspace import workspace_daemon

logger = logging.getLogger(__name__)

STEPS = "company,records,issue,wait"  # checkout first, through the daemon; sandbox added by --container-mode
BUDGET_CHECK_SECONDS = 15.0
DAEMON_SECONDS = 60.0
EXIT_API_ERROR = 1


class Stop(Exception):
    """The run stops between two calls: ``reason`` is ``cancelled`` or ``budget_exceeded``."""

    def __init__(self, reason: str, tokens: int | None = None):
        super().__init__(reason)
        self.reason, self.tokens = reason, tokens


@dataclass
class Job:
    """Everything one run needs; the tests build it with fakes."""

    run_id: int
    company_id: int
    user_id: int
    params: dict[str, Any]
    started_at: datetime
    session_factory: Callable[[], Session]
    daemon: Callable[[str, dict[str, Any], float], Any]  # async request -> reply
    make_api: Callable[[Callable[[], None]], Any]  # before_call -> Api
    harness: Any  # the self_host module
    loop: asyncio.AbstractEventLoop | None = None
    clock: Callable[[], float] = time.monotonic
    state: dict[str, Any] = field(default_factory=dict)
    report: dict[str, Any] = field(default_factory=dict)
    _checked_at: float = -1e9

    # -- the checks before each call ------------------------------------------

    def tokens_observed(self) -> int:
        cid = self.state.get("company_id")
        if not cid:
            return 0
        with self.session_factory() as session:
            total = session.exec(
                select(func.coalesce(func.sum(AuditLog.cost_tokens), 0)).where(
                    AuditLog.company_id == cid, AuditLog.created_at >= self.started_at
                )
            ).one()
        return int(total or 0)

    def before_call(self) -> None:
        with self.session_factory() as session:
            if owner_actions.cancel_requested(session, self.run_id):
                raise Stop("cancelled")
        now = self.clock()
        if now - self._checked_at >= BUDGET_CHECK_SECONDS:
            self._checked_at = now
            tokens = self.tokens_observed()
            if tokens >= int(self.params["token_budget"]):
                raise Stop("budget_exceeded", tokens)

    # -- daemon calls from the harness's thread --------------------------------

    def daemon_sync(self, subject: str, payload: dict[str, Any]) -> dict:
        assert self.loop is not None
        future = asyncio.run_coroutine_threadsafe(
            self.daemon(subject, payload, DAEMON_SECONDS), self.loop
        )
        return future.result(timeout=DAEMON_SECONDS + 5)

    # -- the steps -------------------------------------------------------------

    def args(self, project_path: str) -> argparse.Namespace:
        parser = argparse.ArgumentParser()
        args = argparse.Namespace(
            checkout=project_path,
            project_path=project_path,
            steps=STEPS,
            sandbox=None,
            profile=None,
            test_command=None,
            container_mode=True,
            autonomy=2,
            scope=self.params["scope"],
            timeout=int(self.params["timeout_seconds"]),
            spec=None,
            source=self.harness.spec_source.load(None, None),
            snapshot=self_host_snapshot.DaemonSnapshot(self.daemon_sync, project_path),
        )
        args.steps_to_run = self.harness.sandbox_mode.apply(
            parser, args, self.harness.STEPS
        )
        return args

    def observations(self) -> dict[str, Any]:
        r = self.report
        records = r.get("records") or {}
        issue = r.get("issue") or {}
        wait = r.get("wait") or {}
        out: dict[str, Any] = {
            "checkout": r.get("checkout"),
            "company_id": self.state.get("company_id"),
            "company_created": (r.get("company") or {}).get("created"),
            "sandbox_asked": (r.get("company") or {}).get("sandbox"),
            "specification_id": records.get("specification_id"),
            "architecture_id": records.get("architecture_id"),
            "records_document_matches": records.get("document_matches"),
            "records_written_unchanged_in_git": records.get("written_unchanged_in_git"),
            "issue_specification_id": issue.get("specification_id"),
            "issue_architecture_id": issue.get("architecture_id"),
            "sprint_id": self.state.get("sprint_id"),
            "sprint_status": wait.get("status"),
            "integration_state": wait.get("integration_state"),
            "sandbox": r.get("sandbox"),
            "steps_done": [s for s in r.get("steps_done", [])],
            "state": dict(self.state),
        }
        if "sod" in r:
            out["sod"] = r["sod"]
        if "agent_run_ids" in r:
            out["agent_run_ids"] = r["agent_run_ids"]
        return out

    def save(self) -> None:
        with self.session_factory() as session:
            owner_actions.observe(session, self.run_id, self.observations())

    def run_steps(self, api: Any, args: argparse.Namespace) -> None:
        """In the worker thread: the harness's steps, saved after each."""
        h = self.harness
        for step in args.steps_to_run:
            getattr(h, f"step_{step}")(api, args, self.state, self.report)
            self.report.setdefault("steps_done", []).append(step)
            if (
                step == "issue"
                and self.params.get("urgent")
                and self.state.get("sprint_id")
            ):
                api.put(
                    f"/v1/sprints/{self.state['sprint_id']}/urgent", {"urgent": True}
                )
                self.report["urgent"] = True
            self.save()
            if step == "company":
                self.check_duties()
        sid, cid = self.state.get("sprint_id"), self.state.get("company_id")
        if sid and cid:
            runs = api.get(f"/v1/agent_runs?company_id={cid}&sprint_id={sid}&limit=500")
            self.report["agent_run_ids"] = sorted(r["id"] for r in runs["agent_runs"])
            self.save()

    def check_duties(self) -> None:
        """Right after the run has its company, before any approval or model
        call: stop ``blocked_by_sod`` when the company's policy would refuse
        an approval the harness performs as the Owner. Reads the policy only;
        never grants, changes or bypasses it."""
        with self.session_factory() as session:
            seen = owner_action_sod.situation(
                session, self.state.get("company_id"), self.user_id
            )
        self.report["sod"] = seen
        if seen["refused"]:
            raise owner_action_sod.Blocked(seen)

    def stop_sprint(self, api: Any) -> None:
        sid = self.state.get("sprint_id")
        if not sid:
            return
        try:
            api.post(f"/v1/sprints/{sid}/stop")
            self.report["sprint_stopped"] = True
        except Exception as exc:  # noqa: BLE001 - recorded, the run still finishes.
            self.report["sprint_stop_error"] = str(exc)[:300]


# The company a run with the same checkout name created (kept here by name).
previous_state = owner_action_sod.previous_state


async def run(job: Job) -> OwnerActionRun | None:
    """The whole run; always finishes the run's record."""
    job.loop = asyncio.get_running_loop()
    status, exit_status, reason, tokens = "failed", None, None, None
    api = job.make_api(job.before_call)
    try:
        job.before_call()
        reply = await job.daemon(
            self_host_snapshot.CHECKOUT,
            {"name": job.params["checkout_name"]},
            owner_actions.CHECKOUT_SECONDS,
        )
        job.report["checkout"] = {
            k: reply.get(k)
            for k in (
                "path",
                "created",
                "refreshed",
                "previous_head",
                "head",
                "source_head",
                "files",
            )
        }
        job.report["steps_done"] = ["checkout"]
        await asyncio.to_thread(job.save)
        args = await asyncio.to_thread(job.args, str(reply["path"]))
        await asyncio.to_thread(job.run_steps, api, args)
        exit_status = job.harness.sandbox_mode.exit_status(job.report, True)
        status = "succeeded" if exit_status == 0 else "failed"
        reason = None if exit_status == 0 else "not_container_mode"
    except owner_action_sod.Blocked:
        reason = owner_action_sod.BLOCKED  # the facts are in ``observations.sod``
    except Stop as stop:
        status = "cancelled" if stop.reason == "cancelled" else "failed"
        reason, tokens = stop.reason, stop.tokens
        await asyncio.to_thread(job.stop_sprint, job.make_api(lambda: None))
    except job.harness.ApiError as exc:
        exit_status, reason = EXIT_API_ERROR, f"api_error: {str(exc)[:300]}"
    except workspace_daemon.DaemonUnavailable as exc:
        reason = f"daemon_unavailable: {str(exc)[:300]}"
    except workspace_daemon.DaemonError as exc:
        reason = f"daemon_refused ({exc.status}): {exc.message[:300]}"
    except Exception as exc:
        logger.exception("owner action run %s failed", job.run_id)
        reason = f"error: {type(exc).__name__}: {str(exc)[:300]}"
    observed = job.observations()
    observed["tokens_observed"] = (
        tokens if tokens is not None else await asyncio.to_thread(job.tokens_observed)
    )
    observed["exit_status"] = exit_status
    observed["sprint_stopped"] = job.report.get("sprint_stopped", False)
    observed["urgent"] = job.report.get("urgent", False)
    with job.session_factory() as session:
        return owner_actions.finish(
            session, job.run_id, status, exit_status, reason, observed
        )


# --------------------------------------------------------------------------- launch

_TASKS: set[asyncio.Task] = set()
_LIVE: set[int] = set()
_LIVE_LOCK = threading.Lock()


def live(run_id: int) -> bool:
    with _LIVE_LOCK:
        return run_id in _LIVE


def launch(
    app: Any, nats_client: Any, run: OwnerActionRun, engine: Any
) -> asyncio.Task:
    """Start *run*'s job on the running loop (``POST …/runs``)."""
    folder = self_host_snapshot.harness_dir()
    if folder is None:
        raise owner_actions.RunRefused(503, "The self-hosting harness is not available")
    harness = self_host_snapshot.load_harness(folder)
    principal = InProcessPrincipal(
        user_id=run.started_by_user_id,
        run_id=int(run.id or 0),
        live=lambda: live(int(run.id or 0)),
    )
    loop = asyncio.get_running_loop()

    async def daemon(subject: str, payload: dict[str, Any], timeout: float) -> Any:
        return await workspace_daemon.daemon_request(
            nats_client, subject, payload, timeout
        )

    def session_factory() -> Session:
        return Session(engine)

    with session_factory() as session:
        state = previous_state(session, run.action, (run.params or {})["checkout_name"])
    job = Job(
        run_id=int(run.id or 0),
        company_id=run.company_id,
        user_id=run.started_by_user_id,
        params=dict(run.params or {}),
        started_at=run.started_at,
        session_factory=session_factory,
        daemon=daemon,
        make_api=lambda before: InProcessApi(
            app, principal, loop, harness.ApiError, before
        ),
        harness=harness,
        state=state,
    )

    async def body() -> None:
        with _LIVE_LOCK:
            _LIVE.add(job.run_id)
        try:
            await run_job(job)
        finally:
            with _LIVE_LOCK:
                _LIVE.discard(job.run_id)

    task = loop.create_task(body())
    _TASKS.add(task)
    task.add_done_callback(_TASKS.discard)
    return task


async def run_job(job: Job) -> OwnerActionRun | None:
    return await run(job)
