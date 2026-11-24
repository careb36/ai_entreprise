"""The step sets the tool ceiling; the role only narrows (owner, 2026-11-24).

The audit of 108 runs (``docs/design/agent-toolsets.md``) found tasks given to
the Architect, SecOps or the TechWriter run with no tool at all, because only
``roles.FILE_ROLES`` got the workspace tools. The families a turn may be
offered now follow its step (``services/contracts/tool_steps.py``): an
execution turn of any role reads, writes where edits may go, runs and
researches; a brief, a review, a planning or a ScrumMaster turn only reads
(the brief and the authors may also research). A role's deny set drops
families within its step and never adds one. The PO's planning and the
ScrumMaster's decision read the project through the same read-only toolbox,
and fall back to today's single call without a daemon or a root.
"""

from __future__ import annotations

import asyncio

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
from services.contracts import roles, tool_steps
from services.contracts.tool_steps import READ, RESEARCH, RUN, WRITE
from services.learning import scrum_master
from services.loop.worker import execute as worker_execute
from services.loop.worker import triage_reads
from services.planning import planning_reads, sprint_planner
from services.tools import (
    developer_tools,
    mcp_tools,
    review_tools,
    workspace_edits,
)
from services.workspace import stack_image

project = developer_tools_helpers.project

READS = {
    "workspace__list",
    "workspace__read",
    "workspace__search",
    "workspace__find_file",
}
WRITES = {"workspace__edit", "workspace__move", "workspace__delete"}
RUNS = {"workspace__run_tests"}
PLAN_JSON = (
    '{"sprint_name": "S", "goal": "G", "tasks": ['
    '{"title": "Read the code", "assignee": "Developer"},'
    '{"title": "Test it", "assignee": "QA"}]}'
)
DECISION = "DECISION: retry\nBRIEF: read the failing test first\nWHY: fixable\nEND\n"


def _names(box):
    return {s["function"]["name"] for s in (box.schemas() if box else [])}


@pytest.fixture()
def daemon(monkeypatch, project):
    """A daemon for *project* as the worker binds it, a test command, edits
    allowed there, no MCP servers, no sandbox to read."""
    requester = _Requester(_load_daemon())
    monkeypatch.setattr(developer_tools, "_request_from_thread", requester)
    monkeypatch.setattr(
        developer_tools,
        "_bridge",
        {"nats": object(), "resolve_root": lambda company_id: str(project)},
    )
    monkeypatch.setattr(
        mcp_tools.Toolbox, "load", classmethod(lambda cls, *a, **k: None)
    )
    monkeypatch.setattr(
        developer_tools, "_detect_tests", lambda *a: ("python -m pytest -q", {})
    )
    monkeypatch.setattr(workspace_edits, "allowed", lambda *a: True)
    monkeypatch.setattr(
        developer_tools.sandbox, "request_for_company_id", lambda *a: None
    )
    monkeypatch.setattr(stack_image, "prepare_from_thread", lambda *a: None)
    monkeypatch.setattr(settings, "RESEARCH_TOOLS_ENABLED", True)
    monkeypatch.setattr(settings, "DEVELOPER_TOOLS_ENABLED", True)
    return requester


# ── The table ────────────────────────────────────────────────────────────────


def test_each_step_offers_its_families():
    expected = {
        tool_steps.EXECUTE: {READ, WRITE, RUN, RESEARCH},
        tool_steps.ARCHITECT_BRIEF: {READ, RESEARCH},
        tool_steps.QA_REVIEW: {READ},
        tool_steps.PLAN: {READ},
        tool_steps.SPEC_AUTHOR: {READ, RESEARCH},
        tool_steps.ARCH_AUTHOR: {READ, RESEARCH},
        tool_steps.SCRUM_DECISION: {READ},
    }
    for step, families in expected.items():
        for role in roles.ROLES:
            assert tool_steps.families(step, role) == families, (step, role)
    assert tool_steps.families("an unknown step", "Developer") == frozenset()
    assert tool_steps.record(tool_steps.ARCHITECT_BRIEF, "Architect") == {
        "step": "architect_brief",
        "families": ["R", "S"],
    }


def test_a_role_deny_narrows_and_never_widens(monkeypatch):
    monkeypatch.setattr(
        tool_steps,
        "ROLE_DENY",
        {"TechWriter": frozenset({RUN}), "QA": frozenset({"W", "X", "Z"})},
    )
    assert tool_steps.families(tool_steps.EXECUTE, "TechWriter") == {
        READ,
        WRITE,
        RESEARCH,
    }
    assert tool_steps.families(tool_steps.EXECUTE, "tech-writer") == {
        READ,
        WRITE,
        RESEARCH,
    }
    assert tool_steps.families(tool_steps.QA_REVIEW, "QA") == {READ}
    for step, ceiling in tool_steps.CEILING.items():
        for role in (*roles.ROLES, "Manager", None):
            assert tool_steps.families(step, role) <= ceiling
    # The planner's fallback follows: a role that cannot run takes no runs.
    assert not roles.delivers_files("TechWriter")
    assert roles.delivers_files("Developer") and roles.delivers_files("SecOps")


