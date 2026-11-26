"""The Developer pointed to the move tool, and what the run was offered.

Sprint 6, task 27 split a 642-line test file by rewriting both files from
memory: the evidence did not say whether ``workspace__move`` had been offered,
and one call with a bare tool name was recorded with an empty name and
answered with a generic note. Here: the tools offered and withheld (with why)
go on the run's evidence; a split is pointed to the move tool in the prompt
when it is offered; an unknown tool's reply names what was asked and what
exists, and the record keeps the name asked.
"""

from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "app"))

import developer_tools_helpers
from developer_tools_helpers import _answer, _call, _load_daemon, _Model, _Requester
from services.loop.worker import execute as worker_execute
from services.tools import (
    developer_tool_schemas,
    developer_tools,
    mcp_tools,
    workspace_edits,
)
from services.workspace import daemon_liveness

project = developer_tools_helpers.project

ABILITIES = {
    "abilities_version": 1,
    "subjects": ["workspace.edit", "workspace.generate"],
    "features": {
        "workspace.edit": ["delete", "move"],
        "workspace.generate": ["delete"],
    },
}


def _box(project, can_edit=True):
    return developer_tools.WorkspaceToolbox(
        _Requester(_load_daemon()), str(project), can_edit=can_edit
    )


def test_move_is_withheld_until_a_heartbeat_announces_it_and_says_why():
    daemon_liveness.reset()
    try:
        assert workspace_edits.move_offer(False) == (False, workspace_edits.NOT_HERE)
        # Right after a backend restart nothing has been heard yet.
        assert workspace_edits.move_offer(True) == (False, workspace_edits.NOT_HEARD)
        daemon_liveness.observe_heartbeat({"daemon_id": "d1", "abilities": ABILITIES})
        assert workspace_edits.move_offer(True) == (True, None)
        daemon_liveness.reset()
        older = {**ABILITIES, "features": {"workspace.edit": ["delete"]}}
        daemon_liveness.observe_heartbeat({"daemon_id": "d1", "abilities": older})
        assert workspace_edits.move_offer(True) == (
            False,
            workspace_edits.NOT_ANNOUNCED,
        )
    finally:
        daemon_liveness.reset()


def test_the_tools_offered_and_withheld_are_recorded_with_why(project):
    daemon_liveness.reset()
    box = _box(project)
    workspace_edits.offer_move(box)
    offer = workspace_edits.offered(box)
    assert "workspace__edit" in offer["names"]
    assert "workspace__move" not in offer["names"]
    assert offer["withheld"] == {
        "workspace__move": workspace_edits.NOT_HEARD,
        "workspace__delete": workspace_edits.NOT_HEARD,
    }
    plain = _box(project, can_edit=False)
    workspace_edits.offer_move(plain)
    assert set(workspace_edits.offered(plain)["withheld"].values()) == {
        workspace_edits.NOT_HERE
    }
    assert len(workspace_edits.offered(plain)["withheld"]) == 3
    assert workspace_edits.offered(None) == {}
    evidence = workspace_edits.observe_tools({}, {"tools_offered": offer})
    assert evidence["tools_offered"] == offer


def test_a_split_is_pointed_to_the_move_tool_only_when_it_is_offered(
    project, monkeypatch
):
    box = _box(project)
    box.can_move = True
    monkeypatch.setattr(developer_tools, "toolbox_for", lambda *a, **k: box)
    model = _Model([_answer("Done.")])
    monkeypatch.setattr(worker_execute, "LLMProxyClient", lambda base_url=None: model)
    agent = {"role": "Developer", "model": "m"}
    worker_execute._execute_task(
        {"id": 7, "title": "Split a file", "description": "x"}, agent, "S1"
    )
    prompt = model.requests[0]["messages"][1]["content"]
    assert developer_tool_schemas.MOVE_NOTE in prompt
    assert "workspace__move" in agent["tools_offered"]["names"]
    assert agent["tools_offered"]["withheld"] == {}

    box.can_move = False
    model = _Model([_answer("Done.")])
    monkeypatch.setattr(worker_execute, "LLMProxyClient", lambda base_url=None: model)
    worker_execute._execute_task(
        {"id": 7, "title": "Split a file", "description": "x"}, agent, "S1"
    )
    assert "## Moving code" not in model.requests[0]["messages"][1]["content"]


def test_an_unknown_tool_reply_names_what_was_asked_and_what_exists(project):
    box = _box(project, can_edit=False)
    bare, text = box.call(
        _call("workspace__reed", path="backend/tests/contexts.json"), 5
    )
    assert bare["tool"] == "reed" and bare["requested"] == "workspace__reed"
    assert text.startswith(
        "Unknown tool 'workspace__reed'. Did you mean workspace__read?"
    )
    assert "workspace__find_file" in text
    nameless, text = box.call(_call("", path="x"), 5)
    assert nameless["requested"] == "" and text.startswith("The call named no tool.")
    assert mcp_tools.unknown_tool_note("workspace.read", box.tools).startswith(
        "Unknown tool 'workspace.read'. Did you mean workspace__read?"
    )
