"""The specification: numbered requirements with testable criteria, checked
deterministically, drafted by the PO from a brief, approved by a person, and
covered by the tasks of the sprints planned from it (``services.spec_check``,
``spec_author``, ``spec_planning``, ``routers/specifications.py``).
"""

import json
import secrets

import pytest
from models import BacklogItem, Company, Sprint
from models.specification import Specification
from services import spec_author, spec_check, spec_planning
from test_git_workflow import _member, enforcement  # noqa: F401


def _company(session):
    company = Company(company_name=f"Spec {secrets.token_hex(3)}", autonomy_level=2)
    session.add(company)
    session.commit()
    return company


CRITERION = {
    "given": "a signed-in user",
    "when": "they open the board",
    "then": "their tasks are listed",
}
GOOD = {
    "title": "Task board",
    "summary": "A board where a team sees its tasks.",
    "requirements": [
        {
            "title": "List the tasks",
            "description": "The board lists the tasks of the signed-in user's company.",
            "criteria": [
                CRITERION,
                {**CRITERION, "then": "the list has one row per task"},
            ],
        },
        {
            "title": "Move a task",
            "description": "A task is dragged between columns and keeps its new status.",
            "criteria": [
                {
                    "given": "a task in todo",
                    "when": "it is dropped on done",
                    "then": "its status reads done",
                }
            ],
        },
    ],
    "quality": [
        {"attribute": "performance", "target": "the board renders in under 300 ms"}
    ],
    "constraints": ["TypeScript and React"],
}


# --- the check ---------------------------------------------------------------


def test_normalize_numbers_requirements_criteria_and_quality_in_order():
    spec = spec_check.normalize(
        {
            **GOOD,
            "requirements": [
                {"id": "REQ-9", **GOOD["requirements"][0]},
                GOOD["requirements"][1],
            ],
        }
    )
    assert [r["id"] for r in spec["requirements"]] == ["REQ-1", "REQ-2"]
    assert [c["id"] for c in spec["requirements"][0]["criteria"]] == [
        "REQ-1.1",
        "REQ-1.2",
    ]
    assert spec["quality"][0]["id"] == "NFR-1"
    assert spec_check.criterion_ids(spec) == ["REQ-1.1", "REQ-1.2", "REQ-2.1"]
    result = spec_check.check(spec)
    # Nothing blocks; the standards (STD-PO-1) warn on the quality areas the
    # specification says nothing about (it names performance only).
    assert (result["ok"], result["blocking"], result["warnings"]) == (True, 0, 3)
    assert [f["message"] for f in result["findings"]] == [
        f"No quality attribute says what is expected of {area}."
        for area in ("security", "reliability", "accessibility")
    ]
    assert {f["code"] for f in result["findings"]} == {"quality_area_missing"}


def test_the_check_blocks_what_cannot_be_tested_and_warns_about_promises():
    spec = spec_check.normalize(
        {
            "title": "Board",
            "requirements": [
                {
                    "title": "Be fast",
                    "description": "It must be fast and easy.",
                    "criteria": [],
                },
                {
                    "title": "Sort",
                    "description": "Columns sort by priority, highest first.",
                    "criteria": [
                        {"given": "", "when": "sorted", "then": "it is intuitive"}
                    ],
                },
            ],
            "quality": [{"attribute": "availability", "target": "always up"}],
            "questions": [
                "Which columns?",
                {"question": "Who signs in?", "answer": "the company's members"},
            ],
        }
    )
    result = spec_check.check(spec)
    codes = {(f["where"], f["code"], f["severity"]) for f in result["findings"]}
    assert ("REQ-1", "no_criteria", "blocking") in codes
    assert ("REQ-1", "vague", "warning") in codes
    assert ("REQ-2.1", "empty_step", "blocking") in codes
    assert ("NFR-1", "no_number", "blocking") in codes
    assert ("Q1", "open_question", "blocking") in codes
    assert ("Q2", "open_question", "blocking") not in codes
    assert result["ok"] is False and result["blocking"] == 4
    # A number beside the word is a measure, not a promise.
    assert spec_check.check(
        spec_check.normalize(
            {
                **GOOD,
                "requirements": [
                    {
                        **GOOD["requirements"][0],
                        "description": "The board is fast: it renders 500 tasks in under 300 ms.",
                    }
                ],
            }
        )
    )["ok"]


