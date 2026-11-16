"""The sprints planned from a specification (``services.planning.spec_planning``):
the PO plans tasks that name the criteria they cover, a sprint keeps what each
task covers, the prompts carry only the covered requirements and a carried
task keeps its ``covers``. The shared rows are in ``specification_helpers``.
"""

import json

from models import BacklogItem, Sprint
from models.specification import Specification
from services.planning import spec_author, spec_check, spec_planning

from specification_helpers import GOOD, _company

# --- planning from it, and what the tasks cover ------------------------------


def _approved(session, company):
    spec = Specification(
        company_id=company.id, status="approved", **spec_check.normalize(GOOD)
    )
    session.add(spec)
    session.commit()
    return spec


def test_the_po_plans_tasks_that_name_the_criteria_they_cover(
    sqlite_session, test_client, monkeypatch
):
    company = _company(sqlite_session)
    spec = _approved(sqlite_session, company)
    draft = Specification(company_id=company.id, **spec_check.normalize(GOOD))
    sqlite_session.add(draft)
    sqlite_session.commit()
    prompts = []

    def planning(model, system_prompt, prompt, **kwargs):
        prompts.append(prompt)
        return json.dumps(
            {
                "sprint_name": "Board 1",
                "goal": "List tasks",
                "tasks": [
                    {
                        "title": "List endpoint",
                        "description": "GET /tasks",
                        "priority": "high",
                        "assignee": "Developer",
                        "covers": ["REQ-1.1", "REQ-1.2", "REQ-9.9"],
                    },
                    {
                        "title": "Board page",
                        "description": "Render it",
                        "assignee": "Nobody",
                        "covers": ["REQ-1.1"],
                    },
                    {"description": "no title"},
                ],
            }
        )

    monkeypatch.setattr(spec_author, "ask", planning)
    # The PO's call runs in a thread without the session: the request is plain values.
    prepared = spec_planning.prepare_plan(sqlite_session, spec, "S", 4, [], "note")
    assert (
        prepared["specification_id"] == spec.id
        and "## Note from the person planning\nnote" in prepared["prompt"]
    )
    assert set(prepared) >= {
        "model",
        "system_prompt",
        "prompt",
        "bu_id",
        "content",
        "roles",
        "item_limit",
    }
    planned = test_client.post(
        "/v1/plan_sprint",
        json={
            "company_id": company.id,
            "scope_size": "S",
            "specification_id": spec.id,
            "prompt": "Focus this sprint on the board page.",
        },
    )
    assert planned.status_code == 200, planned.text
    assert (
        "## Note from the person planning\nFocus this sprint on the board page."
        in prompts[-1]
    )
    plan = planned.json()
    assert plan["specification_id"] == spec.id and plan["sprint_name"] == "Board 1"
    # QA's test-writing task of REQ-1 comes first; the implementation tasks
    # depend on it (``services.evidence.acceptance``).
    assert [t["covers"] for t in plan["tasks"]] == [
        ["REQ-1.1", "REQ-1.2"],
        ["REQ-1.1", "REQ-1.2"],
        ["REQ-1.1"],
    ]
    assert [(t["kind"], t["assignee"]) for t in plan["tasks"]] == [
        ("acceptance_tests", "QA"),
        (None, "Developer"),
        (None, "Developer"),
    ]
    assert [t["depends_on"] for t in plan["tasks"]] == [[], [0], [0]]
    assert plan["uncovered"] == ["REQ-2.1"]
    assert (
        test_client.post(
            "/v1/plan_sprint",
            json={
                "company_id": company.id,
                "scope_size": "S",
                "specification_id": draft.id,
            },
        ).status_code
        == 409
    )
    assert (
        test_client.post(
            "/v1/plan_sprint",
            json={
                "company_id": company.id,
                "scope_size": "S",
                "specification_id": 9999,
            },
        ).status_code
        == 404
    )

    def failing(*a, **k):
        raise spec_author.SpecAuthorError("no proxy")

    monkeypatch.setattr(spec_author, "ask", failing)
    fallback = test_client.post(
        "/v1/plan_sprint",
        json={"company_id": company.id, "scope_size": "S", "specification_id": spec.id},
    ).json()
    # One task per requirement, each after its test-writing task.
    assert [t["covers"] for t in fallback["tasks"]] == [
        ["REQ-1.1", "REQ-1.2"],
        ["REQ-2.1"],
        ["REQ-1.1", "REQ-1.2"],
        ["REQ-2.1"],
    ]
    assert [t["depends_on"] for t in fallback["tasks"]] == [[], [], [0], [1]]
    assert fallback["uncovered"] == []


