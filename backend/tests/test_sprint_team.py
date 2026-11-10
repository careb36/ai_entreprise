"""A sprint's team (``services/planning/sprint_team.py``): the roles its tasks may go to.

The PO suggests the team for the request in a fixed shape beside the plan,
an unusable answer meaning every hired role; planning with a ``team`` uses
only those roles, in both planners; ``start_sprint`` refuses an unknown role
and an assignee outside the team, stores the team and who chose it
(``team_source``); the worker never runs a task outside it; the company's
``team_selection`` is ``suggested`` (a Manager confirms) or ``automatic``
(the PO's suggestion, also for a sprint the system plans on its own).
"""

import json
import secrets
from unittest.mock import patch

import pytest
import worker_stages
from models import Agent, BacklogItem, Company, Sprint
from services import spec_planning, sprint_planner, sprint_team
from services.loop.worker import close as worker_close
from services.loop.worker import task_run
from sqlmodel import select
from test_work_control import _member, auth_on  # noqa: F401 - fixture used by name.

HIRED = ["BackendDeveloper", "PO", "QA", "TechWriter"]


def _company(session, roles=HIRED, selection=None, autonomy=2):
    company = Company(
        company_name=f"Team {secrets.token_hex(3)}",
        autonomy_level=autonomy,
        team_selection=selection,
    )
    session.add(company)
    session.commit()
    for role in roles:
        session.add(Agent(company_id=company.id, role=role, name=role, model=""))
    session.commit()
    session.refresh(company)
    return company


def _start(client, company, items, **extra):
    return client.post(
        "/v1/start_sprint",
        json={
            "company_id": company.id,
            "sprint_name": "S",
            "start_date": "2026-11-09",
            "scope_size": "M",
            "backlog_items": [
                {"title": f"t{i}", "description": "d", "assignee": a}
                for i, a in enumerate(items)
            ],
            **extra,
        },
    )


# ── The PO's suggestion ──────────────────────────────────────────────────────


def test_the_suggested_team_is_read_from_the_fixed_shape():
    team, reasons = sprint_team.suggested(
        {
            "team": ["TechWriter", "Wizard"],
            "team_reasons": {"TechWriter": "docs only", "QA": "x"},
        },
        HIRED,
    )
    assert team == ["TechWriter"]
    assert reasons == {"TechWriter": "docs only"}


@pytest.mark.parametrize(
    "answer",
    [None, "not json", {"team": "TechWriter"}, {"team": []}, {"team": ["Wizard"]}, {}],
)
def test_an_unusable_answer_suggests_every_hired_role(answer):
    assert sprint_team.suggested(answer, HIRED) == (HIRED, {})


def test_every_role_a_planned_task_goes_to_is_on_the_suggested_team():
    team, _ = sprint_team.suggested(
        {"team": ["TechWriter"]}, HIRED, [{"assignee": "QA"}, {"assignee": "Dev"}]
    )
    assert team == ["QA", "TechWriter"]


def test_aliases_are_one_role():
    assert sprint_team.check_team(
        ["backend-dev", "BackendDeveloper"], ["BackendDev"]
    ) == ["BackendDev"]
    with pytest.raises(sprint_team.TeamRefused, match="Unknown role 'Wizard'"):
        sprint_team.check_team(["Wizard"], HIRED)
    with pytest.raises(sprint_team.TeamRefused, match="at least one role"):
        sprint_team.check_team([], HIRED)


# ── Planning with a team ─────────────────────────────────────────────────────


def _spec_request(team=None, roles=None):
    content = {
        "title": "Docs",
        "summary": "Document it",
        "requirements": [
            {"id": "REQ-1", "title": "Readme", "criteria": [{"id": "REQ-1.1"}]}
        ],
    }
    return {
        "specification_id": 1,
        "company_id": 1,
        "title": "Docs",
        "summary": "Document it",
        "content": content,
        "roles": roles or team or HIRED,
        "hired": HIRED,
        "team": team,
        "item_limit": 8,
        "model": "m",
        "system_prompt": None,
        "prompt": "p",
        "bu_id": None,
    }