# ── An execution turn of any role ────────────────────────────────────────────


@pytest.mark.parametrize("role", ["TechWriter", "SecOps", "Architect", "PO"])
def test_an_execution_turn_of_any_role_reads_writes_runs_and_researches(daemon, role):
    box = developer_tools.toolbox_for(1, role, item_id=None)
    names = _names(box)
    assert READS <= names and "workspace__edit" in names, role
    assert RUNS <= names and "research__package" in names, role
    assert "research__advisories" in names
    box.close()


def test_a_role_deny_drops_its_tools_from_the_execution_turn(daemon, monkeypatch):
    monkeypatch.setattr(tool_steps, "ROLE_DENY", {"TechWriter": frozenset({RUN})})
    names = _names(developer_tools.toolbox_for(1, "TechWriter"))
    assert READS <= names and "workspace__edit" in names
    assert not names & RUNS
    # Keyed by catalogue name; a stored alias ("Security") is its role.
    monkeypatch.setattr(tool_steps, "ROLE_DENY", {"SecOps": frozenset({WRITE})})
    names = _names(developer_tools.toolbox_for(1, "Security"))
    assert RUNS <= names and not names & WRITES


def test_the_execution_turn_records_the_step_and_its_families(monkeypatch):
    box = developer_tools.WorkspaceToolbox(lambda *a: {}, "/w", can_edit=True)
    monkeypatch.setattr(developer_tools, "toolbox_for", lambda *a, **k: box)
    monkeypatch.setattr(developer_tools, "enter_task_worktree", lambda *a, **k: None)
    model = _Model([_answer("Done.")])
    monkeypatch.setattr(worker_execute, "LLMProxyClient", lambda base_url=None: model)
    agent = {"role": "TechWriter", "model": "m"}
    worker_execute._execute_task(
        {"id": 1, "title": "T", "description": "x"}, agent, "S"
    )
    offered = agent["tools_offered"]
    assert offered["step"] == "execute"
    assert offered["families"] == ["R", "W", "X", "S"]
    assert "workspace__edit" in offered["names"]
    assert (
        workspace_edits.observe_tools({}, agent)["tools_offered"]["step"] == "execute"
    )


# ── The steps that only read ─────────────────────────────────────────────────


@pytest.mark.parametrize("role", ["QA", "Developer", "BackendDeveloper"])
def test_a_review_and_a_brief_never_write_nor_run_whatever_the_role(daemon, role):
    for step in (tool_steps.QA_REVIEW, tool_steps.ARCHITECT_BRIEF):
        names = _names(developer_tools.toolbox_for(1, role, step=step))
        assert READS <= names and not names & (WRITES | RUNS), (step, role)
        read_only = review_tools.for_step(step, role, 1, rounds=2)
        assert _names(read_only) == READS and read_only.max_rounds == 2
    # The brief may research; the review may not.
    review = developer_tools.toolbox_for(1, role, step=tool_steps.QA_REVIEW)
    brief = developer_tools.toolbox_for(1, role, step=tool_steps.ARCHITECT_BRIEF)
    assert "research__package" not in _names(review)
    assert "research__package" in _names(brief)


def test_a_step_without_reading_gets_no_read_toolbox(daemon, monkeypatch):
    assert review_tools.for_step("an unknown step", "PO", 1, rounds=3) is None
    assert review_tools.for_step(tool_steps.PLAN, "PO", 1, rounds=0) is None
    assert review_tools.for_step(tool_steps.PLAN, "PO", None, rounds=3) is None
    monkeypatch.setattr(tool_steps, "ROLE_DENY", {"PO": frozenset({READ})})
    assert review_tools.for_step(tool_steps.PLAN, "PO", 1, rounds=3) is None


# ── The PO reads before it plans ─────────────────────────────────────────────


def _plan(monkeypatch, model, agents=None):
    monkeypatch.setattr(sprint_planner, "_client", lambda po: model)
    agents = agents or [
        {"role": "PO", "name": "PO", "model": "m", "company_id": 1},
        {"role": "Developer", "name": "Dev", "model": "m", "company_id": 1},
        {"role": "QA", "name": "QA", "model": "m", "company_id": 1},
    ]
    return sprint_planner.plan_execution_sprint(
        "Acme", "Greet people", "S", agents, file_listing="src/"
    )


