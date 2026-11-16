"""A sprint's integration in the background (issue #314).

A closed sprint is integrated on its own when the Git workflow is on, through
the ``sprint.closed`` hook; a daemon absent at close leaves a pending job that
is retried, a refusal an error for a person; the job is kept before the daemon
is asked, two attempts integrate once, a sprint another replica holds is left
to it (an advisory lock on Postgres, from a pool of its own), integrations run
within the concurrency limit and each records the commit it tested.

These tests run real git in ``tmp_path``, with a bare repository standing in
for the remote; the shared fakes and rows are in ``sprint_integration_helpers``.
"""

import asyncio
import contextlib
import sys
import threading
import weakref
from pathlib import Path

import pytest
from models.integration import SprintIntegration
from services.loop import replicas, sprint_close, task_worker
from services.loop.worker import close as worker_close
from services.platform import hooks, locks
from services.release import integration, integration_jobs
from sqlmodel import Session, select

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "vscode-daemon"))
import sprint_integration
import sprint_integration_helpers
import worker_stages
from sprint_integration_helpers import _Daemon, _deliver, _sprint, _two_done_tasks
from test_git_workflow import git

# The shared fixtures.
published_repo = sprint_integration_helpers.published_repo
suite = sprint_integration_helpers.suite


def test_a_closed_sprint_is_integrated_on_its_own_when_the_workflow_is_on(
    sqlite_session, published_repo, suite
):
    root, _, _ = published_repo
    _, sprint, *_ = _two_done_tasks(sqlite_session, root)
    _, off = _sprint(sqlite_session, root, on=False)
    engine = sqlite_session.get_bind()
    nats = _Daemon()

    async def close_both():
        integration_jobs.schedule_after_close(nats, engine, off.id)
        integration_jobs.schedule_after_close(nats, engine, sprint.id)
        await asyncio.gather(*list(integration_jobs._RUNNING))

    asyncio.run(close_both())

    with Session(engine) as session:
        rows = session.exec(select(SprintIntegration)).all()
    [row] = [r for r in rows if r.sprint_id in (sprint.id, off.id)]
    assert (row.sprint_id, row.trigger, row.status) == (
        sprint.id,
        "sprint_closed",
        "tests_passed",
    )
    assert row.requested_by == "task_worker"


@pytest.mark.parametrize("reason", ["sprint_completed", sprint_close.RECLOSED])
def test_the_loop_closing_a_sprint_emits_sprint_closed_and_release_integrates_it(
    sqlite_session, published_repo, suite, monkeypatch, reason
):
    """The loop never calls release: it emits ``sprint.closed`` at the close,
    a first one or one after a reopening, and release's handler starts the
    integration in the background, only with the Git workflow on."""
    root, _, _ = published_repo
    _, sprint, *_ = _two_done_tasks(sqlite_session, root)
    _, off = _sprint(sqlite_session, root, on=False)
    engine = sqlite_session.get_bind()
    nats = _Daemon()
    emitted = []

    def _record(**payload):
        emitted.append(payload)

    worker_stages.use_engine(monkeypatch, engine)
    monkeypatch.setattr(worker_close, "_build_sprint_retrospective", lambda _id: None)
    monkeypatch.setattr(
        replicas,
        "close_once",
        lambda _engine, _sprint_id, _close, _retro: {
            "completed": True,
            "reason": reason,
        },
    )
    assert integration_jobs.on_sprint_closed in hooks.handlers(hooks.SPRINT_CLOSED)
    hooks.on(hooks.SPRINT_CLOSED, _record)

    async def close_both():
        await task_worker._finalize_sprint_if_ready(nats, off.id)
        await task_worker._finalize_sprint_if_ready(nats, sprint.id)
        await asyncio.gather(*list(integration_jobs._RUNNING))

    try:
        asyncio.run(close_both())
    finally:
        hooks.off(hooks.SPRINT_CLOSED, _record)

    assert emitted == [
        {"nats_client": nats, "engine": engine, "sprint_id": off.id},
        {"nats_client": nats, "engine": engine, "sprint_id": sprint.id},
    ]
    with Session(engine) as session:
        rows = session.exec(select(SprintIntegration)).all()
    [row] = [r for r in rows if r.sprint_id in (sprint.id, off.id)]
    assert (row.sprint_id, row.trigger, row.status) == (
        sprint.id,
        "sprint_closed",
        "tests_passed",
    )


