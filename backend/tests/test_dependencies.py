"""Dependencies between the tasks of a sprint (``services.tasks.dependencies``):
the plan names them, ``start_sprint`` keeps and checks them, the worker and
the intake queue a task only once what it depends on is done, and a carried
task keeps a dependency carried with it. A task held for a person is in
``test_dependency_holds.py``, the independent history check in
``test_dependency_history.py``; the shared rows in ``dependencies_helpers``.
"""

import asyncio
import json

import pytest
import worker_stages
from dependencies_helpers import _company, _sprint, _task
from models import BacklogItem, OutboxEvent
from models.specification import Specification
from services.loop import sprint_control
from services.loop.worker import intake, task_run
from services.planning import spec_author, spec_check
from services.tasks import dependencies
from sqlmodel import select
from test_specifications import GOOD

# --- the checks on what a plan or a request asks ----------------------------


def test_positions_are_checked_and_cycles_refused():
    tasks = [
        {"title": "a"},
        {"title": "b", "depends_on": [0, 0]},
        {"title": "c", "depends_on": [1, 0]},
    ]
    assert dependencies.check_positions(tasks) == [[], [0], [1, 0]]
    for bad in (
        [{"title": "a", "depends_on": [0]}],
        [{"title": "a", "depends_on": [3]}],
        [{"title": "a", "depends_on": "0"}],
        [{"title": "a", "depends_on": [True]}],
    ):
        with pytest.raises(dependencies.DependencyError) as refused:
            dependencies.check_positions(bad)
        assert refused.value.status == 422
    with pytest.raises(dependencies.DependencyError, match="circle: 0 → 1 → 0"):
        dependencies.check_positions([{"depends_on": [1]}, {"depends_on": [0]}])
    assert dependencies.find_cycle({1: [2], 2: [3], 3: []}) is None
    assert dependencies.without_cycles({0: [1, 0, 9], 1: [2], 2: [0]}) == {
        0: [1],
        1: [2],
        2: [],
    }


def test_the_po_names_dependencies_by_title_and_the_plan_carries_positions(
    sqlite_session, test_client, monkeypatch
):
    company = _company(sqlite_session)
    spec = Specification(
        company_id=company.id, status="approved", **spec_check.normalize(GOOD)
    )
    sqlite_session.add(spec)
    sqlite_session.commit()
    monkeypatch.setattr(
        spec_author,
        "ask",
        lambda *a, **k: json.dumps(
            {
                "sprint_name": "B",
                "goal": "g",
                "tasks": [
                    {
                        "title": "Store " + "x" * 90,
                        "description": "d",
                        "covers": ["REQ-1.1"],
                    },
                    {
                        "title": "List endpoint",
                        "description": "d",
                        "covers": ["REQ-1.2"],
                        # The first title as the PO wrote it, longer than what is shown.
                        "depends_on": ["store " + "x" * 90, "Nobody", "List endpoint"],
                    },
                    {
                        "title": "Board page",
                        "description": "d",
                        "covers": ["REQ-2.1"],
                        "depends_on": ["List endpoint", "1"],
                    },
                ],
            }
        ),
    )
    plan = test_client.post(
        "/v1/plan_sprint",
        json={"company_id": company.id, "scope_size": "S", "specification_id": spec.id},
    ).json()
    # The test-writing task of REQ-1 comes first (position 0); "List
    # endpoint" waits for it and for "Store"; "Board page" does not fit scope
    # S with the test task of REQ-2, so both are left out.
    assert [t["title"][:20] for t in plan["tasks"]] == [
        "Acceptance tests for",
        "Store xxxxxxxxxxxxxx",
        "List endpoint",
    ]
    assert len(plan["tasks"][1]["title"]) == 80
    assert [t["depends_on"] for t in plan["tasks"]] == [[], [0], [0, 1]]

    monkeypatch.setattr(
        spec_author,
        "ask",
        lambda *a, **k: json.dumps(
            {
                "tasks": [
                    {"title": "A", "depends_on": ["B"]},
                    {"title": "B", "depends_on": ["A"]},
                ]
            }
        ),
    )
    cyclic = test_client.post(
        "/v1/plan_sprint",
        json={"company_id": company.id, "scope_size": "S", "specification_id": spec.id},
    ).json()
    assert [t["depends_on"] for t in cyclic["tasks"]] == [[1], []]


# --- start_sprint and the board ---------------------------------------------


def _start(test_client, company, items):
    return test_client.post(
        "/v1/start_sprint",
        json={
            "company_id": company.id,
            "sprint_name": "S",
            "start_date": "2026-11-07",
            "scope_size": "S",
            "backlog_items": items,
        },
    )


