"""The record tools' toolbox (``services/tools/board_tools.py``, issue #766).

``board__task`` and ``board__evidence`` read records the caller's readers
return; the scope comes from the backend, a task outside it is refused, the
reply keeps observations and claims under their own headings, it is redacted
like a read and cut, and each call is a read-only observation.
"""

from __future__ import annotations

import json

from services.tools import board_tools, developer_tools, mcp_tools

SECRET = 'API_KEY = "sk-live-0123456789abcdef0123456789"'


def _call(name, **arguments):
    return {"id": "c1", "function": {"name": name, "arguments": json.dumps(arguments)}}


def _record(company_id, task_id, arguments):
    return {
        "observations": [{"task": task_id, "status": "in_review"}],
        "claims": [{"author": "Developer", "text": "All tests pass."}],
    }


def _box(task_ids=(7, 3), read=_record, rounds=3):
    scope = board_tools.Scope(1, frozenset(task_ids))
    return board_tools.BoardToolbox(scope, read, read, rounds=rounds)


def test_the_tools_are_read_only_and_named_on_the_board_server():
    box = _box()
    assert {s["function"]["name"] for s in box.schemas()} == {
        "board__task",
        "board__evidence",
    }
    observation, _text = box.call(_call("board__task", task_id=7), 2)
    assert observation["server"] == "board" and observation["tool"] == "task"
    assert observation["read_only"] is True and not observation["is_error"]
    assert observation["round"] == 2 and observation["task_id"] == 7
    assert len(observation["result_sha256"]) == 64


def test_a_task_outside_the_scope_is_refused_and_the_reader_is_not_asked():
    asked = []

    def read(company_id, task_id, arguments):
        asked.append(task_id)
        return _record(company_id, task_id, arguments)

    observation, text = _box(read=read).call(_call("board__evidence", task_id=99), 1)
    assert asked == []
    assert (
        observation["is_error"] and observation["refused"] == board_tools.OUTSIDE_SCOPE
    )
    assert text.startswith("Refused: task 99 is outside the turn's scope")
    assert "task(s) 3, 7" in text


def test_a_task_the_reader_does_not_find_in_the_company_is_refused_alike():
    observation, text = _box(read=lambda c, t, a: None).call(
        _call("board__task", task_id=7), 1
    )
    assert observation["refused"] == board_tools.OUTSIDE_SCOPE
    assert "outside the turn's scope" in text


def test_the_scope_never_comes_from_the_arguments():
    box = _box()
    observation, _text = box.call(
        _call("board__task", task_id=99, company_id=1, scope="company"), 1
    )
    assert observation["refused"] == board_tools.OUTSIDE_SCOPE


def test_observations_and_claims_are_printed_under_their_own_headings():
    _observation, text = _box().call(_call("board__task", task_id=7), 1)
    observed, claimed = text.split(board_tools.CLAIMS_HEADING)
    assert board_tools.OBSERVATIONS_HEADING in observed
    assert '"status": "in_review"' in observed and "All tests pass" not in observed
    assert "All tests pass." in claimed and "in_review" not in claimed


def test_an_empty_section_says_none_recorded():
    text = board_tools.render("task", 7, {"observations": [{"a": 1}]})
    assert text.endswith(board_tools.CLAIMS_HEADING + "\n- none recorded")


def test_a_secret_in_a_record_is_redacted_like_a_read():
    def read(company_id, task_id, arguments):
        return {"claims": [{"text": SECRET}]}

    _observation, text = _box(read=read).call(_call("board__task", task_id=7), 1)
    assert "sk-live" not in text
    assert "[line redacted: it looks like a secret]" in text


def test_a_long_reply_is_cut():
    def read(company_id, task_id, arguments):
        return {"observations": [{"n": i, "pad": "x" * 50} for i in range(200)]}

    box = _box(read=read)
    box.result_max_chars = 500
    observation, text = box.call(_call("board__task", task_id=7), 1)
    assert observation["truncated"] and observation["result_chars"] > 500
    assert text.endswith("[result cut here]") and len(text) < 600


def test_an_unknown_name_and_a_missing_id_are_errors_the_model_can_fix():
    box = _box()
    observation, text = box.call(_call("board__shell", task_id=7), 1)
    assert observation["reason"] == "unknown tool"
    assert observation["requested"] == "board__shell"
    assert "board__task" in text
    observation, text = box.call(_call("board__task"), 1)
    assert observation["is_error"] and "task_id" in text


def test_attach_combines_with_a_workspace_box_without_raising_its_rounds():
    workspace = developer_tools.WorkspaceToolbox(lambda *a: {}, "/p")
    workspace.max_rounds = 2
    board = _box(rounds=6)
    combined = board_tools.attach(workspace, board)
    assert isinstance(combined, developer_tools.CombinedToolbox)
    assert combined.max_rounds == 2 and board_tools.has_board(combined)
    names = {s["function"]["name"] for s in combined.schemas()}
    assert {"board__task", "board__evidence", "workspace__read"} <= names
    observation, _ = combined.call(_call("board__task", task_id=7), 1)
    assert observation["server"] == "board"


def test_attach_keeps_either_alone_and_adds_to_a_combined_box():
    board = _box()
    assert board_tools.attach(None, board) is board
    assert board_tools.attach("toolbox", None) == "toolbox"
    workspace = developer_tools.WorkspaceToolbox(lambda *a: {}, "/p")
    combined = developer_tools.CombinedToolbox(workspace, None)
    assert board_tools.attach(combined, board) is combined
    assert board in combined.builtin


def test_board_is_a_reserved_server_name():
    from services.tools import mcp_config

    assert "board" in mcp_config.RESERVED_SERVER_NAMES
    assert board_tools.SERVER + mcp_tools.TOOL_NAME_SEP + "task" in board_tools.NAMES
