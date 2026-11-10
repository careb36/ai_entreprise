"""
Two task workers against one database (issue #328).

Each test plays two replicas: two threads, or two calls, on a file-backed
SQLite database, so the conditional updates and the locks are exercised the
way a second process would meet them.

- A todo task is claimed by one worker only.
- A restarting worker returns to todo only the in-progress tasks no live
  worker holds, and a surviving worker's review cycle recovers and queues
  again the task of a worker that died while working on it.
- A sprint closes once: one close, one follow-up, one retrospective, one
  measurement of the procedures, one proposal round.
- An escalation gets one ScrumMaster triage.
- One review cycle, Support cycle and Reviewer cycle at a time.
"""

import asyncio
import os
import secrets
import threading
import time

import pytest
import worker_stages
from models.agent_message import AgentMessage
from models.company import Company
from models.sprint import BacklogItem, Sprint
from services import replicas
from services.learning import scrum_master
from services.loop.worker import close as worker_close
from services.loop.worker import execute as worker_execute
from services.loop.worker import messages as worker_messages
from services.loop.worker import procedures_round
from services.loop.worker import review as worker_review
from services.platform import locks
from services.tasks import task_events
from sqlmodel import Session, SQLModel, create_engine, select
from test_llm_routing import _task_assign_handler
from test_review_gate import _FakeNATS
from test_scrum_master import RETRY_ANSWER, TRIAGE


@pytest.fixture()
def engine(tmp_path, monkeypatch):
    engine = create_engine(
        f"sqlite:///{tmp_path / 'replicas.db'}",
        connect_args={"check_same_thread": False, "timeout": 30},
    )
    SQLModel.metadata.create_all(engine)
    worker_stages.use_engine(monkeypatch, engine)
    from memory import engram_processor

    def _no_graph():
        raise RuntimeError("no Neo4j in tests")

    monkeypatch.setattr(engram_processor, "get_engram_processor", _no_graph)
    return engine


def _company(engine, autonomy_level=2) -> int:
    with Session(engine) as session:
        company = Company(
            company_name=f"Replicas {secrets.token_hex(3)}",
            autonomy_level=autonomy_level,
        )
        session.add(company)
        session.commit()
        return company.id


def _sprint(engine, company_id, status="active", items=("done",)) -> int:
    with Session(engine) as session:
        sprint = Sprint(company_id=company_id, sprint_name="S", status=status)
        session.add(sprint)
        session.commit()
        for index, item_status in enumerate(items):
            session.add(
                BacklogItem(
                    sprint_id=sprint.id,
                    title=f"Task {index}",
                    status=item_status,
                    assignee="Developer",
                )
            )
        session.commit()
        return sprint.id


def _items(engine, sprint_id):
    with Session(engine) as session:
        return list(
            session.exec(
                select(BacklogItem)
                .where(BacklogItem.sprint_id == sprint_id)
                .order_by(BacklogItem.id)
            ).all()
        )


def _race(*calls):
    """Run each call in its own thread at once; return their results in order."""
    results = [None] * len(calls)
    barrier = threading.Barrier(len(calls))

    def run(index, call):
        barrier.wait()
        results[index] = call()

    threads = [threading.Thread(target=run, args=(i, c)) for i, c in enumerate(calls)]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join(timeout=30)
    return results


# --- the locks -----------------------------------------------------------


def test_a_lock_key_is_the_same_on_every_replica_and_names_differ():
    assert locks.lock_key("review-cycle") == locks.lock_key("review-cycle", 0)
    assert locks.lock_key("task", 1) != locks.lock_key("task", 2)
    assert locks.lock_key("task", 1) != locks.lock_key("triage", 1)
    assert 0 <= locks.lock_key("task", 1) < 2**63


def test_a_held_lock_is_refused_to_the_second_caller_and_released_after(engine):
    with (
        locks.held(engine, "x", 1) as first,
        locks.held(engine, "x", 1) as second,
        locks.held(engine, "x", 2) as other,
    ):
        assert (first, second, other) == (True, False, True)
    with locks.held(engine, "x", 1) as again:
        assert again


