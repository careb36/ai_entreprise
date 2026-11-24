"""The PO and the Architect read the project while they write (issue #766).

The step table gives the PO writing a specification and the Architect
writing or revising an architecture families R and S
(``tool_steps.SPEC_AUTHOR``, ``ARCH_AUTHOR``); their turns had the research
tools only. They now get the read-only workspace tools beside them
(``planning_reads.author_turn``), never an edit or a run, and each round is
an audit turn with the calls' observations. The plan from an approved
specification reads the project and checks what is already done like the
plan from a prompt (``planning_reads.spec_answer``, ``already_done``): the
PO's claim apart from its observed reads, and one QA verification task when
every part is claimed done. Without a daemon, every turn is as before.
"""

from __future__ import annotations

import json

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
from services.contracts import tool_steps
from services.planning import (
    arch_author,
    planning_reads,
    spec_author,
    spec_check,
    spec_planning,
)
from services.tools import developer_tools, mcp_tools, research_tools
from specification_helpers import GOOD

project = developer_tools_helpers.project

NEVER = {"workspace__edit", "workspace__move", "workspace__delete"}
NEVER |= {"workspace__run_tests", "workspace__lint"}
RESEARCH = {"research__package", "research__advisories", "research__fetch"}
ARCH = {
    "title": "Board architecture",
    "modules": [
        {"name": "Board", "responsibility": "lists tasks", "path": "src/"},
    ],
}
CLAIM = [
    {"part": "the task list", "evidence": "src/greet.py", "paths": ["src/greet.py"]}
]


def _names(box):
    return {s["function"]["name"] for s in (box.schemas() if box else [])}


@pytest.fixture()
def daemon(monkeypatch, project):
    """The daemon bound to *project*, no MCP servers; the audit turns kept."""
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
    monkeypatch.setattr(settings, "RESEARCH_TOOLS_ENABLED", True)
    monkeypatch.setattr(settings, "BOARD_TOOLS_ENABLED", False)
    turns: list[dict] = []
    monkeypatch.setattr(
        planning_reads, "record_agent_turn", lambda **k: turns.append(k)
    )
    return turns


def _model(monkeypatch, *answers):
    model = _Model(list(answers))
    monkeypatch.setattr(spec_author, "_client", lambda *a, **k: model)
    return model


def _reads_then(text):
    return (
        _answer_with_calls(_call("workspace__read", path="src/greet.py")),
        _answer(text),
    )


def _tool_names(request):
    return {t["function"]["name"] for t in request["tools"] or []}


def _tool_reply(model):
    return next(m for m in model.requests[1]["messages"] if m["role"] == "tool")


@pytest.mark.parametrize(
    ("step", "role"),
    [(tool_steps.SPEC_AUTHOR, "PO"), (tool_steps.ARCH_AUTHOR, "Architect")],
)
def test_each_author_turn_reads_and_researches_never_writes_nor_runs(
    daemon, step, role
):
    assert tool_steps.families(step, role) == {tool_steps.READ, tool_steps.RESEARCH}
    research = research_tools.ResearchToolbox(hosts=[])
    box, note, record = planning_reads.author_turn(step, role, 1, "m", research)
    names = _names(box)
    assert {"workspace__read", "workspace__search", "workspace__list"} <= names
    assert RESEARCH <= names and not names & NEVER
    # The research tools keep their own rounds beside the reads.
    rounds = max(settings.RESEARCH_TOOL_ROUNDS, settings.PLANNING_TOOL_ROUNDS)
    assert step != tool_steps.SPEC_AUTHOR or box.max_rounds == rounds
    assert "read-only workspace tools" in note and record is not None
    box.close()


def test_the_po_reads_the_project_while_it_writes_the_specification(
    daemon, monkeypatch
):
    model = _model(monkeypatch, *_reads_then(json.dumps(GOOD)))
    spec = spec_author.draft_from(
        {
            "company_id": 1,
            "model": "m",
            "system_prompt": None,
            "prompt": "Write it.",
            "bu_id": None,
            "questions": [],
            "research_hosts": [],
        }
    )
    assert spec["title"] == "Task board"
    offered = _tool_names(model.requests[0])
    assert "workspace__read" in offered and RESEARCH <= offered
    assert not offered & NEVER
    assert "## Reading the project" in model.requests[0]["messages"][-1]["content"]
    assert "Hello" in _tool_reply(model)["content"]  # The read was answered.
    # Each round an audit turn; the last carries the read's observation.
    assert [t["action"] for t in daemon] == ["spec_author_read"] * 2
    [call] = daemon[-1]["tool_calls"]
    assert call["tool"] == "read" and call["path"] == "src/greet.py"
    assert call["sha256"]
    assert spec["sources"] == []  # A read is no research source.