def test_the_prompt_section_carries_only_the_covered_criteria():
    spec = spec_check.normalize(GOOD)
    section = spec_planning.requirements_section(spec, ["REQ-2.1"])
    assert "## Requirements this task covers" in section
    assert (
        "REQ-2: Move a task" in section and "REQ-2.1: given a task in todo" in section
    )
    assert "REQ-1" not in section
    assert "NFR-1: performance" in section and "TypeScript and React" in section
    # A task covering no criterion still gets what holds for every task.
    rules = spec_planning.requirements_section(spec, [])
    assert "## Specification rules for every task" in rules
    assert "NFR-1: performance" in rules and "REQ-" not in rules


# --- the PO's draft ----------------------------------------------------------


def test_the_po_draft_is_read_from_the_fixed_shape_and_nothing_else():
    text = (
        "```json\n"
        + json.dumps({**GOOD, "questions": ["Which columns does the board have?"]})
        + "\n```"
    )
    spec = spec_author.parse_draft(text)
    assert spec["requirements"][0]["id"] == "REQ-1"
    assert spec["questions"] == [
        {"question": "Which columns does the board have?", "answer": None}
    ]
    assert spec_author.parse_draft("Sure! Here is a plan: build a board.") is None
    assert (
        spec_author.parse_draft(json.dumps({"title": "x", "requirements": []})) is None
    )


def test_a_revision_prompt_carries_the_answers_and_the_stack_stored_as_text():
    existing = {
        **spec_check.normalize(GOOD),
        "questions": [
            {"question": "Which columns?", "answer": "todo, doing, done"},
            {"question": "Open?", "answer": None},
        ],
    }
    prompt = spec_author.draft_prompt(
        "Pia", "a board", json.dumps({"language": "TypeScript"}), existing
    )
    assert "Primary language: TypeScript" in prompt
    assert "Q: Which columns?\n  A: todo, doing, done" in prompt
    assert "Q: Open?" not in prompt
    assert spec_author.merge_questions(
        existing["questions"],
        [
            {"question": "which columns?", "answer": None},
            {"question": "New one?", "answer": None},
        ],
    ) == [
        {"question": "Which columns?", "answer": "todo, doing, done"},
        {"question": "New one?", "answer": None},
    ]


# --- the routes --------------------------------------------------------------


@pytest.mark.usefixtures("enforcement")
def test_a_manager_writes_a_draft_a_viewer_reads_it_and_nobody_else(
    sqlite_session, test_client
):
    company, other = _company(sqlite_session), _company(sqlite_session)
    manager = _member(sqlite_session, company.id, "Manager")
    viewer = _member(sqlite_session, company.id, "Viewer")
    outsider = _member(sqlite_session, other.id, "Manager")
    body = {"company_id": company.id, **GOOD}
    assert (
        test_client.post("/v1/specifications", json=body, headers=viewer).status_code
        == 403
    )
    assert (
        test_client.post("/v1/specifications", json=body, headers=outsider).status_code
        == 403
    )
    created = test_client.post("/v1/specifications", json=body, headers=manager)
    assert created.status_code == 201, created.text
    spec = created.json()
    assert spec["status"] == "draft" and spec["version"] == 1
    assert spec["requirements"][1]["criteria"][0]["id"] == "REQ-2.1"
    assert spec["check"]["ok"] is True
    assert spec["coverage"] == {
        "criteria": [
            {
                "id": c,
                "requirement_id": c.split(".")[0],
                "tasks": [],
                "tests": [],
                "status": "uncovered",
                "test": f"test_{c.lower().replace('-', '_').replace('.', '_')}_…",
                "verified": False,
                "last_result": None,
            }
            for c in ("REQ-1.1", "REQ-1.2", "REQ-2.1")
        ],
        "total": 3,
        "covered": 0,
        "done": 0,
        "verified": 0,
    }
    listed = test_client.get(
        f"/v1/specifications?company_id={company.id}", headers=viewer
    ).json()
    assert [s["id"] for s in listed["specifications"]] == [spec["id"]]
    assert (
        test_client.get(
            f"/v1/specifications/{spec['id']}", headers=outsider
        ).status_code
        == 403
    )
    assert (
        test_client.get(
            f"/v1/specifications?company_id={company.id}", headers=outsider
        ).status_code
        == 403
    )