def test_run_exclusive_skips_while_another_replica_runs_the_cycle(engine):
    calls = []
    with locks.held(engine, locks.SUPPORT_AGENT):
        assert locks.run_exclusive(engine, locks.SUPPORT_AGENT, calls.append, 1) is None
    locks.run_exclusive(engine, locks.SUPPORT_AGENT, calls.append, 2)
    assert calls == [2]


def test_the_support_and_reviewer_loops_take_their_lock(engine, monkeypatch):
    from services import reviewer_agent, support_agent

    ran = []
    monkeypatch.setattr(support_agent, "triage_cycle", lambda e: ran.append("s"))
    monkeypatch.setattr(reviewer_agent, "review_cycle", lambda e: ran.append("r"))

    async def one_round(loop_fn):
        task = asyncio.create_task(loop_fn(engine))
        await asyncio.sleep(0.2)
        task.cancel()

    with (
        locks.held(engine, locks.SUPPORT_AGENT),
        locks.held(engine, locks.REVIEWER_AGENT),
    ):
        asyncio.run(one_round(support_agent.run_support_agent))
        asyncio.run(one_round(reviewer_agent.run_reviewer_agent))
    assert ran == []
    asyncio.run(one_round(support_agent.run_support_agent))
    assert ran == ["s"]


# --- claiming a task --------------------------------------------------------


def test_two_workers_claim_one_todo_task_once(engine):
    sprint_id = _sprint(engine, _company(engine), items=("todo",))
    item_id = _items(engine, sprint_id)[0].id

    results = _race(
        lambda: replicas.claim_todo(engine, item_id),
        lambda: replicas.claim_todo(engine, item_id),
    )

    assert sorted(results) == [False, True]
    assert _items(engine, sprint_id)[0].status == "in_progress"
    assert replicas.claim_todo(engine, item_id) is False


def test_two_workers_given_one_task_run_it_once(engine, monkeypatch):
    """Two assignments of one task reach two workers at once: one runs it."""
    sprint_id = _sprint(engine, _company(engine), items=("todo",))
    item_id = _items(engine, sprint_id)[0].id
    executed = []

    def slow_execute(*args, **kwargs):
        executed.append(args)
        time.sleep(0.3)
        raise RuntimeError("stop here")

    monkeypatch.setattr(worker_execute, "_execute_task", slow_execute)
    first = _task_assign_handler(monkeypatch).handler
    second = _task_assign_handler(monkeypatch).handler
    message = {"backlog_item_id": item_id, "sprint_id": sprint_id}

    async def both():
        await asyncio.gather(
            first(None, dict(message)),
            second(None, dict(message)),
            return_exceptions=True,
        )

    asyncio.run(both())

    assert len(executed) == 1


# --- recovery after a restart ----------------------------------------------


def test_a_restarting_worker_leaves_the_tasks_another_worker_holds(engine):
    sprint_id = _sprint(engine, _company(engine), items=("in_progress", "in_progress"))
    held_item, orphan = _items(engine, sprint_id)

    with replicas.task_lock(engine, held_item.id) as mine:
        assert mine
        recovered = replicas.recover_inflight(engine)

    assert [r["id"] for r in recovered] == [orphan.id]
    statuses = {i.id: i.status for i in _items(engine, sprint_id)}
    assert statuses == {held_item.id: "in_progress", orphan.id: "todo"}
    assert "Worker recovery" in _items(engine, sprint_id)[1].comments[-1]["text"]