def test_a_sprint_keeps_the_dependencies_and_queues_only_the_tasks_that_can_start(
    sqlite_session, test_client
):
    company = _company(sqlite_session)
    refused = _start(
        test_client,
        company,
        [
            {"title": "A", "description": "a", "depends_on": [1]},
            {"title": "B", "description": "b", "depends_on": [0]},
        ],
    )
    assert refused.status_code == 422 and "circle" in refused.json()["detail"]
    assert (
        _start(
            test_client,
            company,
            [{"title": "A", "description": "a", "depends_on": [4]}],
        ).status_code
        == 422
    )
    started = _start(
        test_client,
        company,
        [
            {"title": "Store", "description": "s"},
            {"title": "Endpoint", "description": "e", "depends_on": [0]},
            {"title": "Page", "description": "p", "depends_on": [1, 0]},
        ],
    )
    assert started.status_code == 200, started.text
    store_id, endpoint_id, _page_id = started.json()["backlog_item_ids"]
    queued = [
        e.payload["message"]["backlog_item_id"]
        for e in sqlite_session.exec(
            select(OutboxEvent).where(OutboxEvent.aggregate_type == "backlog_item")
        ).all()
        if e.payload["message"]["sprint_id"] == started.json()["sprint_id"]
    ]
    assert queued == [store_id]
    board = test_client.get(f"/v1/sprints/{started.json()['sprint_id']}/board").json()
    items = {i["title"]: i for c in board["columns"].values() for i in c}
    assert items["Endpoint"]["depends_on"] == [store_id] and items["Endpoint"][
        "waiting_for"
    ] == [store_id]
    assert items["Page"]["depends_on"] == [endpoint_id, store_id] and items["Page"][
        "waiting_for"
    ] == [endpoint_id, store_id]
    assert items["Store"]["depends_on"] == [] and items["Store"]["waiting_for"] == []
    assert (
        items["Endpoint"]["comments"][-1]["text"]
        == f'Waits for task {store_id} ("Store") to be done first.'
    )
    assert items["Page"]["comments"][-1]["text"].startswith("Waits for tasks ")


def test_dispatch_and_the_worker_queue_a_task_only_once_its_dependencies_are_done(
    sqlite_session, monkeypatch
):
    company = _company(sqlite_session)
    sprint = _sprint(sqlite_session, company)
    store = _task(sqlite_session, sprint, "Store")
    endpoint = _task(sqlite_session, sprint, "Endpoint", depends_on=[store.id])
    assert sprint_control.dispatch_todo(sqlite_session, sprint) == 1
    sqlite_session.commit()
    engine = sqlite_session.get_bind()
    assert dependencies.ready(engine, store.id) is True
    assert dependencies.ready(engine, endpoint.id) is False
    assert dependencies.ready(engine, 999999) is False

    # The worker's consume-time gate (``_on_task_assign``) asks the same
    # ``ready``: an assignment that arrives early leaves the task in todo.
    store.status = "done"
    sqlite_session.add(store)
    sqlite_session.commit()
    assert dependencies.ready(engine, endpoint.id) is True
    assert sprint_control.dispatch_todo(sqlite_session, sprint) == 1


class _AssignNats:
    def __init__(self):
        self.queued = []

    async def publish(self, subject, payload, msg_id=None):
        if subject == "task.assign":
            self.queued.append(payload["backlog_item_id"])


def test_the_intake_queues_and_claims_a_task_only_once_its_dependencies_are_done(
    sqlite_session, monkeypatch
):
    worker_stages.use_engine(monkeypatch, sqlite_session.get_bind())
    ran = []

    async def _run(nats_client, backlog_item_id, ctx):
        ran.append(backlog_item_id)

    monkeypatch.setattr(task_run, "_run_claimed_task", _run)
    company = _company(sqlite_session)
    sprint = _sprint(sqlite_session, company)
    store = _task(sqlite_session, sprint, "Store", status="in_progress")
    endpoint = _task(sqlite_session, sprint, "Endpoint", depends_on=[store.id])
    assignment = {"backlog_item_id": endpoint.id, "sprint_id": sprint.id}

    nats = _AssignNats()
    asyncio.run(intake._dispatch_todo_items(nats))
    asyncio.run(intake._on_task_assign(nats, None, assignment))
    sqlite_session.expire_all()
    # Neither queued nor claimed while its dependency is in work.
    assert endpoint.id not in nats.queued and ran == []
    assert sqlite_session.get(BacklogItem, endpoint.id).status == "todo"

    store = sqlite_session.get(BacklogItem, store.id)
    store.status = "done"
    sqlite_session.add(store)
    sqlite_session.commit()
    asyncio.run(intake._dispatch_todo_items(nats))
    asyncio.run(intake._on_task_assign(nats, None, assignment))
    sqlite_session.expire_all()
    assert endpoint.id in nats.queued and ran == [endpoint.id]
    assert sqlite_session.get(BacklogItem, endpoint.id).status == "in_progress"


def test_a_carried_task_keeps_a_dependency_carried_with_it_and_drops_the_rest(
    sqlite_session,
):
    from services.loop.carry_over import carry_over

    company = _company(sqlite_session)
    old = _sprint(sqlite_session, company, status="completed")
    gate = [
        {
            "author": "Quality gate",
            "text": "Pre-review quality gate failed: too short",
            "created_at": "",
        }
    ]
    done = _task(sqlite_session, old, "Done before", status="done")
    store = _task(sqlite_session, old, "Store", status="blocked", comments=gate)
    endpoint = _task(
        sqlite_session,
        old,
        "Endpoint",
        status="blocked",
        depends_on=[store.id, done.id],
        comments=[
            {
                "author": "System",
                "text": f'Blocked: waits for task {store.id} ("Store"), which waits for a person.',
                "created_at": "",
            }
        ],
    )
    new = _sprint(sqlite_session, company)
    clones = carry_over(
        sqlite_session, company.id, [store.id, endpoint.id], new, "ana", "Manager"
    )
    sqlite_session.commit()
    by_title = {c.title: c for c in clones}
    assert by_title["Store"].depends_on is None
    assert by_title["Endpoint"].depends_on == [by_title["Store"].id]
    assert (
        by_title["Endpoint"].comments[-1]["text"]
        == f'Waits for task {by_title["Store"].id} ("Store") to be done first.'
    )
    queued = [
        e.payload["message"]["backlog_item_id"]
        for e in sqlite_session.exec(select(OutboxEvent)).all()
        if e.payload["message"].get("sprint_id") == new.id
    ]
    assert queued == [by_title["Store"].id]