@pytest.mark.usefixtures("enforcement")
def test_the_po_drafts_from_a_brief_asks_and_a_person_answers_and_approves(
    sqlite_session, test_client, monkeypatch
):
    company = _company(sqlite_session)
    manager = _member(sqlite_session, company.id, "Manager")
    prompts = []

    def fake_ask(model, system_prompt, prompt, **kwargs):
        prompts.append(prompt)
        draft = {
            **GOOD,
            "quality": [{"attribute": "performance", "target": "fast"}],
            "questions": ["Which columns does the board have?"],
        }
        if "Answers to your questions" in prompt:
            draft["quality"] = [
                {"attribute": "performance", "target": "renders in under 300 ms"}
            ]
            draft["requirements"] = GOOD["requirements"] + [
                {
                    "title": "Columns",
                    "description": "The board has the columns the team chose.",
                    "criteria": [CRITERION],
                }
            ]
        return json.dumps(draft)

    monkeypatch.setattr(spec_author, "ask", fake_ask)
    drafted = test_client.post(
        "/v1/specifications/draft",
        json={"company_id": company.id, "brief": "A task board for small teams."},
        headers=manager,
    )
    assert drafted.status_code == 200, drafted.text
    spec = drafted.json()
    assert spec["drafted_by"] == "PO" and "A task board for small teams." in prompts[0]
    assert spec["questions"] == [
        {"question": "Which columns does the board have?", "answer": None}
    ]
    blocking = {
        (f["where"], f["code"])
        for f in spec["check"]["findings"]
        if f["severity"] == "blocking"
    }
    assert blocking == {("NFR-1", "no_number"), ("Q1", "open_question")}

    refused = test_client.post(
        f"/v1/specifications/{spec['id']}/approve", headers=manager
    )
    assert refused.status_code == 409
    assert [f["where"] for f in refused.json()["detail"]["findings"]] == ["NFR-1", "Q1"]

    answered = test_client.post(
        f"/v1/specifications/{spec['id']}/questions/0",
        json={"answer": "todo, doing, done"},
        headers=manager,
    ).json()
    assert answered["questions"][0]["answer"] == "todo, doing, done"
    assert (
        test_client.post(
            f"/v1/specifications/{spec['id']}/questions/5",
            json={"answer": "x"},
            headers=manager,
        ).status_code
        == 404
    )

    assert (
        test_client.post(
            "/v1/specifications/draft",
            json={"company_id": company.id, "brief": "  "},
            headers=manager,
        ).status_code
        == 422
    )
    # A revision needs no new brief: the answers are what the PO works from.
    revised = test_client.post(
        "/v1/specifications/draft",
        json={"company_id": company.id, "specification_id": spec["id"]},
        headers=manager,
    ).json()
    assert revised["id"] == spec["id"]
    assert "A: todo, doing, done" in prompts[1]
    assert "## Brief from the person asking" not in prompts[1]
    assert revised["questions"] == [
        {
            "question": "Which columns does the board have?",
            "answer": "todo, doing, done",
        }
    ]
    assert [r["id"] for r in revised["requirements"]] == ["REQ-1", "REQ-2", "REQ-3"]
    assert revised["check"]["ok"] is True

    approved = test_client.post(
        f"/v1/specifications/{spec['id']}/approve", headers=manager
    ).json()
    assert approved["status"] == "approved" and approved["approved_by"].startswith(
        "manager-"
    )
    assert (
        test_client.put(
            f"/v1/specifications/{spec['id']}", json=GOOD, headers=manager
        ).status_code
        == 409
    )


@pytest.mark.usefixtures("enforcement")
def test_an_unusable_answer_leaves_the_draft_as_it_was(
    sqlite_session, test_client, monkeypatch
):
    company = _company(sqlite_session)
    manager = _member(sqlite_session, company.id, "Manager")
    spec = test_client.post(
        "/v1/specifications", json={"company_id": company.id, **GOOD}, headers=manager
    ).json()
    monkeypatch.setattr(spec_author, "ask", lambda *a, **k: "I would build a board.")
    refused = test_client.post(
        "/v1/specifications/draft",
        json={
            "company_id": company.id,
            "brief": "a board",
            "specification_id": spec["id"],
        },
        headers=manager,
    )
    assert refused.status_code == 502
    again = test_client.get(f"/v1/specifications/{spec['id']}", headers=manager).json()
    assert (
        again["requirements"] == spec["requirements"]
        and again["drafted_by"] == spec["drafted_by"]
    )

    def failing(*a, **k):
        raise spec_author.SpecAuthorError("LLM call failed")

    monkeypatch.setattr(spec_author, "ask", failing)
    assert (
        test_client.post(
            "/v1/specifications/draft",
            json={"company_id": company.id, "brief": "a board"},
            headers=manager,
        ).status_code
        == 502
    )
    assert (
        len(
            test_client.get(
                f"/v1/specifications?company_id={company.id}", headers=manager
            ).json()["specifications"]
        )
        == 1
    )