def test_the_claim_and_the_recovery_are_task_events(engine):
    """Every transition is an event first (issue #324), also under several replicas."""
    sprint_id = _sprint(engine, _company(engine), items=("todo", "in_progress"))
    claimed, frozen = _items(engine, sprint_id)
    with Session(engine) as session:
        item = session.get(BacklogItem, frozen.id)
        item.comments = [
            {"author": "System", "text": "Freeze after sleep-induced churn"}
        ]
        session.add(item)
        session.commit()

    assert replicas.claim_todo(engine, claimed.id)
    assert replicas.recover_inflight(engine)[0]["id"] == claimed.id

    with Session(engine) as session:
        kinds = [
            (e.event_type, e.task_status)
            for e in task_events.for_task(session, claimed.id)
        ]
        assert kinds == [("claimed", "in_progress"), ("recovered", "todo")]
        item = session.get(BacklogItem, claimed.id)
        events = task_events.for_task(session, claimed.id)
        assert item.comments[-1]["event_id"] == events[-1].id
        assert item.comments[-1]["text"] == replicas.RECOVERY_NOTE
        assert [
            (e.event_type, e.task_status)
            for e in task_events.for_task(session, frozen.id)
        ] == [("blocked", "blocked")]
        assert session.get(BacklogItem, frozen.id).status == "blocked"


def test_a_surviving_worker_recovers_the_task_of_one_that_died(engine, monkeypatch):
    """A worker dies mid-task; the survivor acks the redelivery, then its cycle recovers it."""
    sprint_id = _sprint(engine, _company(engine), items=("todo",))
    item_id = _items(engine, sprint_id)[0].id
    executed = []

    def dies(*args, **kwargs):
        executed.append("dead")
        # Not an Exception: nothing marks the task blocked, as when the process is killed.
        raise asyncio.CancelledError

    monkeypatch.setattr(worker_execute, "_execute_task", dies)
    dead = _task_assign_handler(monkeypatch).handler
    message = {"backlog_item_id": item_id, "sprint_id": sprint_id}
    with pytest.raises(asyncio.CancelledError):
        asyncio.run(dead(None, dict(message)))
    assert executed == ["dead"]
    assert _items(engine, sprint_id)[0].status == "in_progress"

    def runs(*args, **kwargs):
        executed.append("survivor")
        raise RuntimeError("stop here")

    monkeypatch.setattr(worker_execute, "_execute_task", runs)
    survivor = _task_assign_handler(monkeypatch).handler
    # The redelivered assignment finds the task in progress and is skipped.
    asyncio.run(survivor(None, dict(message)))
    assert executed == ["dead"]

    nats = _FakeNATS()
    asyncio.run(worker_review._review_cycle(nats))

    item = _items(engine, sprint_id)[0]
    assert item.status == "todo"
    assert "Worker recovery" in item.comments[-1]["text"]
    assigned = [p for subject, p in nats.published if subject == "task.assign"]
    assert [p["backlog_item_id"] for p in assigned] == [item_id]
    assert any(
        p.get("semantic_type") == "task.recovered"
        for subject, p in nats.published
        if subject == f"glassroom.{sprint_id}"
    )

    asyncio.run(survivor(None, dict(assigned[0])))
    assert executed == ["dead", "survivor"]


def test_a_review_cycle_leaves_the_task_a_live_worker_holds(engine):
    sprint_id = _sprint(engine, _company(engine), items=("in_progress",))
    item_id = _items(engine, sprint_id)[0].id
    nats = _FakeNATS()
    with replicas.task_lock(engine, item_id):
        asyncio.run(worker_review._review_cycle(nats))
    assert _items(engine, sprint_id)[0].status == "in_progress"
    assert not [p for subject, p in nats.published if subject == "task.assign"]


# --- closing a sprint -------------------------------------------------------


def test_a_close_that_read_active_loses_to_the_one_that_closed_first(engine):
    sprint_id = _sprint(engine, _company(engine))
    with Session(engine) as first, Session(engine) as second:
        mine, theirs = first.get(Sprint, sprint_id), second.get(Sprint, sprint_id)
        assert mine.status == theirs.status == "active"
        assert replicas.mark_completed(first, mine)
        first.commit()
        assert replicas.mark_completed(second, theirs) is False
    with Session(engine) as session:
        assert session.get(Sprint, sprint_id).status == "completed"


