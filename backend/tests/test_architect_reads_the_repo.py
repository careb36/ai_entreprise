"""The Architect reads the project before its brief, with read tools only.

In sprint 7 (2026-11-24) the Architect, asked for a brief in one plain call,
wanted to look at the project's workflows and could only type a tool call as
text. The brief now runs through the Developer's tool loop with the
workspace's read tools alone (``services/loop/worker/architect_brief.py``):
list, read, search and find_file on the task's root, no edit, move, delete or
test run, each call recorded as the backend's observation. Without the daemon
it is one plain call, as before. The daemon's read handlers run for real
against ``tmp_path`` (``developer_tools_helpers``).
"""

from __future__ import annotations

import hashlib

import developer_tools_helpers
import pytest
import worker_stages
from config import settings
from developer_tools_helpers import _answer, _answer_with_calls, _call, _load_daemon
from developer_tools_helpers import _Requester as _DaemonRequester
from services.loop.worker import architect_brief
from services.loop.worker import execute as worker_execute
from services.tools import developer_tools
from test_architect import _company, _sprint

project = developer_tools_helpers.project

READ_TOOLS = [
    "workspace__list",
    "workspace__read",
    "workspace__search",
    "workspace__find_file",
]


class _Client:
    """The proxy: each call's tools kept, the answers given in order."""

    def __init__(self, answers):
        self.answers = list(answers)
        self.calls = []

    def chat(self, messages, model, role, bu_id=None, **kwargs):
        self.calls.append({"messages": list(messages), **kwargs})
        return self.answers.pop(0)


@pytest.fixture()
def consult(monkeypatch, sqlite_session):
    """``_consult_architect`` with a fake proxy; the turns it records."""
    worker_stages.use_engine(monkeypatch, sqlite_session.get_bind())
    turns = []
    monkeypatch.setattr(
        worker_execute, "record_agent_turn", lambda **kw: turns.append(kw)
    )
    company = _company(sqlite_session)
    sprint = _sprint(sqlite_session, company, "active", [])

    def run(answers):
        client = _Client(answers)
        monkeypatch.setattr(worker_execute, "LLMProxyClient", lambda url: client)
        advice = worker_execute._consult_architect(
            {"id": 1, "title": "Update the greeting"},
            {"role": "Developer"},
            {"id": sprint.id, "company_id": company.id, "name": "S"},
        )
        return advice, client

    run.turns = turns
    return run


@pytest.fixture()
def daemon(monkeypatch, project):
    """A daemon that answers for *project*, as the worker binds it."""
    requester = _DaemonRequester(_load_daemon())
    monkeypatch.setattr(developer_tools, "_request_from_thread", requester)
    monkeypatch.setattr(
        developer_tools,
        "_bridge",
        {"nats": object(), "resolve_root": lambda company_id: str(project)},
    )
    return requester


def _offered(call):
    return [s["function"]["name"] for s in call.get("tools") or []]


def test_the_architect_is_offered_the_read_tools_only(consult, daemon):
    advice, client = consult([_answer("Reuse greet() in src/greet.py.")])

    assert advice == "Reuse greet() in src/greet.py."
    assert _offered(client.calls[0]) == READ_TOOLS
    request = client.calls[0]["messages"][-1]["content"]
    assert architect_brief.READ_NOTE.strip() in request


def test_a_read_it_makes_is_answered_and_recorded(consult, daemon, project):
    advice, client = consult(
        [
            _answer_with_calls(_call("workspace__read", path="src/greet.py")),
            _answer("Keep greet() pure; add the new greeting beside it."),
        ]
    )

    assert advice == "Keep greet() pure; add the new greeting beside it."
    answered = client.calls[1]["messages"][-1]
    assert answered["role"] == "tool" and "Hello, {name}!" in answered["content"]
    # The round that called a tool, then the brief with every observation.
    assert consult.turns[0]["result_preview"].startswith("tool calls: ")
    final = consult.turns[-1]
    assert final["result_preview"] == advice
    [read] = final["tool_calls"]
    raw = (project / "src" / "greet.py").read_bytes()
    assert read["tool"] == "read" and read["path"] == "src/greet.py"
    assert read["sha256"] == hashlib.sha256(raw).hexdigest()
    assert read["read_only"] is True and read["is_error"] is False


def test_an_edit_it_asks_for_is_not_a_tool_it_has(consult, daemon, project):
    before = (project / "src" / "greet.py").read_text()

    advice, client = consult(
        [
            _answer_with_calls(
                _call(
                    "workspace__edit",
                    path="src/greet.py",
                    search="Hello",
                    replace="Bye",
                )
            ),
            _answer("Change the greeting in src/greet.py."),
        ]
    )

    assert advice == "Change the greeting in src/greet.py."
    [refused] = consult.turns[-1]["tool_calls"]
    assert refused["is_error"] is True and refused["reason"] == "unknown tool"
    assert (project / "src" / "greet.py").read_text() == before
    assert not {"workspace__edit", "workspace__move", "workspace__delete"} & set(
        _offered(client.calls[0])
    )


def test_its_reads_stop_at_the_round_limit(consult, daemon, monkeypatch):
    monkeypatch.setattr(settings, "ARCHITECT_TOOL_ROUNDS", 2)
    list_call = _answer_with_calls(_call("workspace__list"))

    advice, client = consult([list_call, list_call, _answer("Brief.")])

    assert advice == "Brief."
    assert [bool(_offered(c)) for c in client.calls] == [True, True, False]


def test_without_the_daemon_it_is_one_plain_call(consult, monkeypatch):
    monkeypatch.setattr(developer_tools, "_bridge", {})

    advice, client = consult([_answer("Use a service layer.")])

    assert advice == "Use a service layer."
    assert len(client.calls) == 1 and "tools" not in client.calls[0]
    request = client.calls[0]["messages"][-1]["content"]
    assert architect_brief.READ_NOTE.strip() not in request
    assert "tool_calls" not in consult.turns[-1] or not consult.turns[-1]["tool_calls"]


def test_zero_rounds_asks_in_one_call(consult, daemon, monkeypatch):
    monkeypatch.setattr(settings, "ARCHITECT_TOOL_ROUNDS", 0)

    advice, client = consult([_answer("Use a service layer.")])

    assert advice == "Use a service layer."
    assert len(client.calls) == 1 and "tools" not in client.calls[0]