@pytest.mark.usefixtures("enforcement")
def test_a_revision_is_a_new_version_that_supersedes_the_approved_one(
    sqlite_session, test_client
):
    company = _company(sqlite_session)
    manager = _member(sqlite_session, company.id, "Manager")
    v1 = test_client.post(
        "/v1/specifications", json={"company_id": company.id, **GOOD}, headers=manager
    ).json()
    assert (
        test_client.post(
            f"/v1/specifications/{v1['id']}/revise", headers=manager
        ).status_code
        == 409
    )
    test_client.post(f"/v1/specifications/{v1['id']}/approve", headers=manager)
    v2 = test_client.post(f"/v1/specifications/{v1['id']}/revise", headers=manager)
    assert v2.status_code == 201
    v2 = v2.json()
    assert (v2["version"], v2["supersedes_id"], v2["status"]) == (2, v1["id"], "draft")
    assert (
        test_client.post(
            f"/v1/specifications/{v1['id']}/revise", headers=manager
        ).status_code
        == 409
    )
    edited = test_client.put(
        f"/v1/specifications/{v2['id']}",
        json={**GOOD, "title": "Task board v2"},
        headers=manager,
    ).json()
    assert edited["title"] == "Task board v2"
    test_client.post(f"/v1/specifications/{v2['id']}/approve", headers=manager)
    statuses = {
        s["id"]: s["status"]
        for s in test_client.get(
            f"/v1/specifications?company_id={company.id}", headers=manager
        ).json()["specifications"]
    }
    assert statuses == {v1["id"]: "superseded", v2["id"]: "approved"}


# --- diff and impact of a revision -----------------------------------------


@pytest.mark.usefixtures("enforcement")
def test_specification_diff_and_impact_report_changed_criteria(
    sqlite_session, test_client
):
    company = _company(sqlite_session)
    manager = _member(sqlite_session, company.id, "Manager")
    v1 = test_client.post(
        "/v1/specifications", json={"company_id": company.id, **GOOD}, headers=manager
    ).json()
    test_client.post(f"/v1/specifications/{v1['id']}/approve", headers=manager)

    # A revision that rewords one criterion and drops a requirement.
    revised_good = {
        **GOOD,
        "requirements": [GOOD["requirements"][0]],
    }
    revised_good["requirements"][0]["criteria"][0]["then"] = (
        "the list has two rows per task"
    )
    v2 = test_client.post(
        f"/v1/specifications/{v1['id']}/revise", headers=manager
    ).json()
    test_client.put(
        f"/v1/specifications/{v2['id']}", json=revised_good, headers=manager
    )
    test_client.post(f"/v1/specifications/{v2['id']}/approve", headers=manager)

    diff = test_client.get(
        f"/v1/specifications/{v2['id']}/diff?against={v1['id']}", headers=manager
    ).json()
    assert diff["from_version"] == 1 and diff["to_version"] == 2
    d = diff["diff"]
    # The "Move a task" requirement was removed.
    assert "REQ-2" in d["requirements"]["removed"]
    # The reworded criterion is reported as changed.
    assert "REQ-1.1" in d["changed_criterion_ids"]

    # Impact lists no tasks (none planned yet) but reports the changed criteria.
    impact = test_client.get(
        f"/v1/specifications/{v2['id']}/impact", headers=manager
    ).json()
    assert impact["previous_id"] == v1["id"]
    assert "REQ-1.1" in impact["changed_criterion_ids"]
    assert "REQ-2" in impact["changed_requirement_ids"]


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
    # depend on it (``services.acceptance``).
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
    from services.carry_over import carry_over

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


@pytest.mark.parametrize("word", ["fast", "rápido", "etc", "user-friendly"])
def test_vague_words_are_found_whole_and_in_both_languages(word):
    assert spec_check._vague(f"it must be {word} for everyone") == word
    assert spec_check._vague("fastened in 2 steps") is None