def test_the_spec_planner_plans_only_with_the_team_and_reads_the_suggestion(
    monkeypatch,
):
    answer = {
        "sprint_name": "Docs",
        "goal": "g",
        "tasks": [
            {"title": "Readme", "assignee": "BackendDeveloper", "covers": ["REQ-1.1"]}
        ],
        "team": ["TechWriter"],
        "team_reasons": {"TechWriter": "documentation"},
    }
    monkeypatch.setattr(
        spec_planning.spec_author, "ask", lambda *a, **k: json.dumps(answer)
    )
    plan = spec_planning.plan_from(_spec_request(team=["TechWriter"]))
    # The team leaves QA out, so no test-writing task; the task goes to the team.
    assert [(t["title"], t["assignee"]) for t in plan["tasks"]] == [
        ("Readme", "TechWriter")
    ]
    assert plan["suggested_team"] == ["TechWriter"]
    assert plan["team_reasons"] == {"TechWriter": "documentation"}

    unrestricted = spec_planning.plan_from(_spec_request())
    assert [t["assignee"] for t in unrestricted["tasks"]] == ["QA", "BackendDeveloper"]
    assert unrestricted["suggested_team"] == ["BackendDeveloper", "QA", "TechWriter"]


def test_the_spec_planners_fallback_keeps_to_the_team(monkeypatch):
    monkeypatch.setattr(spec_planning.spec_author, "ask", lambda *a, **k: "no json")
    plan = spec_planning.plan_from(_spec_request(team=["TechWriter"]))
    assert {t["assignee"] for t in plan["tasks"]} == {"TechWriter"}
    assert plan["suggested_team"] == ["TechWriter"]
    unrestricted = spec_planning.plan_from(_spec_request())
    assert unrestricted["suggested_team"] == HIRED


class _Client:
    def __init__(self, answer):
        self.answer, self.prompts = answer, []

    def chat(self, messages, model, role):
        self.prompts.append(messages[0]["content"])
        return {"choices": [{"message": {"content": json.dumps(self.answer)}}]}


AGENTS = [{"role": r, "name": r, "model": "m"} for r in HIRED]


def test_the_execution_planner_plans_only_with_the_team(monkeypatch):
    client = _Client(
        {
            "sprint_name": "Docs",
            "goal": "g",
            "tasks": [
                {"title": "Readme", "assignee": "TechWriter"},
                {"title": "API docs", "assignee": "BackendDeveloper"},
            ],
            "team": ["TechWriter"],
        }
    )
    monkeypatch.setattr(sprint_planner, "_client", lambda po: client)
    plan = sprint_planner.plan_execution_sprint(
        "Acme", "Document the project", "S", AGENTS, team=["TechWriter"]
    )
    assert '"assignee": one of: TechWriter\n' in client.prompts[0]
    assert "BackendDeveloper (BackendDeveloper)" not in client.prompts[0]
    assert "The Manager chose this sprint's team: TechWriter" in client.prompts[0]
    assert {t["assignee"] for t in plan["tasks"]} == {"TechWriter"}
    assert plan["suggested_team"] == ["TechWriter"]

    free = sprint_planner.plan_execution_sprint("Acme", "Docs", "S", AGENTS)
    assert '"team": ["<role>", ...]' in client.prompts[1]
    assert free["suggested_team"] == ["BackendDeveloper", "TechWriter"]


def test_plan_sprint_returns_the_suggestion_the_hired_roles_and_the_mode(
    sqlite_session, test_client
):
    company = _company(sqlite_session, selection="automatic")
    body = test_client.post(
        "/v1/plan_sprint",
        json={
            "company_id": company.id,
            "prompt": "Document the project",
            "scope_size": "S",
        },
    ).json()
    # No planning model: the fallback, and every hired role suggested.
    assert body["hired_roles"] == HIRED
    assert body["suggested_team"] == HIRED
    assert body["team_selection"] == "automatic"

    team = test_client.post(
        "/v1/plan_sprint",
        json={
            "company_id": company.id,
            "prompt": "Document it",
            "scope_size": "S",
            "team": ["TechWriter"],
        },
    ).json()
    assert {t["assignee"] for t in team["tasks"]} == {"TechWriter"}
    assert team["suggested_team"] == ["TechWriter"]
    refused = test_client.post(
        "/v1/plan_sprint",
        json={"company_id": company.id, "prompt": "x", "team": ["Wizard"]},
    )
    assert refused.status_code == 422


