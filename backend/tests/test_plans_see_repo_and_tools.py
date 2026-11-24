"""A plan gives runnable work to a role that can run it, and the PO sees the repository.

Audit of sprint 6 (issue #766): a task the PO gave the Architect ran five
times with no tool call and ended blocked ("there is no shell/exec"), and one
given to Security ran with no tool at all; only ``roles.FILE_ROLES`` get the
workspace tools. ``sprint_team.fit_tasks`` now moves such a task to a file
role, with ``reassigned_from`` and a note saying why. And the PO's listing of
the project was read from the backend container, where the user's project is
not mounted ("(project path not accessible)"); it now comes from the
workspace daemon (``sprint_context.project_listing``), as the Architect's.
"""

import asyncio
import json

from services.planning import (
    spec_author,
    spec_planning,
    sprint_context,
    sprint_planner,
    sprint_team,
)
from services.workspace import workspace_daemon

HIRED = ["PO", "Architect", "Security", "TechWriter", "Developer", "QA", "DevOps"]


def _fit(title, assignee, allowed=HIRED, **extra):
    task = {"title": title, "description": "As planned.", "assignee": assignee}
    return sprint_team.fit_tasks([{**task, **extra}], allowed)[0]


# ── Runnable work goes to a role with the workspace tools ───────────────────


def test_a_validation_given_to_the_architect_goes_to_qa_and_says_why():
    task = _fit("Run the validation scripts", "Architect")

    assert task["assignee"] == "QA"
    assert task["reassigned_from"] == "Architect"
    assert task["description"].startswith("As planned.")
    assert "Reassigned from Architect" in task["description"]
    assert "workspace tools" in task["description"]


def test_a_change_given_to_security_goes_to_a_developer():
    task = _fit("Audit and fix the dependency pins", "Security")

    assert task["assignee"] == "Developer"
    assert task["reassigned_from"] == "Security"


def test_deployment_work_goes_to_devops_and_spanish_titles_are_read():
    assert _fit("Deploy the CI pipeline", "Architect")["assignee"] == "DevOps"
    assert _fit("Ejecutar las pruebas de humo", "PO")["assignee"] == "QA"


def test_writing_and_decisions_stay_where_the_po_put_them():
    for title, role in (
        ("Design implementation slice", "Architect"),
        ("Write the threat model", "Security"),
        ("Document the testing strategy", "TechWriter"),
        ("Update the user guide", "TechWriter"),
    ):
        task = _fit(title, role)
        assert task["assignee"] == role and "reassigned_from" not in task, title


def test_the_techwriter_moves_only_when_the_title_runs_something():
    task = _fit("Verify every link in the docs", "TechWriter")

    assert task["assignee"] == "QA" and task["reassigned_from"] == "TechWriter"


def test_acceptance_tests_file_roles_and_teams_without_one_are_left_alone():
    kept = _fit("Run the acceptance tests", "Architect", kind="acceptance_tests")
    assert kept["assignee"] == "Architect" and "reassigned_from" not in kept
    assert "reassigned_from" not in _fit("Run the tests", "QA")
    # No file role on the team: nobody can run it, so nothing is moved.
    alone = _fit("Run the tests", "Architect", allowed=["Architect", "TechWriter"])
    assert alone["assignee"] == "Architect" and "reassigned_from" not in alone


def test_the_description_stays_within_the_limit():
    from services.contracts.task_text import DESCRIPTION_MAX

    long = {"title": "Run it", "description": "x" * DESCRIPTION_MAX}
    task = sprint_team.fit_tasks([{**long, "assignee": "Architect"}], HIRED)[0]

    assert len(task["description"]) == DESCRIPTION_MAX
    assert task["description"].endswith("(issue #766).")


class _Client:
    def __init__(self, answer):
        self.answer, self.prompts = answer, []

    def chat(self, messages, model, role):
        self.prompts.append(messages[0]["content"])
        return {"choices": [{"message": {"content": self.answer}}]}


ANSWER = json.dumps(
    {
        "goal": "Check the split",
        "tasks": [
            {"title": "Split the module", "assignee": "Developer"},
            {"title": "Validate the split with the scripts", "assignee": "Architect"},
        ],
    }
)
AGENTS = [{"role": r, "name": r, "model": "m"} for r in HIRED]


def test_an_execution_plan_gives_the_validation_to_qa(monkeypatch):
    monkeypatch.setattr(sprint_planner, "_client", lambda po: _Client(ANSWER))

    plan = sprint_planner.plan_execution_sprint("Acme", "Split it", "M", AGENTS)

    validate = plan["tasks"][1]
    assert validate["assignee"] == "QA" and validate["reassigned_from"] == "Architect"
    assert "QA" in plan["suggested_team"]