def test_a_sprint_planned_from_a_specification_keeps_what_each_task_covers(
    sqlite_session, test_client
):
    company = _company(sqlite_session)
    spec = _approved(sqlite_session, company)
    base = {
        "company_id": company.id,
        "sprint_name": "Board 1",
        "start_date": "2026-11-07",
        "scope_size": "S",
    }
    typed = test_client.post(
        "/v1/start_sprint",
        json={**base, "backlog_items": [{"title": 123, "description": "x"}]},
    )
    assert typed.status_code == 422
    unknown = test_client.post(
        "/v1/start_sprint",
        json={
            **base,
            "specification_id": spec.id,
            "backlog_items": [
                {"title": "A", "description": "a", "covers": ["REQ-7.1"]}
            ],
        },
    )
    assert unknown.status_code == 422 and "REQ-7.1" in unknown.json()["detail"]
    assert (
        test_client.post(
            "/v1/start_sprint",
            json={
                **base,
                "backlog_items": [
                    {"title": "A", "description": "a", "covers": ["REQ-1.1"]}
                ],
            },
        ).status_code
        == 422
    )
    started = test_client.post(
        "/v1/start_sprint",
        json={
            **base,
            "specification_id": spec.id,
            "backlog_items": [
                {
                    "title": "List endpoint",
                    "description": "GET /tasks",
                    "covers": ["REQ-1.1", "REQ-1.2", "REQ-1.1"],
                },
                {"title": "Docs", "description": "no criteria"},
            ],
        },
    )
    assert started.status_code == 200, started.text
    sprint_id = started.json()["sprint_id"]
    board = test_client.get(f"/v1/sprints/{sprint_id}/board").json()
    assert board["sprint"]["specification_id"] == spec.id
    covers = {i["title"]: i["covers"] for c in board["columns"].values() for i in c}
    assert covers == {"List endpoint": ["REQ-1.1", "REQ-1.2"], "Docs": []}
    listed = test_client.get(f"/v1/sprints?company_id={company.id}").json()
    assert listed[0]["specification_id"] == spec.id

    coverage = test_client.get(f"/v1/specifications/{spec.id}").json()["coverage"]
    assert {c["id"]: c["status"] for c in coverage["criteria"]} == {
        "REQ-1.1": "planned",
        "REQ-1.2": "planned",
        "REQ-2.1": "uncovered",
    }
    item = sqlite_session.exec(
        __import__("sqlmodel")
        .select(BacklogItem)
        .where(BacklogItem.title == "List endpoint")
    ).one()
    item.status = "done"
    sqlite_session.add(item)
    sqlite_session.commit()
    coverage = test_client.get(f"/v1/specifications/{spec.id}").json()["coverage"]
    assert (coverage["covered"], coverage["done"], coverage["total"]) == (2, 2, 3)
    assert coverage["criteria"][0]["tasks"] == [
        {
            "id": item.id,
            "sprint_id": sprint_id,
            "title": "List endpoint",
            "status": "done",
        }
    ]


def test_the_prompts_carry_the_requirements_a_task_covers_and_nothing_more(
    sqlite_session,
):
    company = _company(sqlite_session)
    spec = _approved(sqlite_session, company)
    sprint = Sprint(
        company_id=company.id, sprint_name="Board 1", specification_id=spec.id
    )
    plain = Sprint(company_id=company.id, sprint_name="Plain")
    sqlite_session.add_all([sprint, plain])
    sqlite_session.commit()
    covered = BacklogItem(sprint_id=sprint.id, title="Move", covers=["REQ-2.1"])
    bare = BacklogItem(sprint_id=sprint.id, title="Docs")
    elsewhere = BacklogItem(sprint_id=plain.id, title="Old", covers=["REQ-2.1"])
    sqlite_session.add_all([covered, bare, elsewhere])
    sqlite_session.commit()
    engine = sqlite_session.get_bind()
    section = spec_planning.requirements_context(engine, covered.id)
    assert (
        "REQ-2.1: given a task in todo, when it is dropped on done, then its status reads done"
        in section
    )
    assert "REQ-1" not in section
    bare_context = spec_planning.requirements_context(engine, bare.id)
    assert (
        "Specification rules for every task" in bare_context
        and "REQ-" not in bare_context
    )
    assert spec_planning.requirements_context(engine, elsewhere.id) == ""
    assert spec_planning.requirements_context(engine, 999999) == ""
    assert spec_planning.requirements_for(sqlite_session, sprint, covered) == section


def test_a_carried_task_keeps_what_it_covers(sqlite_session, test_client):
    from services.loop.carry_over import carry_over

    company = _company(sqlite_session)
    spec = _approved(sqlite_session, company)
    old = Sprint(
        company_id=company.id,
        sprint_name="S1",
        status="completed",
        specification_id=spec.id,
    )
    sqlite_session.add(old)
    sqlite_session.commit()
    blocked = BacklogItem(
        sprint_id=old.id,
        title="Move",
        status="blocked",
        covers=["REQ-2.1"],
        comments=[
            {"author": "Quality gate", "text": "Blocked: too short", "created_at": ""}
        ],
    )
    sqlite_session.add(blocked)
    sqlite_session.commit()
    new = Sprint(company_id=company.id, sprint_name="S2", specification_id=spec.id)
    sqlite_session.add(new)
    sqlite_session.commit()
    [clone] = carry_over(
        sqlite_session, company.id, [blocked.id], new, "ana", "Manager"
    )
    sqlite_session.commit()
    assert clone.covers == ["REQ-2.1"]
    # Into a sprint planned from nothing, or from another specification, the
    # criteria ids mean nothing: they are dropped.
    other_blocked = BacklogItem(
        sprint_id=old.id,
        title="List",
        status="blocked",
        covers=["REQ-1.1"],
        comments=[
            {"author": "Quality gate", "text": "Blocked: too short", "created_at": ""}
        ],
    )
    sqlite_session.add(other_blocked)
    sqlite_session.commit()
    plain = Sprint(company_id=company.id, sprint_name="S3")
    sqlite_session.add(plain)
    sqlite_session.commit()
    [dropped] = carry_over(
        sqlite_session, company.id, [other_blocked.id], plain, "ana", "Manager"
    )
    sqlite_session.commit()
    assert dropped.covers is None
    assert {
        c["id"]: c["status"]
        for c in spec_planning.coverage(sqlite_session, spec)["criteria"]
    }["REQ-2.1"] == "planned"