# ── Starting the sprint ──────────────────────────────────────────────────────


def test_start_sprint_refuses_an_unknown_role_and_an_assignee_outside_the_team(
    sqlite_session, test_client
):
    company = _company(sqlite_session)
    unknown = _start(test_client, company, ["TechWriter"], team=["Wizard"])
    assert unknown.status_code == 422 and "Unknown role" in unknown.json()["detail"]
    outside = _start(test_client, company, ["TechWriter", "QA"], team=["TechWriter"])
    assert outside.status_code == 422
    assert "'QA' is not on this sprint's team" in outside.json()["detail"]
    assert (
        sqlite_session.exec(select(Sprint).where(Sprint.company_id == company.id)).all()
        == []
    )


@pytest.mark.parametrize(
    ("selection", "extra", "team", "source"),
    [
        (None, {}, None, "all"),
        (
            None,
            {"team": ["TechWriter"], "suggested_team": ["TechWriter"]},
            ["TechWriter"],
            "suggested",
        ),
        (
            None,
            {"team": ["TechWriter", "QA"], "suggested_team": ["TechWriter"]},
            ["TechWriter", "QA"],
            "person",
        ),
        ("automatic", {"suggested_team": ["TechWriter"]}, ["TechWriter"], "automatic"),
        ("automatic", {}, None, "all"),
        ("suggested", {"suggested_team": ["TechWriter"]}, None, "all"),
    ],
)
def test_start_sprint_records_the_team_and_who_chose_it(
    sqlite_session, test_client, selection, extra, team, source
):
    company = _company(sqlite_session, selection=selection)
    response = _start(test_client, company, ["TechWriter"], **extra)
    assert response.status_code == 200, response.text
    sprint = sqlite_session.get(Sprint, response.json()["sprint_id"])
    assert (sprint.team, sprint.team_source) == (team, source)
    board = test_client.get(f"/v1/sprints/{sprint.id}/board").json()["sprint"]
    assert (board["team"], board["team_source"]) == (team, source)
    listed = test_client.get("/v1/sprints", params={"company_id": company.id}).json()
    assert (listed[0]["team"], listed[0]["team_source"]) == (team, source)


def test_an_automatic_start_refuses_a_task_outside_the_suggestion(
    sqlite_session, test_client
):
    company = _company(sqlite_session, selection="automatic")
    response = _start(test_client, company, ["QA"], suggested_team=["TechWriter"])
    assert response.status_code == 422


def test_the_assignee_cannot_be_moved_outside_the_team(sqlite_session, test_client):
    company = _company(sqlite_session)
    started = _start(test_client, company, ["TechWriter"], team=["TechWriter", "QA"])
    item_id = started.json()["backlog_item_ids"][0]
    refused = test_client.patch(f"/v1/backlog/{item_id}", json={"assignee": "PO"})
    assert refused.status_code == 422
    assert (
        test_client.patch(f"/v1/backlog/{item_id}", json={"assignee": "QA"}).status_code
        == 200
    )


# ── The worker ───────────────────────────────────────────────────────────────


def test_the_worker_never_runs_a_task_outside_the_team(sqlite_session, monkeypatch):
    worker_stages.use_engine(monkeypatch, sqlite_session.get_bind())
    company = _company(sqlite_session)
    sprint = Sprint(
        company_id=company.id, sprint_name="S", status="active", team=["TechWriter"]
    )
    sqlite_session.add(sprint)
    sqlite_session.commit()
    inside = BacklogItem(sprint_id=sprint.id, title="in", assignee="TechWriter")
    outside = BacklogItem(sprint_id=sprint.id, title="out", assignee="BackendDeveloper")
    sqlite_session.add_all([inside, outside])
    sqlite_session.commit()

    assert task_run._load_task_context(inside.id) is not None
    assert task_run._load_task_context(outside.id) is None
    sqlite_session.expire_all()
    blocked = sqlite_session.get(BacklogItem, outside.id)
    assert blocked.status == "blocked"
    assert blocked.comments[-1]["text"].startswith(sprint_team.OFF_TEAM_PREFIX)
    # Blocked for a person: the sprint can close around it.
    from services.board import item_is_settled

    assert item_is_settled(blocked)
    # Loading it again writes nothing more.
    assert task_run._load_task_context(outside.id) is None
    sqlite_session.expire_all()
    assert len(sqlite_session.get(BacklogItem, outside.id).comments) == 1