def test_the_fallback_s_refine_task_stays_with_the_po():
    """Its description quotes the person's objective: only titles are read."""
    plan = sprint_planner.plan_execution_sprint(
        "Acme", "Run the tests and verify the build", "M", AGENTS
    )

    assert plan["tasks"][0]["assignee"] == "PO"
    assert "reassigned_from" not in plan["tasks"][0]


def test_a_plan_from_a_specification_gives_the_check_to_qa(monkeypatch):
    spec = {
        "title": "Login",
        "requirements": [
            {"id": "REQ-1", "title": "Login", "criteria": [{"id": "REQ-1.1"}]}
        ],
    }
    answer = json.dumps(
        {
            "tasks": [
                {
                    "title": "Build login",
                    "assignee": "Developer",
                    "covers": ["REQ-1.1"],
                },
                {"title": "Verify the login flow", "assignee": "Security"},
            ]
        }
    )
    monkeypatch.setattr(spec_author, "ask", lambda *a, **k: answer)
    request = {
        "specification_id": 1,
        "company_id": 1,
        "title": "Login",
        "summary": "",
        "content": spec,
        "roles": HIRED,
        "hired": HIRED,
        "team": None,
        "item_limit": 8,
        "model": "m",
        "system_prompt": "",
        "prompt": "",
        "bu_id": None,
    }

    plan = spec_planning.plan_from(request)

    verify = next(t for t in plan["tasks"] if t["title"] == "Verify the login flow")
    assert verify["assignee"] == "QA" and verify["reassigned_from"] == "Security"


# ── The PO's listing of the project comes from the daemon ───────────────────


def _daemon(monkeypatch, reply=None, error=None):
    asked = []

    async def request(nats_client, subject, payload, timeout):
        asked.append((subject, payload))
        if error:
            raise error
        return reply

    monkeypatch.setattr(workspace_daemon, "daemon_request", request)
    return asked


def test_the_listing_is_the_daemon_s_two_top_levels(monkeypatch):
    asked = _daemon(
        monkeypatch,
        {"entries": ["src/", "src/app.py", "README.md"], "truncated": True},
    )

    text = asyncio.run(sprint_context.project_listing(object(), "/repo"))

    assert asked == [
        ("workspace.list", {"workspace_root": "/repo", "path": None, "depth": 2})
    ]
    assert text == "  src/\n  src/app.py\n  README.md\n  ... and more"


def test_without_a_daemon_the_backend_s_own_listing_is_said(monkeypatch):
    _daemon(monkeypatch, error=workspace_daemon.DaemonError(503, "no daemon"))
    missing = "/no/such/project/for/this/test"

    assert (
        asyncio.run(sprint_context.project_listing(object(), missing))
        == "(project path not accessible)"
    )
    assert (
        asyncio.run(sprint_context.project_listing(None, missing))
        == "(project path not accessible)"
    )
    assert asyncio.run(sprint_context.project_listing(None, None)) == (
        "(no project path)"
    )


def test_the_plan_prompt_carries_the_daemon_s_listing(
    sqlite_session, test_client, monkeypatch
):
    import secrets

    from models import Agent, Company, ProjectDiscovery

    company = Company(company_name=f"Repo {secrets.token_hex(3)}")
    sqlite_session.add(company)
    sqlite_session.commit()
    for role in ("PO", "Developer", "QA"):
        sqlite_session.add(
            Agent(company_id=company.id, role=role, name=role, model="m")
        )
    sqlite_session.add(
        ProjectDiscovery(company_id=company.id, project_path="/home/user/project")
    )
    sqlite_session.commit()
    _daemon(monkeypatch, {"entries": ["pyproject.toml", "src/"], "truncated": False})
    client = _Client(ANSWER)
    monkeypatch.setattr(sprint_planner, "_client", lambda po: client)
    # The test app's NATS client is a fake; the daemon's reply is the one above.
    assert test_client.app.state.nats_client is not None

    planned = test_client.post(
        "/v1/plan_sprint",
        json={"company_id": company.id, "prompt": "Split it", "scope_size": "M"},
    )

    assert planned.status_code == 200, planned.text
    assert "  pyproject.toml\n  src/" in client.prompts[0]
    assert "(project path not accessible)" not in client.prompts[0]


def test_the_follow_up_sprint_is_planned_with_the_daemon_s_listing(
    sqlite_session, monkeypatch
):
    import secrets

    from models import Company, ProjectDiscovery
    from services.loop.worker import close

    company = Company(company_name=f"Next {secrets.token_hex(3)}")
    sqlite_session.add(company)
    sqlite_session.commit()
    sqlite_session.add(ProjectDiscovery(company_id=company.id, project_path="/p"))
    sqlite_session.commit()
    seen = {}

    def plan(**kwargs):
        seen.update(kwargs)
        return {"tasks": []}

    monkeypatch.setattr(close, "plan_self_improvement_sprint", plan)

    close._plan_follow_up(sqlite_session, company, None, lambda path: f"listed {path}")

    assert seen["file_listing"] == "listed /p"
