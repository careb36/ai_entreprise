"""The PO checks the code before it plans (owner, 2026-11-24).

Sprint 8 was planned from an issue whose option, tests and docs already
existed; a Developer spent fifteen tool rounds finding out. With its read
tools the PO is now asked to check first what is already done and to name it
in ``already_done``. What it says is a claim (``already_done_claim``); what
it read, with the daemon's sha256, is the backend's observation
(``already_done_observed``), never in the same field. A claimed path it did
not read is flagged; a plan whose every part is claimed done is one QA task
that verifies it with the tests. Without reads, today's plan and no claim.
"""

from __future__ import annotations

import json
import secrets

import developer_tools_helpers
import pytest
from config import settings
from developer_tools_helpers import (
    _answer,
    _answer_with_calls,
    _call,
    _load_daemon,
    _Model,
    _Requester,
)
from models import Agent, AuditLog, Company, Sprint
from routers import sprint_planning as planning_router
from services.loop import sprint_archive
from services.planning import (
    already_done,
    planner_fallback,
    planning_reads,
    sprint_planner,
)
from services.tools import developer_tools, mcp_tools
from sqlmodel import select

project = developer_tools_helpers.project

OBJECTIVE = "Add a --greet option to the CLI, with a test and docs"
TWO_TASKS = [
    {"title": "Document the option", "assignee": "Developer"},
    {"title": "Test it", "assignee": "QA"},
]
CLAIM = [
    {
        "part": "the greet function",
        "evidence": "src/greet.py defines greet()",
        "paths": ["src/greet.py"],
    },
    {"part": "its test", "evidence": "in tests/test_greet.py", "paths": []},
]


def _plan_json(tasks, claim=None):
    body = {"sprint_name": "S", "goal": "G", "tasks": tasks}
    if claim is not None:
        body["already_done"] = claim
    return json.dumps(body)


@pytest.fixture()
def daemon(monkeypatch, project):
    """The worker's daemon bound to *project*, no MCP servers, no audit writes."""
    monkeypatch.setattr(
        developer_tools, "_request_from_thread", _Requester(_load_daemon())
    )
    monkeypatch.setattr(
        developer_tools,
        "_bridge",
        {"nats": object(), "resolve_root": lambda company_id: str(project)},
    )
    monkeypatch.setattr(
        mcp_tools.Toolbox, "load", classmethod(lambda cls, *a, **k: None)
    )
    monkeypatch.setattr(planning_reads, "record_agent_turn", lambda **k: None)
    monkeypatch.setattr(settings, "BOARD_TOOLS_ENABLED", False)


def _plan(monkeypatch, model):
    monkeypatch.setattr(sprint_planner, "_client", lambda po: model)
    agents = [
        {"role": r, "name": r, "model": "m", "company_id": 1}
        for r in ("PO", "Developer", "QA")
    ]
    return sprint_planner.plan_execution_sprint(
        "Acme", OBJECTIVE, "S", agents, file_listing="src/"
    )


def _reads_then(answer):
    return _Model(
        [_answer_with_calls(_call("workspace__read", path="src/greet.py")), answer]
    )


def test_with_reads_the_po_is_asked_to_check_the_code_first(daemon, monkeypatch):
    model = _reads_then(_answer(_plan_json(TWO_TASKS)))
    plan = _plan(monkeypatch, model)
    asked = model.requests[0]["messages"][-1]["content"]
    assert "already exists in the code" in asked
    assert '"already_done"' in asked and "Plan tasks only for what is missing" in asked
    # Nothing claimed: no claim fields, today's plan.
    assert "already_done_claim" not in plan and len(plan["tasks"]) == 2


def test_the_claim_and_the_observed_reads_stay_apart(daemon, monkeypatch):
    model = _reads_then(_answer(_plan_json(TWO_TASKS, CLAIM)))
    plan = _plan(monkeypatch, model)
    claim, observed = plan["already_done_claim"], plan["already_done_observed"]
    # The PO's words: parts, evidence, the paths it named; no hash.
    assert [c["part"] for c in claim] == ["the greet function", "its test"]
    assert claim[0]["paths"] == ["src/greet.py"]
    assert claim[1]["paths"] == ["tests/test_greet.py"]  # From its evidence.
    assert "sha256" not in json.dumps(claim)
    # The backend's: the file read, with the sha256 the daemon reported.
    daemon_sha = plan["planning_reads"][0]["sha256"]
    assert observed["files_read"] == [
        {
            "path": "src/greet.py",
            "sha256": daemon_sha,
            "bytes": plan["planning_reads"][0]["bytes"],
            "tool": "read",
            "round": 1,
        }
    ]
    assert len(plan["tasks"]) == 2 and plan["planner_fallback"] is None


def test_a_claimed_path_the_po_never_read_is_flagged(daemon, monkeypatch):
    plan = _plan(monkeypatch, _reads_then(_answer(_plan_json(TWO_TASKS, CLAIM))))
    checked = {c["path"]: c for c in plan["already_done_observed"]["claimed_paths"]}
    assert checked["src/greet.py"]["read"] is True
    assert checked["src/greet.py"]["files"][0]["sha256"]
    assert checked["tests/test_greet.py"] == {
        "path": "tests/test_greet.py",
        "read": False,
        "files": [],
    }