def test_an_autonomy_3_follow_up_gets_an_automatic_team(sqlite_session, monkeypatch):
    worker_stages.use_engine(monkeypatch, sqlite_session.get_bind())
    company = _company(sqlite_session, autonomy=3)
    closing = Sprint(company_id=company.id, sprint_name="S1", status="active")
    sqlite_session.add(closing)
    sqlite_session.commit()
    sqlite_session.add(BacklogItem(sprint_id=closing.id, title="Done", status="done"))
    sqlite_session.commit()
    plan = {
        "sprint_name": "S2",
        "goal": "Improve",
        "tasks": [{"title": "Docs", "description": "d", "assignee": "TechWriter"}],
        "suggested_team": ["TechWriter"],
    }
    with (
        patch("services.loop.worker.close.AUTO_SELF_IMPROVEMENT_ENABLED", True),
        patch(
            "services.loop.worker.close.plan_self_improvement_sprint", return_value=plan
        ),
        patch(
            "services.loop.worker.close.build_self_improvement_objective",
            return_value="x",
        ),
    ):
        result = worker_close._complete_sprint_and_plan_follow_up(closing.id)
    assert result["auto_opened"] is True
    sqlite_session.expire_all()
    follow_up = sqlite_session.get(Sprint, result["next_sprint_id"])
    assert (follow_up.team, follow_up.team_source) == (["TechWriter"], "automatic")


def test_a_carried_tasks_role_joins_the_automatic_team():
    sprint = Sprint(company_id=1, sprint_name="S")
    carried = [BacklogItem(id=9, sprint_id=1, title="t", assignee="QA")]
    ids = sprint_team.automatic_team(
        sprint, {"suggested_team": ["TechWriter"]}, carried
    )
    assert ids == [9]
    assert (sprint.team, sprint.team_source) == (["TechWriter", "QA"], "automatic")
    sprint_team.automatic_team(sprint, {}, [])
    assert (sprint.team, sprint.team_source) == (None, "all")


# ── The company's setting ────────────────────────────────────────────────────


def test_team_selection_is_read_by_a_viewer_and_changed_by_a_manager(
    sqlite_session,
    test_client,
    auth_on,  # noqa: F811 - the fixture imported above.
):
    company = _company(sqlite_session)
    manager = _member(sqlite_session, company.id, "Manager")
    viewer = _member(sqlite_session, company.id, "Viewer")
    outsider = _member(sqlite_session, None, "Owner")
    url = f"/v1/companies/{company.id}/team_selection"
    assert test_client.get(url, headers=viewer).json()["team_selection"] == "suggested"
    assert test_client.get(url, headers=outsider).status_code == 403
    body = {"team_selection": "automatic"}
    assert test_client.put(url, json=body, headers=viewer).status_code == 403
    assert (
        test_client.put(
            url, json={"team_selection": "random"}, headers=manager
        ).status_code
        == 422
    )
    assert test_client.put(url, json=body, headers=manager).json()[
        "team_selection"
    ] == ("automatic")
    assert test_client.get(url, headers=viewer).json()["team_selection"] == "automatic"


# ── Review fixes: hired roles only, and carry-over within the team ───────────

ONLY_BACKEND = ["BackendDeveloper", "PO", "TechWriter"]


def test_without_a_team_the_spec_planners_fallback_keeps_to_the_hired_roles(
    monkeypatch,
):
    monkeypatch.setattr(spec_planning.spec_author, "ask", lambda *a, **k: "no json")
    request = {**_spec_request(roles=ONLY_BACKEND), "hired": ONLY_BACKEND}
    plan = spec_planning.plan_from(request)
    # No Developer and no QA hired: the task goes to the backend developer and
    # no test-writing task is added for a role the company lacks.
    assert [t["assignee"] for t in plan["tasks"]] == ["BackendDeveloper"]
    # Every hired role (an unusable answer), and so every task's role: it starts.
    assert plan["suggested_team"] == ONLY_BACKEND

    answer = {"tasks": [{"title": "Readme", "assignee": "Developer"}]}
    monkeypatch.setattr(
        spec_planning.spec_author, "ask", lambda *a, **k: json.dumps(answer)
    )
    assert [t["assignee"] for t in spec_planning.plan_from(request)["tasks"]] == [
        "BackendDeveloper"
    ]


