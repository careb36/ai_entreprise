"""The record tools' scope, readers and offering (``loop/worker/board_record.py``).

The scope is the turn's company and its task plus the tasks it depends on,
read from the database; another company's task or an unrelated task is
refused. The readers keep the platform's observations apart from what agents
and people wrote, and the tools are offered to the QA review, the Architect's
brief and an execution turn on a rework.
"""

from __future__ import annotations

import json

from developer_tools_helpers import _answer, _Model
from models import AgentRun, Verification
from models.agent_run import TASK_WORKER_PRODUCER
from services.loop.worker import architect_brief, board_record, qa_review_reads
from services.loop.worker import execute as worker_execute
from services.tasks import task_events
from services.tools import board_tools, developer_tools, review_tools
from sprint_board_helpers import _setup

SHA = "ab" * 32


def _call(name, **arguments):
    return {"id": "c1", "function": {"name": name, "arguments": json.dumps(arguments)}}


def _history(session, item):
    task_events.record(
        session, item, "claimed", task_events.SYSTEM, status="in_progress"
    )
    task_events.record(
        session,
        item,
        "delivered",
        "Developer",
        text="Done: src/store.py written, all tests pass.",
        author="Developer",
        status="in_review",
    )
    task_events.record(
        session,
        item,
        "note",
        task_events.SYSTEM,
        text="Verifier: 1 file written as sent.",
    )
    task_events.record(
        session,
        item,
        "reviewed",
        "QA",
        text="NEEDS WORK: the empty store raises KeyError.",
        author="QA",
        status="todo",
    )
    session.commit()


def _run(session, company, item):
    run = AgentRun(
        company_id=company.id,
        external_ref=f"task-{item.id}",
        version=1,
        producer=TASK_WORKER_PRODUCER,
        model="deepseek-v4-pro",
        agent_role="Developer",
        claims={"files": ["src/store.py"], "tests": ["pytest: 3 passed"]},
        artifact_text="# Artifact: src/store.py\nstore = {}\n",
        artifact_digest="cd" * 32,
        artifact_size=38,
    )
    session.add(run)
    session.commit()
    session.add(
        Verification(
            company_id=company.id,
            agent_run_id=run.id,
            verdict="unverifiable",
            checks=[
                {
                    "name": "files_written",
                    "outcome": "pass",
                    "observation": "1 file(s) written: src/store.py",
                },
                {
                    "name": "tests_evidenced",
                    "outcome": "unverifiable",
                    "observation": "agent claims 'pytest: 3 passed'; no run observed",
                },
            ],
            evidence={
                "workspace_writes": [
                    {"path": "src/store.py", "status": "written", "sha256": SHA}
                ],
                "expected": {"src/store.py": SHA},
                "tools_offered": {"names": ["workspace__read"], "withheld": {}},
                "rounds_used": 2,
                "rounds_max": 5,
                "rounds_exhausted": False,
            },
        )
    )
    session.commit()


def _world(session):
    company, _sprint, items = _setup(session, tasks=3)
    own, dep, unrelated = items
    own.depends_on = [dep.id]
    session.add(own)
    session.commit()
    _other_company, _sprint, (foreign,) = _setup(session, tasks=1)
    return company, own, dep, unrelated, foreign


def _board(session, company, own):
    box = board_record.toolbox_for_task(
        company.id, own.id, rounds=3, engine=session.get_bind()
    )
    assert box is not None
    return box


def test_the_scope_is_the_task_and_its_dependencies_in_its_company(sqlite_session):
    company, own, dep, _unrelated, _foreign = _world(sqlite_session)
    box = _board(sqlite_session, company, own)
    assert box.scope.company_id == company.id
    assert box.scope.task_ids == frozenset({own.id, dep.id})
    for task_id in (own.id, dep.id):
        observation, _ = box.call(_call("board__task", task_id=task_id), 1)
        assert not observation["is_error"], observation


