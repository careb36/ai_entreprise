"""When the sprint planner's model does not answer usably, nothing the person
wrote is lost and the fallback is visible.

- The objective is never cut: the fallback's goal, the PO's task and the
  sprint's stored ``objective`` carry it whole; a display name is a label
  with an ellipsis and never touches the stored text (``objective_text``).
- An objective with explicit items (Markdown headings or a numbered list)
  keeps them as tasks, each with its text as written; items beyond the scope
  are named, never dropped silently.
- The fallback is recorded with its reason as the backend observed it, at
  planning and on the sprint, read back from the backend's own row and never
  from the request body (``planner_fallback``); the board shows it.
"""

import secrets

import pytest
from models import Agent, AuditLog, BacklogItem, Company, Sprint
from services.loop import sprint_archive
from services.planning import planner_fallback, spec_planning, sprint_planner
from services.planning.objective_text import display_name, objective_items
from sqlmodel import select

ROLES = ["PO", "Architect", "Developer", "QA"]
EARS = (
    "- WHEN the person {verb} the {thing}, THE SYSTEM SHALL record it whole "
    "and show it on the board without cutting a single character of it.\n"
)


def _item(number: int, lines: int) -> str:
    body = "".join(
        EARS.format(verb=f"submits form {number}.{i}", thing=f"field {i}")
        for i in range(lines)
    )
    return f"## {number}. Item {number}: keep the person's input\n\n{body}"


def _objective(items: int = 3, size: int = 5000) -> str:
    """A long, structured sprint prompt: a preamble and *items* EARS items."""
    lines = 1
    while True:
        text = "# Sprint: three items with EARS criteria\n\nContext first.\n\n" + (
            "\n".join(_item(n, lines) for n in range(1, items + 1))
        )
        if len(text) >= size:
            return text
        lines += 1


def _agents(model=""):
    return [{"role": r, "name": r, "model": model} for r in ROLES]


class _Client:
    def __init__(self, answer=None, error=None):
        self.answer, self.error = answer, error

    def chat(self, messages, model, role):
        if self.error:
            return {"error": self.error}
        return {"choices": [{"message": {"content": self.answer}}]}


# ── The text ─────────────────────────────────────────────────────────────────


def test_items_are_read_from_headings_or_a_numbered_list_without_a_model():
    headed = "# Title\n\nintro\n\n## One\nfirst\n\n## Two\nsecond\n"
    assert objective_items(headed) == [
        {"title": "One", "text": "## One\nfirst"},
        {"title": "Two", "text": "## Two\nsecond"},
    ]
    numbered = "Do this:\n1. **First** thing\n   - WHEN a\n2) Second thing\n"
    assert [i["title"] for i in objective_items(numbered)] == [
        "First thing",
        "Second thing",
    ]
    assert objective_items(numbered)[0]["text"] == "1. **First** thing\n   - WHEN a"
    # One item, prose, or headings inside a code fence are no list.
    assert objective_items("## Only one\ntext") == []
    assert objective_items("Build the login page and test it.") == []
    assert objective_items("```\n## a\n## b\n```\n") == []


def test_a_display_name_is_a_marked_label_and_the_text_is_untouched():
    text = "# A very long first line that goes on and on past the limit\nmore"
    before = str(text)
    name = display_name(text, 20)
    assert name.endswith("…") and len(name) <= 20
    assert text == before
    # A short single line is itself; more lines below it are marked.
    assert display_name("Ship it", 40) == "Ship it"
    assert display_name("Ship it\nand the rest", 40) == "Ship it…"


# ── The fallback plan ────────────────────────────────────────────────────────


def test_a_5000_character_objective_survives_the_fallback_whole():
    objective = _objective()
    assert len(objective) >= 5000
    plan = sprint_planner.plan_execution_sprint("Acme", objective, "S", _agents())

    assert plan["planner_fallback"]["reason"] == "no_model"
    assert plan["objective"] == objective
    assert plan["goal"] == objective
    po, *items = plan["tasks"]
    assert po["assignee"] == "PO" and objective in po["description"]
    assert len(items) == 3
    for task, item in zip(items, objective_items(objective), strict=True):
        assert item["text"] in task["description"]
        assert task["assignee"] == "Developer"
        assert len(task["title"]) <= 80
    # Together the items hold every line after the preamble.
    body = objective[objective.index("## 1.") :].rstrip()
    assert "\n\n".join(i["text"] for i in objective_items(objective)) == body
    assert plan["sprint_name"].startswith("Sprint - Sprint: three items")
    assert plan["unplanned_items"] == []


def test_items_beyond_the_scope_are_named_never_dropped():
    objective = _objective(items=5, size=0)
    plan = sprint_planner.plan_execution_sprint("Acme", objective, "S", _agents())
    assert len(plan["tasks"]) == 4  # the PO's and three items: scope S
    assert plan["unplanned_items"] == [
        "4. Item 4: keep the person's input",
        "5. Item 5: keep the person's input",
    ]


def test_prose_keeps_the_generic_plan_with_the_objective_whole():
    objective = "Make the login page accessible. " * 200
    plan = sprint_planner.plan_execution_sprint("Acme", objective, "M", _agents())
    assert [t["assignee"] for t in plan["tasks"]] == ROLES
    assert objective in plan["tasks"][0]["description"]
    assert plan["goal"] == objective


