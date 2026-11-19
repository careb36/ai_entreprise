"""The edit format per model (issue #689; Aider's edit-format benchmark, method only).

- ``services/models/edit_format.py``: ``EDIT_FORMAT_BY_MODEL`` maps the model
  the task worker requests to ``auto``, ``whole`` or ``search_replace``; a
  value that cannot be read is dropped with a warning; unset, every model
  runs on ``auto``, the prompt as before;
- the Developer's prompt carries the format's instructions;
- the run's evidence carries ``edit_format``: the format asked for and what
  the backend read in the answer's blocks and tool calls, never the model's
  account; malformed edit blocks and blocks off the format are recorded as
  such, never as well formed.
"""

from __future__ import annotations

import asyncio
import json
import logging

import pytest
from config import settings
from models.agent_run import AgentRun, Verification
from services.contracts.manifest import parse_file_manifest
from services.loop.worker import deliver as worker_deliver
from services.loop.worker import execute as worker_execute
from services.loop.worker import prompt
from services.models import edit_format, routing_policy
from sqlmodel import select
from test_llm_routing import _board, _Nats, db  # noqa: F401

EDIT_BLOCK = (
    "```python path=app/login.py edit\n"
    "<<<<<<< SEARCH\nold\n=======\nnew\n>>>>>>> REPLACE\n```\n"
)
MALFORMED_BLOCK = "```python path=app/login.py edit\nold\n=======\nnew\n```\n"
WHOLE_BLOCK = "```python path=app/new.py\nprint('hi')\n```\n"


@pytest.fixture(autouse=True)
def _defaults(monkeypatch):
    monkeypatch.setattr(settings, "AGENT_MODEL_POLICY", "")
    monkeypatch.setattr(settings, "EDIT_FORMAT_BY_MODEL", "")
    routing_policy._warned.clear()


# ---------------------------------------------------------------------------
# The setting
# ---------------------------------------------------------------------------


def test_without_a_setting_every_model_runs_on_auto():
    assert edit_format.format_for("any-model", "") == {
        "format": "auto",
        "source": "default",
        "model": "any-model",
    }


def test_a_named_model_beats_the_star_and_others_take_the_star():
    setting = json.dumps({"model-a": "whole", "*": "search_replace"})
    assert edit_format.format_for("model-a", setting)["format"] == "whole"
    assert edit_format.format_for("model-a", setting)["source"] == "model"
    other = edit_format.format_for("model-b", setting)
    assert (other["format"], other["source"]) == ("search_replace", "any_model")


@pytest.mark.parametrize(
    "setting", ["not json", "[1, 2]", json.dumps({"model-a": "udiff"})]
)
def test_a_setting_that_cannot_be_read_is_dropped_with_a_warning(setting, caplog):
    with caplog.at_level(logging.WARNING):
        assert edit_format.format_for("model-a", setting)["format"] == "auto"
    assert "EDIT_FORMAT_BY_MODEL" in caplog.text


def test_each_format_has_its_instructions_and_auto_is_the_old_one():
    assert prompt.file_instructions("auto") == prompt.FILE_MANIFEST_INSTRUCTIONS
    assert prompt.file_instructions(None) == prompt.FILE_MANIFEST_INSTRUCTIONS
    whole = prompt.file_instructions("whole")
    assert "<<<<<<< SEARCH" not in whole and "complete content" in whole
    edits = prompt.file_instructions("search_replace")
    assert "<<<<<<< SEARCH" in edits and "new file in full" in edits


# ---------------------------------------------------------------------------
# The observation
# ---------------------------------------------------------------------------


def _observe(answer, fmt, calls=None):
    requested = {"format": fmt, "source": "any_model"}
    return edit_format.observe(parse_file_manifest(answer), calls, requested)


def test_whole_files_and_edit_blocks_are_counted_from_the_answer():
    seen = _observe(WHOLE_BLOCK + EDIT_BLOCK, "search_replace")
    assert seen["whole_files"] == 1 and seen["edit_blocks"] == 1
    assert seen["used"] == ["whole", "search_replace"]
    assert seen["requested"] == "search_replace" and seen["well_formed"] is True


def test_a_malformed_edit_block_is_recorded_never_as_well_formed():
    seen = _observe(MALFORMED_BLOCK, "search_replace")
    assert seen["malformed_edit_blocks"] == 1 and seen["edit_blocks"] == 0
    assert seen["used"] == [] and seen["well_formed"] is False


def test_an_edit_block_under_whole_is_off_the_format():
    seen = _observe(EDIT_BLOCK, "whole")
    assert seen["off_format"] == 1 and seen["well_formed"] is False