def test_two_workers_closing_one_sprint_open_one_follow_up(engine, monkeypatch):
    company_id = _company(engine, autonomy_level=3)
    sprint_id = _sprint(engine, company_id)
    planning, release = threading.Event(), threading.Event()
    plan = {"sprint_name": "Next", "goal": "g", "tasks": [{"title": "t"}]}

    def slow_planner(**kwargs):
        planning.set()
        release.wait(10)
        return plan

    monkeypatch.setattr(worker_close, "AUTO_SELF_IMPROVEMENT_ENABLED", True)
    monkeypatch.setattr(worker_close, "plan_self_improvement_sprint", slow_planner)
    monkeypatch.setattr(
        worker_close, "build_self_improvement_objective", lambda **k: "Improve"
    )

    def close():
        return replicas.close_once(
            engine, sprint_id, worker_close._complete_sprint_and_plan_follow_up
        )

    results = []
    first = threading.Thread(target=lambda: results.append(close()))
    first.start()
    assert planning.wait(10)
    results.append(close())  # the second replica, while the first plans
    release.set()
    first.join(10)

    reasons = sorted(r["reason"] for r in results)
    assert reasons == ["auto_follow_up_opened", "closing_elsewhere"]
    assert results[0]["reason"] == "closing_elsewhere"  # the second, appended first
    assert close()["reason"] == "not_active"  # a later cycle finds it closed
    with Session(engine) as session:
        sprints = session.exec(
            select(Sprint).where(Sprint.company_id == company_id)
        ).all()
    assert sorted(s.status for s in sprints) == ["active", "completed"]


def test_two_workers_finalizing_one_sprint_post_one_retrospective(engine, monkeypatch):
    company_id = _company(engine)
    sprint_id = _sprint(engine, company_id, items=("done", "blocked"))
    evaluated, proposed = [], []
    monkeypatch.setattr(
        procedures_round, "_evaluate_procedures", lambda c, s: evaluated.append(s)
    )
    monkeypatch.setattr(
        procedures_round, "_propose_procedures", lambda c, s, m: proposed.append(s)
    )
    real_close = worker_close._complete_sprint_and_plan_follow_up

    def slow_close(sprint, metrics=None):
        time.sleep(0.3)  # the other worker arrives meanwhile
        return real_close(sprint, metrics)

    monkeypatch.setattr(worker_close, "_complete_sprint_and_plan_follow_up", slow_close)

    async def two_workers():
        await asyncio.gather(
            worker_close._finalize_sprint_if_ready(_FakeNATS(), sprint_id),
            worker_close._finalize_sprint_if_ready(_FakeNATS(), sprint_id),
        )

    asyncio.run(two_workers())

    with Session(engine) as session:
        retros = session.exec(
            select(AgentMessage).where(
                AgentMessage.sprint_id == sprint_id,
                AgentMessage.kind == "retrospective",
            )
        ).all()
        assert session.get(Sprint, sprint_id).status == "completed"
    assert len(retros) == 1
    assert evaluated == [sprint_id] and proposed == [sprint_id]


# --- the ScrumMaster's triage ----------------------------------------------


def _escalation(engine, company_id, sprint_id, item_id) -> int:
    with Session(engine) as session:
        message = AgentMessage(
            company_id=company_id,
            sprint_id=sprint_id,
            backlog_item_id=item_id,
            from_name="Quality gate",
            from_role="Quality gate",
            to_role="ScrumMaster",
            kind="escalation",
            body=TRIAGE,
        )
        session.add(message)
        session.commit()
        return message.id


def _decisions(engine, item_id):
    with Session(engine) as session:
        return session.exec(
            select(AgentMessage).where(
                AgentMessage.backlog_item_id == item_id,
                AgentMessage.kind == "decision",
            )
        ).all()