def test_a_sprint_closed_handler_that_fails_never_stops_the_others(monkeypatch):
    calls, scheduled = [], []

    def _fails(**payload):
        raise RuntimeError("down")

    def _records(**payload):
        calls.append(payload)

    monkeypatch.setattr(hooks, "_HANDLERS", {})
    hooks.on(hooks.SPRINT_CLOSED, _fails)
    hooks.on(hooks.SPRINT_CLOSED, _records)
    hooks.on(hooks.SPRINT_CLOSED, _records)  # registered once
    integration_jobs.install()
    integration_jobs.install()
    assert hooks.handlers(hooks.SPRINT_CLOSED) == [
        _fails,
        _records,
        integration_jobs.on_sprint_closed,
    ]
    monkeypatch.setattr(
        integration_jobs, "schedule_after_close", lambda *a: scheduled.append(a)
    )

    hooks.emit(hooks.SPRINT_CLOSED, nats_client="n", engine="e", sprint_id=7)

    assert calls == [{"nats_client": "n", "engine": "e", "sprint_id": 7}]
    assert scheduled == [("n", "e", 7)]


class _RacingDaemon(_Daemon):
    """While the first suite runs, another integration moves the sprint branch."""

    def __init__(self, root, task_id):
        super().__init__()
        self.root, self.task_id, self.heads = root, task_id, []

    async def request(self, subject, payload, timeout=5.0):
        if subject == "workspace.execute":
            if not self.heads:
                # Another integration moves the sprint branch and its worktree first.
                _deliver(self.root, self.task_id, {"a2.py": "A2 = 2\n"})
                branch = self.integrate_payload["branch"]
                sprint_integration.integrate(str(self.root), self.integrate_payload)
                self.moved_to = git(self.root, "rev-parse", branch)
            self.heads.append(git(Path(payload["workspace_root"]), "rev-parse", "HEAD"))
            await asyncio.sleep(0)
        if subject == "workspace.git.integrate":
            self.integrate_payload = payload
        return await super().request(subject, payload, timeout)


def test_concurrent_integrations_each_record_the_commit_they_tested(
    sqlite_session, published_repo, suite
):
    root, _, _ = published_repo
    _, sprint, first, *_ = _two_done_tasks(sqlite_session, root)
    engine = sqlite_session.get_bind()
    nats = _RacingDaemon(root, first.id)

    async def twice():
        return await asyncio.gather(
            *(
                integration.integrate(
                    nats, engine, sprint.id, trigger="manual", requested_by="human:A"
                )
                for _ in range(2)
            )
        )

    rows = asyncio.run(twice())

    # The first attempt tested its own commit, not where the branch was moved to.
    for row, head in zip(rows, nats.heads, strict=True):
        assert row.tested_commit == row.commit == head
    assert rows[0].commit != nats.moved_to == rows[1].commit


def test_an_integration_the_daemon_missed_at_close_is_pending_and_retried(
    sqlite_session, test_client, published_repo, suite, monkeypatch
):
    root, _, _ = published_repo
    company, sprint, *_ = _two_done_tasks(sqlite_session, root)
    engine = sqlite_session.get_bind()
    nats = _Daemon()
    nats.offline = True

    async def close_then_retry():
        integration_jobs.schedule_after_close(nats, engine, sprint.id)
        await asyncio.gather(*list(integration_jobs._RUNNING))
        return await integration_jobs.retry_pending(nats, engine)

    assert asyncio.run(close_then_retry()) == 1  # still offline: tried again
    with Session(engine) as session:
        pending = integration.latest(session, sprint.id)
        assert (pending.status, pending.details["attempts"]) == ("pending", 2)
        assert pending.commit is None
    inbox = test_client.get("/v1/inbox", params={"company_id": company.id}).json()
    assert [(i["status"], i["reason"]) for i in inbox["integrations"]] == [
        ("pending", "the workspace daemon did not answer")
    ]
    refused = test_client.post(f"/v1/sprints/{sprint.id}/integration/publish")
    assert refused.status_code == 409

    nats.offline = False
    assert asyncio.run(integration_jobs.retry_pending(nats, engine)) == 1
    with Session(engine) as session:
        done = integration.latest(session, sprint.id)
    assert (done.status, done.trigger) == ("tests_passed", "sprint_closed")
    assert asyncio.run(integration_jobs.retry_pending(nats, engine)) == 0
    inbox = test_client.get("/v1/inbox", params={"company_id": company.id}).json()
    assert inbox["integrations"] == []