@pytest.mark.parametrize(
    "planner",
    [sprint_planner.plan_execution_sprint, sprint_planner.plan_self_improvement_sprint],
)
def test_without_a_team_the_planners_fallback_keeps_to_the_hired_roles(planner):
    agents = [{"role": r, "name": r, "model": ""} for r in ["BackendDeveloper"]]
    plan = planner("Acme", "Build it", "S", agents)
    assert plan["tasks"]
    assert {t["assignee"] for t in plan["tasks"]} == {"BackendDeveloper"}
    assert plan["suggested_team"] == ["BackendDeveloper"]


def test_the_self_improvement_plan_keeps_to_the_team_sent(sqlite_session, test_client):
    company = _company(sqlite_session)
    url = "/v1/plan_self_improvement_sprint"
    body = test_client.post(
        url, json={"company_id": company.id, "scope_size": "S", "team": ["TechWriter"]}
    ).json()
    assert {t["assignee"] for t in body["tasks"]} == {"TechWriter"}
    assert body["suggested_team"] == ["TechWriter"]
    refused = test_client.post(url, json={"company_id": company.id, "team": ["Wizard"]})
    assert refused.status_code == 422


def _blocked_task(session, company, assignee="BackendDeveloper", status="blocked"):
    from test_board_moves import NEEDS_WORK, TRIAGE

    source = Sprint(company_id=company.id, sprint_name="Old", status="completed")
    session.add(source)
    session.commit()
    comments = [NEEDS_WORK, NEEDS_WORK, {"author": "Quality gate", "text": TRIAGE}]
    item = BacklogItem(
        sprint_id=source.id,
        title="Endpoint",
        description="d",
        assignee=assignee,
        status=status,
        comments=comments if status == "blocked" else [],
    )
    session.add(item)
    session.commit()
    return item


@pytest.mark.parametrize("status", ["blocked", "todo"])
def test_a_task_off_the_target_sprints_team_is_not_moved_and_keeps_its_carry_over(
    sqlite_session, test_client, monkeypatch, status
):
    from services import carry_over

    worker_stages.use_engine(monkeypatch, sqlite_session.get_bind())
    company = _company(sqlite_session)
    item = _blocked_task(sqlite_session, company, status=status)
    docs = Sprint(
        company_id=company.id, sprint_name="Docs", status="active", team=["TechWriter"]
    )
    sqlite_session.add(docs)
    sqlite_session.commit()

    refused = test_client.post(
        f"/v1/backlog/{item.id}/carry_over", json={"target_sprint_id": docs.id}
    )
    assert refused.status_code == 409
    assert "not on sprint" in refused.json()["detail"]
    sqlite_session.expire_all()
    original = sqlite_session.get(BacklogItem, item.id)
    assert original.status == status
    assert not carry_over.carried_over(original)
    clones = sqlite_session.exec(
        select(BacklogItem).where(BacklogItem.sprint_id == docs.id)
    ).all()
    assert clones == []

    # A sprint whose team has the role takes it.
    team = Sprint(
        company_id=company.id,
        sprint_name="API",
        status="active",
        team=["BackendDeveloper"],
    )
    sqlite_session.add(team)
    sqlite_session.commit()
    moved = test_client.post(
        f"/v1/backlog/{item.id}/carry_over", json={"target_sprint_id": team.id}
    )
    assert moved.status_code == 200, moved.text


def test_start_sprint_refuses_a_carried_task_off_its_team_before_marking_it(
    sqlite_session, test_client
):
    from services import carry_over

    company = _company(sqlite_session)
    item = _blocked_task(sqlite_session, company)
    response = _start(
        test_client,
        company,
        ["TechWriter"],
        team=["TechWriter"],
        carry_over_ids=[item.id],
    )
    assert response.status_code == 409
    assert "not on sprint" in response.json()["detail"]
    sqlite_session.expire_all()
    assert not carry_over.carried_over(sqlite_session.get(BacklogItem, item.id))