def test_an_unrelated_task_and_another_companys_task_are_refused(sqlite_session):
    company, own, _dep, unrelated, foreign = _world(sqlite_session)
    box = _board(sqlite_session, company, own)
    for task_id in (unrelated.id, foreign.id):
        for tool in ("board__task", "board__evidence"):
            observation, text = box.call(_call(tool, task_id=task_id), 1)
            assert observation["refused"] == board_tools.OUTSIDE_SCOPE
            assert "outside the turn's scope" in text
            assert "Task number" not in text


def test_a_reader_refuses_a_task_of_another_company_even_in_scope(sqlite_session):
    company, own, _dep, _unrelated, foreign = _world(sqlite_session)
    engine = sqlite_session.get_bind()
    assert board_record.read_task(engine, company.id, foreign.id, {}) is None
    assert board_record.read_evidence(engine, company.id, foreign.id, {}) is None
    # Another company's turn gets no scope on this company's task.
    assert board_record.task_scope(engine, company.id + 10_000, own.id) is None


def test_the_task_history_keeps_agents_words_as_claims(sqlite_session):
    company, own, _dep, _unrelated, _foreign = _world(sqlite_session)
    _history(sqlite_session, own)
    _obs, text = _board(sqlite_session, company, own).call(
        _call("board__task", task_id=own.id), 1
    )
    observed, claimed = text.split(board_tools.CLAIMS_HEADING)
    assert '"attempts": 1' in observed and '"rework_requests": 1' in observed
    assert (
        '"kind": "delivered"' in observed and '"status_after": "in_review"' in observed
    )
    assert "Verifier: 1 file written as sent." in observed  # The system's own text.
    assert "all tests pass" not in observed and "KeyError" not in observed
    assert "all tests pass" in claimed
    assert "KeyError" in claimed and '"rework_request": true' in claimed
    assert '"by": "an agent"' in claimed
    assert "Verifier: 1 file written" not in claimed


def test_the_evidence_shows_checks_files_and_tools_apart_from_claims(sqlite_session):
    company, own, _dep, _unrelated, _foreign = _world(sqlite_session)
    _run(sqlite_session, company, own)
    observation, text = _board(sqlite_session, company, own).call(
        _call("board__evidence", task_id=own.id), 1
    )
    assert not observation["is_error"] and observation["read_only"]
    observed, claimed = text.split(board_tools.CLAIMS_HEADING)
    assert '"check": "tests_evidenced"' in observed
    assert f'"sha256_on_disk": "{SHA}"' in observed
    assert '"tools_offered"' in observed and '"rounds_used": 2' in observed
    assert "deepseek-v4-pro" not in observed  # The producer's report is a claim.
    assert "deepseek-v4-pro" in claimed and "pytest: 3 passed" in claimed
    assert '"check"' not in claimed


def test_an_attempt_that_was_never_made_says_so(sqlite_session):
    company, own, _dep, _unrelated, _foreign = _world(sqlite_session)
    _run(sqlite_session, company, own)
    _obs, text = _board(sqlite_session, company, own).call(
        _call("board__evidence", task_id=own.id, attempt=4), 1
    )
    assert "no attempt 4" in text and '"attempts_recorded": [1]' in text


def test_the_qa_review_is_offered_the_record_tools(sqlite_session, monkeypatch):
    company, own, _dep, _unrelated, _foreign = _world(sqlite_session)
    monkeypatch.setattr(qa_review_reads, "engine", sqlite_session.get_bind())
    workspace = developer_tools.WorkspaceToolbox(lambda *a: {}, "/w")
    monkeypatch.setattr(review_tools, "read_only_toolbox", lambda *a, **k: workspace)
    box = qa_review_reads._toolbox(company.id, own.id)
    names = {s["function"]["name"] for s in box.schemas()}
    assert {"board__task", "board__evidence", "workspace__read"} <= names
    assert "workspace__edit" not in names
    # Without a recorded delivery, today's single call: no tool loop at all.
    monkeypatch.setattr(review_tools, "read_only_toolbox", lambda *a, **k: None)
    assert qa_review_reads._toolbox(company.id, own.id) is None