def test_a_daemon_that_refuses_at_close_leaves_an_error_for_a_person(
    sqlite_session, test_client, published_repo, suite, monkeypatch
):
    root, _, _ = published_repo
    company, sprint, *_ = _two_done_tasks(sqlite_session, root)
    engine = sqlite_session.get_bind()

    class _Refusing(_Daemon):
        async def request(self, subject, payload, timeout=5.0):
            return {"error": {"status": 409, "message": "fatal: bad base"}}

    async def close():
        integration_jobs.schedule_after_close(_Refusing(), engine, sprint.id)
        await asyncio.gather(*list(integration_jobs._RUNNING))

    asyncio.run(close())

    inbox = test_client.get("/v1/inbox", params={"company_id": company.id}).json()
    assert [(i["status"], i["reason"]) for i in inbox["integrations"]] == [
        ("error", "fatal: bad base")
    ]
    # An error is not retried on its own: a person integrates again.
    assert asyncio.run(integration_jobs.retry_pending(_Refusing(), engine)) == 0


def test_a_closed_sprints_integration_is_kept_before_the_daemon_is_asked(
    sqlite_session, published_repo, suite
):
    root, _, _ = published_repo
    _, sprint, *_ = _two_done_tasks(sqlite_session, root)
    engine = sqlite_session.get_bind()
    nats = _Daemon()

    async def close_then_die():
        integration_jobs.schedule_after_close(nats, engine, sprint.id)
        # The backend stops before the background attempt runs.
        for task in list(integration_jobs._RUNNING):
            task.cancel()
        await asyncio.gather(*list(integration_jobs._RUNNING), return_exceptions=True)

    asyncio.run(close_then_die())
    assert nats.requests == []
    with Session(engine) as session:
        job = integration.latest(session, sprint.id)
        assert (job.status, job.trigger, job.details["attempts"]) == (
            "running",
            "sprint_closed",
            0,
        )
        job_id = job.id

    # After the restart, the retry loop takes the job nobody holds.
    assert asyncio.run(integration_jobs.retry_pending(nats, engine)) == 1
    with Session(engine) as session:
        rows = session.exec(
            select(SprintIntegration).where(SprintIntegration.sprint_id == sprint.id)
        ).all()
    [row] = rows
    assert (row.id, row.status, row.details["attempts"]) == (job_id, "tests_passed", 1)
    assert asyncio.run(integration_jobs.retry_pending(nats, engine)) == 0


def test_two_attempts_at_one_job_integrate_once(sqlite_session, published_repo, suite):
    root, _, _ = published_repo
    _, sprint, *_ = _two_done_tasks(sqlite_session, root)
    engine = sqlite_session.get_bind()
    nats = _Daemon()

    async def twice():
        integration_jobs.schedule_after_close(nats, engine, sprint.id)
        tried = await integration_jobs.retry_pending(nats, engine)
        await asyncio.gather(*list(integration_jobs._RUNNING))
        tried += await integration_jobs.retry_pending(nats, engine)
        return tried

    assert asyncio.run(twice()) == 1
    assert [s for s, _ in nats.requests].count("workspace.git.integrate") == 1


def test_a_sprint_another_replica_integrates_is_left_to_it(
    sqlite_session, test_client, published_repo, suite, monkeypatch
):
    root, _, _ = published_repo
    _, sprint, *_ = _two_done_tasks(sqlite_session, root)
    engine = sqlite_session.get_bind()
    nats = _Daemon()
    monkeypatch.setattr(test_client.app.state, "nats_client", nats)

    @contextlib.asynccontextmanager
    async def held_elsewhere(engine, sprint_id):
        yield False

    monkeypatch.setattr(integration, "_database_lock", held_elsewhere)
    refused = test_client.post(f"/v1/sprints/{sprint.id}/integrate")
    assert refused.status_code == 409 and "already running" in refused.json()["detail"]

    async def close():
        integration_jobs.schedule_after_close(nats, engine, sprint.id)
        await asyncio.gather(*list(integration_jobs._RUNNING))
        return await integration_jobs.retry_pending(nats, engine)

    assert asyncio.run(close()) == 0
    assert nats.requests == []
    with Session(engine) as session:
        assert integration.latest(session, sprint.id).status == "running"