def test_the_architect_reads_the_project_while_it_writes_the_architecture(
    daemon, monkeypatch
):
    model = _model(monkeypatch, *_reads_then(json.dumps(ARCH)))
    arch = arch_author.draft_from(
        {
            "specification_id": 7,
            "company_id": 1,
            "model": "m",
            "system_prompt": None,
            "prompt": "Design it.",
            "bu_id": None,
            "questions": [],
            "research_hosts": [],
        }
    )
    assert arch["title"] == "Board architecture"
    offered = _tool_names(model.requests[0])
    assert "workspace__read" in offered and RESEARCH <= offered
    assert not offered & NEVER
    assert "Hello" in _tool_reply(model)["content"]
    assert [t["action"] for t in daemon] == ["arch_author_read"] * 2
    assert daemon[-1]["agent"]["role"] == "Architect"
    assert daemon[-1]["tool_calls"][0]["sha256"]


def test_without_a_daemon_the_authors_write_as_before(daemon, monkeypatch):
    monkeypatch.setattr(developer_tools, "_bridge", {})
    seen = []
    box, note, record = planning_reads.author_turn(
        tool_steps.SPEC_AUTHOR, "PO", 1, "m", "research box"
    )
    assert (box, note, record) == ("research box", "", None)

    def ask(model, system_prompt, prompt, **kwargs):
        seen.append(prompt)
        return json.dumps(GOOD)

    monkeypatch.setattr(spec_author, "ask", ask)
    monkeypatch.setattr(settings, "RESEARCH_TOOLS_ENABLED", False)  # A plain call.
    spec_author.draft_from(
        {
            "company_id": 1,
            "model": "m",
            "system_prompt": None,
            "prompt": "Write it.",
            "bu_id": None,
            "questions": [],
            "research_hosts": [],
        }
    )
    assert seen == ["Write it."] and daemon == []


# ── The plan from an approved specification ─────────────────────────────────


def _plan_request(prompt="Plan it."):
    content = spec_check.normalize(GOOD)
    return {
        "specification_id": 3,
        "company_id": 1,
        "title": content["title"],
        "summary": content["summary"],
        "content": content,
        "roles": ["Developer", "QA"],
        "hired": ["Developer", "QA"],
        "team": None,
        "item_limit": 4,
        "model": "m",
        "system_prompt": "You are the PO.",
        "prompt": prompt,
        "bu_id": None,
    }


def _plan_json(tasks, claim=None):
    body = {"sprint_name": "S", "goal": "G", "tasks": tasks}
    return json.dumps(body | ({"already_done": claim} if claim is not None else {}))


TASK = {"title": "Board page", "assignee": "Developer", "covers": ["REQ-1.1"]}


def test_the_spec_plan_reads_and_checks_what_is_already_done(daemon, monkeypatch):
    model = _model(monkeypatch, *_reads_then(_plan_json([TASK], CLAIM)))
    plan = spec_planning.plan_from(_plan_request())
    asked = model.requests[0]["messages"]
    assert asked[0] == {"role": "system", "content": "You are the PO."}
    assert "already exists in the code" in asked[-1]["content"]
    assert "workspace__read" in _tool_names(model.requests[0])
    # The claim (the PO's words) apart from the observed reads (the daemon's).
    claim, observed = plan["already_done_claim"], plan["already_done_observed"]
    assert claim[0]["part"] == "the task list" and "sha256" not in json.dumps(claim)
    assert observed["files_read"][0]["path"] == "src/greet.py"
    assert observed["files_read"][0]["sha256"] == plan["planning_reads"][0]["sha256"]
    assert observed["claimed_paths"][0]["read"] is True
    assert [t["action"] for t in daemon] == ["plan_read"] * 2
    assert plan["planner_fallback"] is None
    assert "Board page" in [t["title"] for t in plan["tasks"]]


def test_an_all_done_spec_plan_is_one_qa_verification_task(daemon, monkeypatch):
    _model(monkeypatch, *_reads_then(_plan_json([], CLAIM)))
    plan = spec_planning.plan_from(_plan_request())
    [task] = plan["tasks"]  # No test-writing tasks beside it.
    assert task["assignee"] == "QA" and task["depends_on"] == []
    assert task["description"].startswith(
        'Verify that the specification "Task board" is already met'
    )
    assert task["covers"] == spec_check.criterion_ids(spec_check.normalize(GOOD))
    assert plan["uncovered"] == [] and plan["planner_fallback"] is None
    assert plan["already_done_claim"][0]["paths"] == ["src/greet.py"]


def test_without_a_daemon_the_spec_plan_is_todays(daemon, monkeypatch):
    monkeypatch.setattr(developer_tools, "_bridge", {})
    seen = []

    def ask(model, system_prompt, prompt, **kwargs):
        seen.append(prompt)
        return _plan_json([], CLAIM)

    monkeypatch.setattr(spec_author, "ask", ask)
    plan = spec_planning.plan_from(_plan_request())
    assert seen == ["Plan it."] and daemon == []
    # A claim not asked for is ignored: no tasks is today's fallback.
    assert "already_done_claim" not in plan
    assert plan["planner_fallback"]["reason"] == "too_few_tasks"
    assert plan["planning_reads"] == []
