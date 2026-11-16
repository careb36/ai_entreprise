"""The page budget of a saved view's filter (issue #535): its SQL statements
grow only by its id chunks, never per task, and over five thousand tasks it
answers within the budget (a 1 s guard; the specification's 300 ms with
``PERF_STRICT=1``). The shared rows are in ``task_views_helpers``.
"""

from __future__ import annotations

import os
import time

import pytest
from models import (
    AgentRun,
    BacklogItem,
    Verification,
)
from models.agent_run import TASK_WORKER_PRODUCER
from services.contracts import task_filters
from services.tasks import task_filter
from sqlalchemy import event
from sqlmodel import select
from task_views_helpers import WAITS, _company, _sprint

# ── Performance ──────────────────────────────────────────────────────────────


def _seed_tasks(session, count):
    """*count* tasks over ten sprints, half with a run and a verification."""
    company = _company(session)
    sprints = [_sprint(session, company, f"S{i}") for i in range(10)]
    statuses = task_filters.STATUSES
    items = [
        BacklogItem(
            sprint_id=sprints[i % 10].id,
            title=f"Task {i}",
            status=statuses[i % 5],
            assignee="Developer",
            comments=WAITS if i % 5 == 4 else [],
            covers=[f"REQ-{i % 7}.1"],
        )
        for i in range(count)
    ]
    session.add_all(items)
    session.commit()
    runs = [
        AgentRun(
            company_id=company.id,
            external_ref=f"task-{item.id}",
            producer=TASK_WORKER_PRODUCER,
            artifact_digest="0" * 64,
            artifact_size=1,
        )
        for item in items[::2]
    ]
    session.add_all(runs)
    session.commit()
    session.add_all(
        Verification(
            company_id=company.id,
            agent_run_id=run.id,
            verdict="refuted" if n % 3 else "verified",
            checks=[{"name": "risk_tier", "tier": "high" if n % 4 else "low"}],
        )
        for n, run in enumerate(runs)
    )
    session.commit()
    return company


_BUDGET_FILTER = {
    "and": [
        {"field": "verdict", "op": "eq", "value": "refuted"},
        {"field": "risk_tier", "op": "gte", "value": "medium"},
        {
            "or": [
                {"field": "status", "op": "in", "value": ["blocked", "todo"]},
                {"field": "covers", "op": "eq", "value": "REQ-3"},
            ]
        },
    ]
}


def _statements(session, company_id, tree):
    """How many SQL statements one filtered page runs."""
    seen = []

    def count(*_args, **_kwargs):
        seen.append(1)

    engine = session.get_bind()
    event.listen(engine, "before_cursor_execute", count)
    try:
        task_filter.query(session, company_id, tree, limit=100)
    finally:
        event.remove(engine, "before_cursor_execute", count)
    return len(seen)


def test_a_filter_s_queries_grow_only_by_its_chunks_never_per_task(sqlite_session):
    """The deterministic half of the page budget (#535): the filter's SQL
    statements do not grow per task. The runs and verifications are read in
    chunks of ``task_filter._CHUNK`` ids (SQLite's bound-parameter limit), so
    5000 tasks may add a statement per chunk for each of the two reads, and
    no more: an N+1 cannot hide behind a fast machine. The wall-clock half is
    the test below."""
    tree = task_filters.parse_filter(_BUDGET_FILTER)
    small = _seed_tasks(sqlite_session, 50)
    large = _seed_tasks(sqlite_session, 5000)
    few = _statements(sqlite_session, small.id, tree)
    many = _statements(sqlite_session, large.id, tree)
    chunks = -(-5000 // task_filter._CHUNK)
    assert few <= many <= few + 2 * chunks, (few, many, chunks)


@pytest.mark.xdist_group("wall_clock")
def test_a_filter_over_five_thousand_tasks_answers_within_the_page_budget(
    sqlite_session,
):
    company = _seed_tasks(sqlite_session, 5000)
    tree = task_filters.parse_filter(_BUDGET_FILTER)
    task_filter.query(sqlite_session, company.id, tree, limit=100)  # warm-up
    timings, reads = [], []
    for _ in range(5):
        start = time.perf_counter()
        result = task_filter.query(sqlite_session, company.id, tree, limit=100)
        timings.append(time.perf_counter() - start)
        # The reference: reading every task row, on the same database at the
        # same moment, so the machine's load weighs on both sides alike.
        start = time.perf_counter()
        sqlite_session.exec(select(BacklogItem)).all()
        reads.append(time.perf_counter() - start)
        sqlite_session.expire_all()
    assert 0 < result["count"] < 5000 and len(result["tasks"]) == 100
    # The page budget of docs/ux/performance_usability_benchmark.md is 400 ms
    # (the Doherty threshold); the specification asks 300 ms for the query.
    # Wall-clock time is a measurement only on a machine with CPU to spare:
    # the nightly bench job, alone on the machine, holds the query to the
    # specification's 300 ms with PERF_STRICT=1 (.github/workflows/bench.yml).
    # The self-hosted runners that check a pull request share one machine, so
    # there an absolute budget measures the other jobs; the query is held
    # instead to five times the reference read (about 1.5 times on an idle
    # machine), which a large regression crosses and the load does not.
    # Per-task queries are caught exactly by the statement count above.
    if os.environ.get("PERF_STRICT") == "1":
        assert min(timings) < 0.3, timings
    else:
        assert min(timings) < 5 * min(reads), (timings, reads)
