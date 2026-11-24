"""The PO's plan from a prompt carries the order between its tasks.

In sprint 6 (2026-11-23) the plan from a prompt had no ``depends_on``: the
QA task that validates a split started before the split existed, refuted the
missing work and spent reworks. The specification planner already asked the
PO for dependencies; the plan from a prompt (``sprint_planner._plan_with_po``)
now asks too and reads them the same way: by title (as written, or as cut) or
by position, unknown ones and self dropped, a cycle broken. ``start_sprint``
then holds the task until the ones it waits for are done
(``services/tasks/dependencies.py``).
"""

import secrets

from models import Agent, BacklogItem, Company
from services.planning import sprint_planner, sprint_prompts
from sqlmodel import select

ROLES = ["PO", "Developer", "QA"]


class _Client:
    def __init__(self, answer):
        self.answer = answer

    def chat(self, messages, model, role):
        return {"choices": [{"message": {"content": self.answer}}]}


SPLIT = (
    '{"sprint_name": "Split", "goal": "Split the file", "tasks": ['
    '{"title": "Split the test file", "assignee": "Developer", "depends_on": []},'
    '{"title": "Register the new files", "assignee": "Developer",'
    ' "depends_on": ["Split the test file"]},'
    '{"title": "Validate sizes and tests", "assignee": "QA",'
    ' "depends_on": ["split the test file", "Register the new files"]}]}'
)


def _plan(monkeypatch, answer):
    monkeypatch.setattr(sprint_planner, "_client", lambda po: _Client(answer))
    agents = [{"role": r, "name": r, "model": "m"} for r in ROLES]
    return sprint_planner.plan_execution_sprint("Acme", "Split it", "M", agents)


def test_the_prompts_ask_the_po_for_the_order():
    for template in (
        sprint_prompts._EXECUTION_PROMPT,
        sprint_prompts._SELF_IMPROVEMENT_PROMPT,
    ):
        assert '"depends_on"' in template and "validates" in template


def test_dependencies_by_title_become_positions(monkeypatch):
    plan = _plan(monkeypatch, SPLIT)

    assert plan["planner_fallback"] is None
    assert [t["depends_on"] for t in plan["tasks"]] == [[], [0], [0, 1]]


def test_unknown_self_and_cycles_are_dropped_and_positions_are_read(monkeypatch):
    answer = (
        '{"tasks": ['
        '{"title": "A", "assignee": "Developer", "depends_on": ["B", "A", "nope"]},'
        '{"title": "B", "assignee": "Developer", "depends_on": ["A"]},'
        '{"title": "C", "assignee": "QA", "depends_on": ["0", "9", 1]},'
        '{"title": "D", "assignee": "QA", "depends_on": "A"}]}'
    )

    plan = _plan(monkeypatch, answer)

    deps = [t["depends_on"] for t in plan["tasks"]]
    assert deps[0] == [1] and deps[1] == []  # The edge that closes A<->B is dropped.
    assert deps[2] == [0, 1] and deps[3] == []


def test_a_title_cut_to_80_characters_still_names_its_task(monkeypatch):
    long = "Split " + "x" * 100
    answer = (
        '{"tasks": ['
        f'{{"title": "{long}", "assignee": "Developer"}},'
        f'{{"title": "Check", "assignee": "QA", "depends_on": ["{long}"]}}]}}'
    )

    plan = _plan(monkeypatch, answer)

    assert len(plan["tasks"][0]["title"]) == 80
    assert plan["tasks"][1]["depends_on"] == [0]


def test_the_started_sprint_holds_the_validation_until_the_work_is_done(
    sqlite_session, test_client, monkeypatch
):
    company = Company(company_name=f"Deps {secrets.token_hex(3)}")
    sqlite_session.add(company)
    sqlite_session.commit()
    for role in ROLES:
        sqlite_session.add(
            Agent(company_id=company.id, role=role, name=role, model="m")
        )
    sqlite_session.commit()
    monkeypatch.setattr(sprint_planner, "_client", lambda po: _Client(SPLIT))

    planned = test_client.post(
        "/v1/plan_sprint",
        json={"company_id": company.id, "prompt": "Split it", "scope_size": "M"},
    )
    assert planned.status_code == 200, planned.text
    plan = planned.json()
    started = test_client.post(
        "/v1/start_sprint",
        json={
            "company_id": company.id,
            "sprint_name": plan["sprint_name"],
            "start_date": "2026-11-24",
            "scope_size": "M",
            "goal": plan["goal"],
            "backlog_items": plan["tasks"],
        },
    )
    assert started.status_code == 200, started.text

    rows = sqlite_session.exec(
        select(BacklogItem)
        .where(BacklogItem.sprint_id == started.json()["sprint_id"])
        .order_by(BacklogItem.id)
    ).all()
    split, register, validate = rows
    assert split.depends_on in (None, [])
    assert register.depends_on == [split.id]
    assert validate.depends_on == [split.id, register.id]


def test_a_follow_up_sprint_holds_its_tasks_in_the_plan_s_order(sqlite_session):
    """The sprint the platform opens on its own follows the same order."""
    from models import OutboxEvent, Sprint
    from services.loop.worker import close

    company = Company(company_name=f"Follow {secrets.token_hex(3)}")
    sqlite_session.add(company)
    sqlite_session.commit()
    sprint = Sprint(company_id=company.id, sprint_name="Next", scope_size="S")
    sqlite_session.add(sprint)
    sqlite_session.commit()
    tasks = [
        {"title": "Build", "assignee": "Developer", "depends_on": []},
        {"title": "Check", "assignee": "QA", "depends_on": [0, 1, 7]},
    ]

    close._add_follow_up_tasks(sqlite_session, sprint, tasks)
    sqlite_session.commit()

    build, check = sqlite_session.exec(
        select(BacklogItem)
        .where(BacklogItem.sprint_id == sprint.id)
        .order_by(BacklogItem.id)
    ).all()
    assert build.depends_on in (None, []) and check.depends_on == [build.id]
    queued = [
        e.payload["message"]["backlog_item_id"]
        for e in sqlite_session.exec(select(OutboxEvent)).all()
    ]
    assert build.id in queued and check.id not in queued