def test_two_workers_triage_one_escalation_once(engine, monkeypatch):
    company_id = _company(engine, autonomy_level=3)
    sprint_id = _sprint(engine, company_id, items=("blocked",))
    item_id = _items(engine, sprint_id)[0].id
    escalation_id = _escalation(engine, company_id, sprint_id, item_id)
    asked = []

    def slow_answer(*args, **kwargs):
        asked.append(1)
        time.sleep(0.3)
        return RETRY_ANSWER

    monkeypatch.setattr(scrum_master, "ask_triage", slow_answer)

    def triage():
        worker_messages._triage_blocked_task(
            company_id, sprint_id, item_id, TRIAGE, escalation_id
        )

    _race(triage, triage)
    triage()  # a third, later: the escalation is answered

    decisions = _decisions(engine, item_id)
    assert len(decisions) == 1 and len(asked) == 1
    assert scrum_master.answered_escalation(decisions[0].body) == escalation_id


def test_a_new_escalation_of_the_same_task_gets_its_own_triage(engine, monkeypatch):
    company_id = _company(engine, autonomy_level=3)
    sprint_id = _sprint(engine, company_id, items=("blocked",))
    item_id = _items(engine, sprint_id)[0].id
    monkeypatch.setattr(scrum_master, "ask_triage", lambda *a, **k: RETRY_ANSWER)

    for _ in range(2):
        escalation_id = _escalation(engine, company_id, sprint_id, item_id)
        worker_messages._triage_blocked_task(
            company_id, sprint_id, item_id, TRIAGE, escalation_id
        )

    assert len(_decisions(engine, item_id)) == 2


# --- the review cycle -------------------------------------------------------


def test_one_review_cycle_at_a_time(engine, monkeypatch):
    ran = []

    async def body(nats):
        ran.append(nats)

    monkeypatch.setattr(worker_review, "_review_cycle_body", body)
    with locks.held(engine, locks.REVIEW_CYCLE):
        asyncio.run(worker_review._review_cycle("other replica"))
    asyncio.run(worker_review._review_cycle("this replica"))
    assert ran == ["this replica"]


# --- on a real Postgres, when one is given ---------------------------------
#
# ``REPLICAS_POSTGRES_URL`` (a migrated database, e.g. the e2e one) runs the
# advisory locks and the conditional updates where they matter. Skipped
# without it: the suite needs no external service.

PG_URL = os.environ.get("REPLICAS_POSTGRES_URL")
needs_postgres = pytest.mark.skipif(not PG_URL, reason="REPLICAS_POSTGRES_URL unset")


@pytest.fixture()
def pg(monkeypatch):
    engine = create_engine(PG_URL, pool_size=5, max_overflow=5)
    worker_stages.use_engine(monkeypatch, engine)
    yield engine
    engine.dispose()


@needs_postgres
def test_postgres_advisory_lock_is_refused_to_another_connection(pg):
    assert locks.is_postgres(pg)
    with locks.held(pg, "pg-test", 1) as first:
        _, second = _race(lambda: None, lambda: _held_now(pg, "pg-test", 1))
        assert first and second is False
    assert _held_now(pg, "pg-test", 1) is True


def _held_now(engine, name, ident):
    with locks.held(engine, name, ident) as mine:
        return mine


@needs_postgres
def test_postgres_two_workers_claim_and_close_once(pg):
    company_id = _company(pg)
    sprint_id = _sprint(pg, company_id, items=("todo",))
    item_id = _items(pg, sprint_id)[0].id

    claims = _race(*[lambda: replicas.claim_todo(pg, item_id)] * 4)
    assert sorted(claims) == [False, False, False, True]

    with Session(pg) as session:
        session.get(BacklogItem, item_id).status = "done"
        session.commit()

    def close_without_the_lock():
        return worker_close._complete_sprint_and_plan_follow_up(sprint_id)

    results = _race(*[close_without_the_lock] * 4)
    assert sorted(r["completed"] for r in results) == [False, False, False, True]
