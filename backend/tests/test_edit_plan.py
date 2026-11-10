"""Architect and editor mode (issue #319, A.10, from Aider;
``services/loop/edit_plan.py``): off, nothing changes; on, a Developer task at or
above the difficulty band asks the Architect for a numbered edit plan in a
fixed format, the plan is read back only from that format (relative paths,
every line a step), the Developer's prompt carries it as the plan to apply and
the task runs on the policy's cheapest band model, unless a person chose its
model. An answer without a plan stays guidance on the routed model."""

from __future__ import annotations

import asyncio
import json

import pytest
from config import settings
from models.company import Agent
from services import edit_plan, model_routing
from services.loop.worker import execute as worker_execute
from test_llm_routing import _board, _task_assign_handler, db  # noqa: F401

PLAN = """Here is the plan.

EDIT PLAN
1. app/auth.py — add a login() that checks the hash
2. `tests/test_auth.py` - cover a wrong password
END
"""
HARD = {"covers": ["REQ-1.1", "REQ-1.2", "REQ-2.1"], "comments": []}
EASY = {"covers": ["REQ-1.1"], "comments": []}
POLICY = json.dumps({"bands": {"1-2": "cheap-model", "3-5": "strong-model"}})


@pytest.fixture
def mode(monkeypatch):
    monkeypatch.setattr(settings, "ARCHITECT_EDITOR_MODE", True)
    monkeypatch.setattr(settings, "ARCHITECT_EDITOR_MIN_DIFFICULTY", 4)
    monkeypatch.setattr(settings, "AGENT_MODEL_POLICY", POLICY)


def test_the_plan_is_read_from_the_fixed_format():
    assert edit_plan.parse(PLAN) == [
        {"path": "app/auth.py", "change": "add a login() that checks the hash"},
        {"path": "tests/test_auth.py", "change": "cover a wrong password"},
    ]
    assert edit_plan.parse("**EDIT PLAN**\n1) a.py: x\n**END**") == [
        {"path": "a.py", "change": "x"}
    ]


@pytest.mark.parametrize(
    "text",
    [
        "Use a service layer.",
        "EDIT PLAN\n1. a.py — x\n",  # no END
        "EDIT PLAN\nEND",  # no step
        "EDIT PLAN\n1. a.py — x\nthen refactor everything\nEND",  # a stray line
        "EDIT PLAN\n1. /etc/passwd — x\nEND",
        "EDIT PLAN\n1. ../outside.py — x\nEND",
    ],
    ids=["prose", "no-end", "empty", "stray-line", "absolute", "parent"],
)
def test_anything_else_is_no_plan(text):
    assert edit_plan.parse(text) is None


def test_off_nothing_changes(monkeypatch):
    monkeypatch.setattr(settings, "ARCHITECT_EDITOR_MODE", False)
    assert not edit_plan.wanted(HARD, {"role": "Developer"})
    assert edit_plan.request_section(HARD) == ""
    assert edit_plan.plan_in(PLAN) is None
    assert "## Architect Guidance" in edit_plan.prompt_section(PLAN)
    assert edit_plan.prompt_section("") == ""


@pytest.mark.usefixtures("mode")
def test_on_only_for_hard_developer_tasks_once_per_attempt():
    assert edit_plan.wanted(HARD, {"role": "Developer"})
    assert not edit_plan.wanted(EASY, {"role": "Developer"})
    assert not edit_plan.wanted(HARD, {"role": "QA"})
    assert "EDIT PLAN" in edit_plan.request_section(HARD)
    assert edit_plan.request_section(EASY) == ""
    consulted = {"author": "Architect", "text": "ARCHITECT_CONSULTED: plan"}
    assert not edit_plan.wanted({**HARD, "comments": [consulted]}, {"role": "Dev"})
    rework = {"author": "QA", "text": "NEEDS WORK: wrong hash"}
    assert edit_plan.wanted({**HARD, "comments": [consulted, rework]}, {"role": "Dev"})


@pytest.mark.usefixtures("mode")
def test_the_developer_gets_the_plan_on_the_cheapest_model():
    section = edit_plan.prompt_section(PLAN)
    assert section.startswith("\n\n## Edit plan to apply")
    assert "1. `app/auth.py` — add a login()" in section
    assert "2. `tests/test_auth.py`" in section
    assert model_routing.cheapest_model() == "cheap-model"
    agent = {"role": "Developer", "model": ""}
    assert model_routing.resolve_agent_model(agent, item=HARD) == "strong-model"
    assert (
        model_routing.resolve_agent_model(agent, item=HARD, editor=True)
        == "cheap-model"
    )
    # A model a person chose is never re-routed.
    chosen = {"role": "Developer", "model": "chosen"}
    assert model_routing.resolve_agent_model(chosen, item=HARD, editor=True) == "chosen"


def test_a_policy_of_tiers_names_no_editor_model(monkeypatch):
    monkeypatch.setattr(
        settings, "AGENT_MODEL_POLICY", json.dumps({"bands": {"1-5": "fast"}})
    )
    assert model_routing.cheapest_model() is None
    assert model_routing.band_of(3) == "1-5"


@pytest.mark.usefixtures("mode")
@pytest.mark.parametrize(("answer", "plan"), [(PLAN, True), ("Use a service.", False)])
def test_the_worker_asks_for_a_plan_and_applies_it(db, monkeypatch, answer, plan):  # noqa: F811
    calls = []

    class _Client:
        def __init__(self, base_url=None):
            self.timeout = None

        def chat(self, messages, model, role, bu_id=None):
            calls.append({"role": role, "model": model, "prompt": messages[-1]})
            text = answer if role == "Architect" else "A written analysis. " * 40
            return {"choices": [{"message": {"content": text}}]}

    monkeypatch.setattr(worker_execute, "LLMProxyClient", _Client)
    company, sprint, item = _board(db, role="Developer", agent_model="")
    item.covers = HARD["covers"]
    db.add(Agent(company_id=company.id, name="Ada", role="Architect", model="arch"))
    db.commit()
    nats = _task_assign_handler(monkeypatch)
    monkeypatch.setattr(worker_execute, "ARCHITECT_CONSULT_ENABLED", True)

    asyncio.run(
        nats.handler(None, {"backlog_item_id": item.id, "sprint_id": sprint.id})
    )

    architect = next(c for c in calls if c["role"] == "Architect")
    assert "## Answer as an edit plan" in architect["prompt"]["content"]
    developer = next(c for c in calls if c["role"] == "Developer")
    content = developer["prompt"]["content"]
    if plan:
        assert "## Edit plan to apply" in content
        assert developer["model"] == "cheap-model"
    else:
        assert "## Architect Guidance" in content
        assert developer["model"] == "strong-model"