def test_the_po_reads_the_project_before_its_plan(daemon, monkeypatch):
    turns = []
    monkeypatch.setattr(
        planning_reads, "record_agent_turn", lambda **k: turns.append(k)
    )
    model = _Model(
        [
            _answer_with_calls(_call("workspace__read", path="src/greet.py")),
            _answer(PLAN_JSON),
        ]
    )
    plan = _plan(monkeypatch, model)
    assert plan["planner_fallback"] is None and len(plan["tasks"]) == 2
    reads = plan["planning_reads"]
    assert [(r["tool"], r["path"]) for r in reads] == [("read", "src/greet.py")]
    assert reads[0]["sha256"] and reads[0]["read_only"] is True
    offered = {t["function"]["name"] for t in model.requests[0]["tools"]}
    assert offered == READS
    assert "Reading the project" in model.requests[0]["messages"][-1]["content"]
    # Each reading round is a turn; the final one carries the observations.
    assert [t["action"] for t in turns] == ["plan_read", "plan_read"]
    assert turns[-1]["tool_calls"] == reads and turns[0]["company_id"] == 1


def test_the_po_plans_in_one_call_without_a_daemon_or_rounds(daemon, monkeypatch):
    model = _Model([_answer(PLAN_JSON)])
    monkeypatch.setattr(settings, "PLANNING_TOOL_ROUNDS", 0)
    plan = _plan(monkeypatch, model)
    assert plan["planning_reads"] == [] and model.requests[0]["tools"] is None
    monkeypatch.setattr(settings, "PLANNING_TOOL_ROUNDS", 3)
    monkeypatch.setattr(developer_tools, "_bridge", {})
    plan = _plan(monkeypatch, model)
    assert plan["planning_reads"] == [] and model.requests[-1]["tools"] is None
    assert len(plan["tasks"]) == 2


def test_on_an_event_loop_the_po_plans_without_tools(daemon):
    async def on_loop():
        return planning_reads.toolbox(1)

    assert planning_reads.toolbox(1) is not None
    assert asyncio.run(on_loop()) is None


def test_an_empty_answer_after_the_reads_falls_back_to_one_call(daemon, monkeypatch):
    monkeypatch.setattr(planning_reads, "record_agent_turn", lambda **k: None)
    model = _Model(
        [
            _answer_with_calls(_call("workspace__list")),
            _answer(""),
            _answer(PLAN_JSON),
        ]
    )
    plan = _plan(monkeypatch, model)
    assert len(plan["tasks"]) == 2 and len(plan["planning_reads"]) == 1
    assert model.requests[-1]["tools"] is None


# ── The ScrumMaster reads before it decides ──────────────────────────────────


def test_the_scrum_master_reads_the_task_before_deciding(daemon, monkeypatch):
    turns = []
    monkeypatch.setattr(triage_reads, "record_agent_turn", lambda **k: turns.append(k))
    model = _Model(
        [
            _answer_with_calls(_call("workspace__search", pattern="greet")),
            _answer(DECISION),
        ]
    )
    monkeypatch.setattr(scrum_master, "client_for", lambda **k: model)
    monkeypatch.setattr(
        scrum_master, "ask_triage", lambda *a: pytest.fail("no single call")
    )
    text = triage_reads.answer("m", "You are the SM.", "Why blocked?", 1, 7, 3)
    assert scrum_master.decide(text)["decision"] == "retry"
    offered = {t["function"]["name"] for t in model.requests[0]["tools"]}
    assert offered == READS
    assert model.requests[0]["messages"][0]["content"] == "You are the SM."
    assert [t["action"] for t in turns] == ["scrum_triage_read"] * 2
    assert turns[-1]["tool_calls"][0]["tool"] == "search"
    assert turns[-1]["backlog_item_id"] == 7 and turns[-1]["sprint_id"] == 3


def test_the_scrum_master_falls_back_to_today_s_call(daemon, monkeypatch):
    asked = []
    monkeypatch.setattr(
        scrum_master, "ask_triage", lambda *a: asked.append(a) or DECISION
    )
    # No daemon: today's call alone.
    monkeypatch.setattr(developer_tools, "_bridge", {})
    assert triage_reads.answer("m", None, "Why?", 1, 7) == DECISION
    assert asked == [("m", None, "Why?", 1)]
    # With the reads but no decision after them: today's call too.
    monkeypatch.setattr(
        developer_tools,
        "_bridge",
        {"nats": object(), "resolve_root": lambda company_id: "/nowhere"},
    )
    monkeypatch.setattr(triage_reads, "record_agent_turn", lambda **k: None)
    monkeypatch.setattr(
        scrum_master, "client_for", lambda **k: _Model([_answer("no idea")])
    )
    assert triage_reads.answer("m", None, "Why?", 1, 7) == DECISION
    assert len(asked) == 2
    monkeypatch.setattr(settings, "SCRUM_TRIAGE_TOOL_ROUNDS", 0)
    assert triage_reads.toolbox(1, 7) is None