def test_the_architect_brief_is_offered_the_record_tools(sqlite_session, monkeypatch):
    company, own, dep, _unrelated, _foreign = _world(sqlite_session)
    engine = sqlite_session.get_bind()
    workspace = developer_tools.WorkspaceToolbox(lambda *a: {}, "/w")
    monkeypatch.setattr(developer_tools, "read_tools", lambda *a: workspace)
    box = architect_brief.read_tools(company.id, own.id, engine)
    (board,) = [b for b in box.builtin if isinstance(b, board_tools.BoardToolbox)]
    assert board.scope.task_ids == frozenset({own.id, dep.id})
    names = {s["function"]["name"] for s in box.schemas()}
    assert {"board__task", "workspace__read"} <= names
    assert not names & {"workspace__edit", "workspace__run_tests"}
    note = architect_brief.note_for(box)
    assert "board__task" in note and "list, read, search" in note
    # No daemon, no reads: the brief stays one plain call.
    monkeypatch.setattr(developer_tools, "read_tools", lambda *a: None)
    assert architect_brief.read_tools(company.id, own.id, engine) is None


def test_the_record_tools_can_be_turned_off(sqlite_session, monkeypatch):
    from config import settings

    company, own, _dep, _unrelated, _foreign = _world(sqlite_session)
    monkeypatch.setattr(settings, "BOARD_TOOLS_ENABLED", False)
    engine = sqlite_session.get_bind()
    assert (
        board_record.toolbox_for_task(company.id, own.id, rounds=3, engine=engine)
        is None
    )


def _execute(monkeypatch, comments, tools=True):
    board = board_tools.BoardToolbox(
        board_tools.Scope(1, frozenset({1})), lambda *a: {}, lambda *a: {}, rounds=3
    )
    monkeypatch.setattr(board_record, "toolbox_for_task", lambda *a, **k: board)
    workspace = developer_tools.WorkspaceToolbox(lambda *a: {}, "/w") if tools else None
    monkeypatch.setattr(developer_tools, "toolbox_for", lambda *a, **k: workspace)
    monkeypatch.setattr(developer_tools, "enter_task_worktree", lambda *a, **k: None)
    model = _Model([_answer("Done.")])
    monkeypatch.setattr(worker_execute, "LLMProxyClient", lambda base_url=None: model)
    agent = {"role": "Developer", "model": "m"}
    item = {"id": 1, "title": "T", "description": "x", "comments": comments}
    worker_execute._execute_task(item, agent, "S")
    return agent, model


REWORK = [
    {"author": "Developer", "text": "# Artifact: a.py\nx = 1\n"},
    {"author": "QA", "text": "NEEDS WORK: x must be 2."},
]


def _offered(model):
    return {t["function"]["name"] for t in model.requests[0]["tools"] or []}


def test_an_execution_turn_on_a_rework_is_offered_its_record(monkeypatch):
    agent, model = _execute(monkeypatch, REWORK)
    assert {"board__task", "board__evidence"} <= set(agent["tools_offered"]["names"])
    assert {"board__task", "board__evidence", "workspace__read"} <= _offered(model)


def test_a_first_attempt_is_not_offered_the_record_tools(monkeypatch):
    agent, model = _execute(monkeypatch, [])
    assert "board__task" not in agent["tools_offered"]["names"]
    assert "workspace__read" in _offered(model) and "board__task" not in _offered(model)


def test_a_rework_without_tools_stays_a_plain_call(monkeypatch):
    agent, model = _execute(monkeypatch, REWORK, tools=False)
    assert agent["tools_offered"] == {} and model.requests[0]["tools"] is None