def test_the_edit_tool_calls_are_counted_apart_with_their_errors():
    calls = [
        {"server": "workspace", "tool": "edit", "is_error": False},
        {"server": "workspace", "tool": "edit", "is_error": True},
        {"server": "workspace", "tool": "read", "is_error": False},
    ]
    seen = _observe("No files.", "auto", calls)
    assert (seen["tool_edits"], seen["tool_edit_errors"]) == (1, 1)
    assert seen["used"] == ["workspace_edit"]


def test_what_the_model_says_about_its_format_is_not_read():
    claim = "I used SEARCH/REPLACE edits everywhere, all well formed.\n" + WHOLE_BLOCK
    seen = _observe(claim, "search_replace")
    assert seen["edit_blocks"] == 0 and seen["whole_files"] == 1


# ---------------------------------------------------------------------------
# Through the task worker, with the fake model
# ---------------------------------------------------------------------------


class _FakeLLM:
    def __init__(self, content):
        self.calls = []
        self.content = content


@pytest.fixture()
def fake(monkeypatch):
    state = _FakeLLM("")

    class _Client:
        def __init__(self, base_url=None):
            self.timeout = None

        def chat(self, messages, model, role, bu_id=None, **kwargs):
            state.calls.append({"model": model, "messages": messages})
            return {
                "choices": [{"message": {"content": state.content}}],
                "served_model": "what-the-proxy-served",
                "provider": "qwen",
            }

    monkeypatch.setattr(worker_execute, "LLMProxyClient", _Client)
    return state


def _run(db, fake, tmp_path, monkeypatch, content):  # noqa: F811
    monkeypatch.setenv("AUDIT_LOG_PATH", str(tmp_path / "audit"))
    monkeypatch.setattr(worker_deliver, "_resolve_workspace_root", lambda cid: None)
    fake.content = content
    company, sprint, item = _board(db, role="Developer", agent_model="model-a")
    agent = {"role": "Developer", "name": "Dev", "model": "model-a"}
    task = {"id": item.id, "title": item.title, "description": item.description}
    deliverable = worker_execute._execute_task(
        task, agent, "Formats", company_id=company.id
    )
    sprint_dict = {"id": sprint.id, "company_id": company.id, "name": "Formats"}
    asyncio.run(
        worker_deliver._deliver_artifacts(
            _Nats(), task, agent, sprint_dict, deliverable
        )
    )
    db.expire_all()
    run = db.exec(
        select(AgentRun).where(AgentRun.external_ref == f"task-{item.id}")
    ).first()
    verification = db.exec(
        select(Verification).where(Verification.agent_run_id == run.id)
    ).first()
    evidence = verification.evidence
    return run, evidence if isinstance(evidence, dict) else json.loads(evidence)


def test_the_prompt_and_the_evidence_follow_the_models_format(
    db,  # noqa: F811
    fake,
    tmp_path,
    monkeypatch,
):
    monkeypatch.setattr(
        settings, "EDIT_FORMAT_BY_MODEL", json.dumps({"model-a": "search_replace"})
    )
    run, evidence = _run(db, fake, tmp_path, monkeypatch, "Done.\n" + EDIT_BLOCK)

    sent = fake.calls[0]["messages"][-1]["content"]
    assert prompt.file_instructions("search_replace").strip() in sent
    assert prompt.FILE_MANIFEST_INSTRUCTIONS.strip() not in sent
    observed = evidence["edit_format"]
    assert observed["requested"] == "search_replace"
    assert observed["source"] == "model"
    assert observed["edit_blocks"] == 1 and observed["well_formed"] is True
    # The claim stays the claim: the declared paths, apart from the observation.
    assert run.claims["files"] == ["app/login.py"]
    assert "edit_format" not in run.claims


def test_without_a_setting_the_prompt_is_the_one_before(
    db,  # noqa: F811
    fake,
    tmp_path,
    monkeypatch,
):
    _, evidence = _run(db, fake, tmp_path, monkeypatch, "Done.\n" + WHOLE_BLOCK)

    sent = fake.calls[0]["messages"][-1]["content"]
    assert prompt.FILE_MANIFEST_INSTRUCTIONS.strip() in sent
    assert evidence["edit_format"]["requested"] == "auto"
    assert evidence["edit_format"]["source"] == "default"


def test_a_format_the_model_cannot_use_is_recorded_as_such(
    db,  # noqa: F811
    fake,
    tmp_path,
    monkeypatch,
):
    monkeypatch.setattr(
        settings, "EDIT_FORMAT_BY_MODEL", json.dumps({"*": "search_replace"})
    )
    _, evidence = _run(db, fake, tmp_path, monkeypatch, "Done.\n" + MALFORMED_BLOCK)

    observed = evidence["edit_format"]
    assert observed["requested"] == "search_replace"
    assert observed["malformed_edit_blocks"] == 1 and observed["edit_blocks"] == 0
    assert observed["well_formed"] is False and observed["used"] == []