class _Conn:
    def __init__(self, owned):
        self.owned, self.sql, self.threads = owned, [], set()

    def execute(self, statement, params):
        self.threads.add(threading.get_ident())
        self.sql.append((str(statement), params))
        return type("R", (), {"scalar": lambda _self: self.owned})()

    def commit(self):
        return None

    def close(self):
        return None

    def invalidate(self):
        raise AssertionError("not expected")


@pytest.mark.parametrize("owned", [True, False])
def test_on_postgres_the_sprint_is_held_with_an_advisory_lock_off_the_loop(
    owned, monkeypatch
):
    conn = _Conn(owned)
    engine = type(
        "E",
        (),
        {"dialect": type("D", (), {"name": "postgresql"})(), "connect": lambda _: conn},
    )()
    monkeypatch.setattr(integration, "_lock_engine", lambda _engine: engine)

    async def hold():
        async with integration._database_lock(engine, 42) as got:
            return got, threading.get_ident()

    got, loop_thread = asyncio.run(hold())

    assert got is owned
    calls = [sql.split("(")[0] for sql, _ in conn.sql]
    expected = ["SELECT pg_try_advisory_lock"] + (
        ["SELECT pg_advisory_unlock"] if owned else []
    )
    assert calls == expected
    key = locks.lock_key(locks.SPRINT_INTEGRATION, 42)
    assert all(p["k"] == key for _, p in conn.sql)
    # Taken and released in a thread: a checkout never blocks the event loop.
    assert loop_thread not in conn.threads


def test_lock_connections_come_from_a_small_pool_of_their_own():
    from sqlalchemy.engine import make_url

    app = type(
        "E",
        (),
        {
            "dialect": type("D", (), {"name": "postgresql"})(),
            "url": make_url("postgresql://u:p@db.invalid/fabrique"),
        },
    )()

    lock_engine = integration._lock_engine(app)

    assert lock_engine is integration._lock_engine(app)
    assert lock_engine.pool.size() == integration.MAX_CONCURRENT
    assert lock_engine.pool._max_overflow == 0
    assert lock_engine.url.password == "p"
    # SQLite has no advisory locks: the same engine, never a pool.
    sqlite = type("E", (), {"dialect": type("D", (), {"name": "sqlite"})()})()
    assert integration._lock_engine(sqlite) is sqlite


class _CountingDaemon(_Daemon):
    def __init__(self):
        super().__init__()
        self.in_flight = self.most = 0

    async def request(self, subject, payload, timeout=5.0):
        self.in_flight += 1
        self.most = max(self.most, self.in_flight)
        try:
            await asyncio.sleep(0.05)
            return await super().request(subject, payload, timeout)
        finally:
            self.in_flight -= 1


def test_integrations_of_different_sprints_run_within_the_limit(
    sqlite_session, published_repo, suite, monkeypatch
):
    root, _, _ = published_repo
    sprints = [_two_done_tasks(sqlite_session, root)[1] for _ in range(3)]
    engine = sqlite_session.get_bind()
    monkeypatch.setattr(integration, "MAX_CONCURRENT", 1)
    monkeypatch.setattr(integration, "_SLOTS", weakref.WeakKeyDictionary())
    nats = _CountingDaemon()

    async def all_at_once():
        return await asyncio.gather(
            *(
                integration.integrate(
                    nats, engine, s.id, trigger="manual", requested_by="human:A"
                )
                for s in sprints
            )
        )

    rows = asyncio.run(all_at_once())

    assert [r.status for r in rows] == ["tests_passed"] * 3
    assert nats.most == 1
    attempts = [
        p["attempt"] for s, p in nats.requests if s == "workspace.git.integrate"
    ]
    assert len(set(attempts)) == 3
    assert all(r.test_run["workspace_root"].endswith(a) for r, a in zip(rows, attempts))