def test_every_part_done_is_one_qa_verification_task(daemon, monkeypatch):
    plan = _plan(monkeypatch, _reads_then(_answer(_plan_json([], CLAIM))))
    assert plan["planner_fallback"] is None
    [task] = plan["tasks"]
    assert task["assignee"] == "QA" and task["depends_on"] == []
    assert task["description"].startswith(f"Verify that {OBJECTIVE} is already met")
    assert "run the tests that cover it" in task["description"]
    assert "src/greet.py" in task["description"]
    assert plan["already_done_claim"][0]["part"] == "the greet function"


def test_one_task_left_beside_a_claim_is_a_plan(daemon, monkeypatch):
    plan = _plan(monkeypatch, _reads_then(_answer(_plan_json(TWO_TASKS[:1], CLAIM))))
    assert plan["planner_fallback"] is None
    assert [t["title"] for t in plan["tasks"]] == ["Document the option"]


def test_without_reads_the_plan_is_todays_and_keeps_no_claim(daemon, monkeypatch):
    monkeypatch.setattr(developer_tools, "_bridge", {})  # No daemon.
    model = _Model([_answer(_plan_json(TWO_TASKS, CLAIM))])
    plan = _plan(monkeypatch, model)
    assert (
        "already exists in the code" not in model.requests[0]["messages"][-1]["content"]
    )
    assert "already_done_claim" not in plan and "already_done_observed" not in plan
    assert len(plan["tasks"]) == 2
    # No tasks without reads is still too few: today's fallback, no QA verification.
    plan = _plan(monkeypatch, _Model([_answer(_plan_json([], CLAIM))]))
    assert plan["planner_fallback"]["reason"] == "too_few_tasks"


def test_a_failed_or_searched_read_is_no_file_read():
    reads = [
        {"tool": "read", "path": "a.py", "sha256": "x", "is_error": True},
        {
            "tool": "search",
            "arguments": {"pattern": "greet", "path": "src"},
            "match_count": 2,
            "is_error": False,
        },
        {"tool": "read", "path": "./src/b.py", "sha256": "y", "round": 2},
    ]
    claim = already_done.claim_of(
        {"already_done": [{"part": "p", "paths": ["a.py", "src/", "b.py"]}]}
    )
    seen = already_done.observed(reads, claim)
    assert [f["path"] for f in seen["files_read"]] == ["src/b.py"]
    assert seen["searches"][0]["pattern"] == "greet"
    flags = {c["path"]: c["read"] for c in seen["claimed_paths"]}
    assert flags == {"a.py": False, "src/": True, "b.py": True}


# ── The record ───────────────────────────────────────────────────────────────


def _company(session):
    company = Company(company_name=f"Done {secrets.token_hex(3)}")
    session.add(company)
    session.commit()
    for role in ("PO", "Developer", "QA"):
        session.add(Agent(company_id=company.id, role=role, name=role, model="m"))
    session.commit()
    session.refresh(company)
    return company


def test_the_claim_is_recorded_and_carried_to_the_sprint(
    sqlite_session, test_client, monkeypatch
):
    company = _company(sqlite_session)
    claim = already_done.claim_of({"already_done": CLAIM})
    observed = already_done.observed(
        [{"tool": "read", "path": "src/greet.py", "sha256": "abc", "round": 1}], claim
    )
    task = already_done.verification_task(OBJECTIVE, claim)

    def planned(**kwargs):
        return {
            "sprint_name": "S",
            "goal": "G",
            "tasks": [task],
            "objective": OBJECTIVE,
            "planner_fallback": None,
            "already_done_claim": claim,
            "already_done_observed": observed,
        }

    monkeypatch.setattr(planning_router, "plan_execution_sprint", planned)
    response = test_client.post(
        "/v1/plan_sprint",
        json={"company_id": company.id, "prompt": OBJECTIVE, "scope_size": "S"},
    )
    assert response.status_code == 200, response.text
    plan = response.json()
    assert plan["already_done_claim"] == claim
    assert plan["already_done_observed"] == observed and plan["plan_id"]
    row = sqlite_session.exec(
        select(AuditLog).where(
            AuditLog.interaction_id
            == planner_fallback.plan_ref(company.id, plan["plan_id"])
        )
    ).one()
    assert row.action == already_done.ACTION
    assert row.result == {"claim": claim} and row.details["observed"] == observed

    started = test_client.post(
        "/v1/start_sprint",
        json={
            "company_id": company.id,
            "sprint_name": "S",
            "start_date": "2026-11-24",
            "scope_size": "S",
            "goal": "G",
            "objective": OBJECTIVE,
            "backlog_items": plan["tasks"],
            "plan_id": plan["plan_id"],
        },
    )
    assert started.status_code == 200, started.text
    sprint = sqlite_session.get(Sprint, started.json()["sprint_id"])
    carried = sqlite_session.exec(
        select(AuditLog).where(
            AuditLog.interaction_id == planner_fallback.sprint_ref(sprint.id),
            AuditLog.action == already_done.ACTION,
        )
    ).one()
    assert carried.result == {"claim": claim}
    assert carried.details["observed"] == observed
    assert carried.details["plan_audit_id"] == row.id
    # The record is no work, and no fallback is shown for it.
    assert planner_fallback.of_sprint(sqlite_session, sprint) is None
    assert sprint_archive.evidence_of(sqlite_session, sprint) == []