@pytest.mark.parametrize(
    ("client", "reason", "detail"),
    [
        (_Client(error="timeout after 30s"), "model_error", "timeout after 30s"),
        (_Client(answer="I cannot help"), "unparseable", "JSONDecodeError"),
        (_Client(answer='{"tasks": [{"title": "one"}]}'), "too_few_tasks", "1 usable"),
    ],
)
def test_the_reason_is_the_one_observed(monkeypatch, client, reason, detail):
    monkeypatch.setattr(sprint_planner, "_client", lambda po: client)
    plan = sprint_planner.plan_execution_sprint("Acme", "Ship", "S", _agents("m"))
    assert plan["planner_fallback"]["reason"] == reason
    assert detail in plan["planner_fallback"]["detail"]


def test_a_usable_answer_is_no_fallback_and_the_objective_stays_apart(monkeypatch):
    answer = (
        '{"sprint_name": "S", "goal": "The model\'s line", "tasks": '
        '[{"title": "a", "assignee": "PO"}, {"title": "b", "assignee": "QA"}]}'
    )
    monkeypatch.setattr(sprint_planner, "_client", lambda po: _Client(answer=answer))
    objective = _objective()
    plan = sprint_planner.plan_execution_sprint("Acme", objective, "S", _agents("m"))
    assert plan["planner_fallback"] is None
    assert plan["goal"] == "The model's line"
    assert plan["objective"] == objective


def test_the_spec_planners_note_is_never_cut():
    note = _objective()
    prompt = spec_planning.plan_prompt("PO", {"title": "t"}, "S", 4, "r", ["PO"], note)
    assert note.strip() in prompt


# ── The record and the board ─────────────────────────────────────────────────


def _company(session):
    company = Company(company_name=f"Fallback {secrets.token_hex(3)}")
    session.add(company)
    session.commit()
    for role in ROLES:
        session.add(Agent(company_id=company.id, role=role, name=role, model=""))
    session.commit()
    session.refresh(company)
    return company


def _plan(client, company, objective):
    response = client.post(
        "/v1/plan_sprint",
        json={"company_id": company.id, "prompt": objective, "scope_size": "S"},
    )
    assert response.status_code == 200, response.text
    return response.json()


def _start(client, company, plan, **extra):
    response = client.post(
        "/v1/start_sprint",
        json={
            "company_id": company.id,
            "sprint_name": plan["sprint_name"],
            "start_date": "2026-11-20",
            "scope_size": "S",
            "goal": plan["goal"],
            "objective": plan["objective"],
            "backlog_items": plan["tasks"],
            **extra,
        },
    )
    assert response.status_code == 200, response.text
    return response.json()["sprint_id"]


def test_the_fallback_is_recorded_and_shown_on_the_board(sqlite_session, test_client):
    company = _company(sqlite_session)
    objective = _objective()
    plan = _plan(test_client, company, objective)
    assert plan["planner_fallback"]["reason"] == "no_model"
    assert plan["objective"] == objective and plan["plan_id"]
    planned = sqlite_session.exec(
        select(AuditLog).where(
            AuditLog.interaction_id
            == planner_fallback.plan_ref(company.id, plan["plan_id"])
        )
    ).one()
    assert planned.action == "planner_fallback"
    assert planned.result["reason"] == "no_model"

    sprint_id = _start(test_client, company, plan, plan_id=plan["plan_id"])
    sprint = sqlite_session.get(Sprint, sprint_id)
    assert sprint.objective == objective
    assert sprint.goal == objective
    descriptions = [
        i.description
        for i in sqlite_session.exec(
            select(BacklogItem).where(BacklogItem.sprint_id == sprint_id)
        ).all()
    ]
    assert any(objective in d for d in descriptions)
    for item in objective_items(objective):
        assert any(item["text"] in d for d in descriptions)

    board = test_client.get(f"/v1/sprints/{sprint_id}/board").json()["sprint"]
    assert board["objective"] == objective
    assert board["planner_fallback"]["reason"] == "no_model"
    assert board["planner_fallback"]["recorded_at"]
    # The record is no work: an empty sprint can still be deleted.
    assert sprint_archive.evidence_of(sqlite_session, sprint) == []


def test_a_plan_id_the_backend_did_not_record_records_nothing(
    sqlite_session, test_client
):
    company, other = _company(sqlite_session), _company(sqlite_session)
    plan = _plan(test_client, company, "Ship the thing")
    elsewhere = _plan(test_client, other, "Ship another thing")
    forged = _start(test_client, company, plan, plan_id="0" * 32)
    borrowed = _start(test_client, company, plan, plan_id=elsewhere["plan_id"])
    for sprint_id in (forged, borrowed):
        board = test_client.get(f"/v1/sprints/{sprint_id}/board").json()["sprint"]
        assert board["planner_fallback"] is None


def test_a_sprint_the_platform_planned_records_its_fallback(sqlite_session):
    company = _company(sqlite_session)
    sprint = Sprint(company_id=company.id, sprint_name="Next", objective="Improve")
    sqlite_session.add(sprint)
    sqlite_session.flush()
    assert planner_fallback.record_sprint(sqlite_session, sprint, None) is None
    planner_fallback.record_sprint(
        sqlite_session, sprint, {"reason": "model_error", "detail": "HTTP 503"}
    )
    sqlite_session.commit()
    seen = planner_fallback.of_sprint(sqlite_session, sprint)
    assert (seen["reason"], seen["detail"]) == ("model_error", "HTTP 503")
