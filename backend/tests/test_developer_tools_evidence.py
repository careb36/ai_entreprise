"""The Developer's reads as evidence.

The Verifier says which modified files were read first and the tier follows;
the worker offers the tools, reads through the daemon and keeps the reads as
evidence; the history check refuses a read recorded without a hash.

The Developer's workspace tools (``services/developer_tools.py``) and the
daemon's read handlers (``vscode-daemon/workspace_read.py``). The daemon's
functions run for real against ``tmp_path``; the shared fakes are in
``developer_tools_helpers``.
"""

from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "app"))

import developer_tools_helpers
from developer_tools_helpers import (
    _answer,
    _answer_with_calls,
    _call,
    _load_daemon,
    _Model,
    _Requester,
)
from models.agent_run import AgentRun
from models.company import Company
from services.evidence import context_read, risk
from services.evidence.verification import check_risk_tier, verify_agent_run
from services.loop.worker import execute as worker_execute
from services.tools import developer_tools, mcp_tools

# The shared fixtures.
project = developer_tools_helpers.project


def _run():
    return AgentRun(
        company_id=1,
        external_ref="task-1",
        producer="p",
        artifact_digest="d",
        artifact_size=1,
        claims={"files": ["src/a.py"]},
    )


def test_the_verifier_says_which_modified_files_were_read_first_and_the_tier_follows():
    calls = [
        {
            "server": "workspace",
            "tool": "read",
            "is_error": False,
            "read_only": True,
            "path": "src/a.py",
            "sha256": "aa",
        },
        {
            "server": "workspace",
            "tool": "read",
            "is_error": True,
            "read_only": True,
            "path": "src/none.py",
            "sha256": None,
        },
        {"server": "workspace", "tool": "list", "is_error": False, "read_only": True},
    ]
    writes = [
        {"path": "src/a.py", "status": "written", "change": "modified"},
        {"path": "src/b.py", "status": "written", "change": "modified"},
        {"path": "src/new.py", "status": "written", "change": "added"},
    ]
    assert context_read.files_read(calls) == {"src/a.py": "aa"}
    check = context_read.check_context_read(
        {"tool_calls": calls, "workspace_writes": writes}
    )
    assert check["name"] == "context_read" and check["outcome"] == "pass"
    assert (check["read"], check["modified_read"], check["modified_unread"]) == (
        ["src/a.py"],
        ["src/a.py"],
        ["src/b.py"],
    )
    assert check["observation"] == "modified without reading first: src/b.py"
    tier = check_risk_tier(_run(), {"tool_calls": calls, "workspace_writes": writes})
    assert tier["tier"] == "medium" and tier["reasons"] == [
        "modified without reading first: src/b.py"
    ]
    # Every modified file read: low. New files only: a record, no reason.
    all_read = calls + [
        {
            "server": "workspace",
            "tool": "read",
            "is_error": False,
            "read_only": True,
            "path": "src/b.py",
            "sha256": "bb",
        }
    ]
    assert (
        check_risk_tier(_run(), {"tool_calls": all_read, "workspace_writes": writes})[
            "tier"
        ]
        == "low"
    )
    assert (
        context_read.check_context_read(
            {"tool_calls": all_read, "workspace_writes": writes}
        )["observation"]
        == "every modified file was read first (2)"
    )
    only_new = context_read.check_context_read(
        {"tool_calls": calls, "workspace_writes": writes[2:]}
    )
    assert only_new["observation"] == "read 1 file(s); no existing file was changed"
    # No workspace tools in use: a producer without them is classified by its paths alone.
    assert (
        context_read.check_context_read({"tool_calls": [], "workspace_writes": []})
        is None
    )
    no_tools = check_risk_tier(_run(), {"tool_calls": [], "workspace_writes": writes})
    assert no_tools["tier"] == "low"
    assert (
        risk.raise_for_unread_changes({"tier": "high", "reasons": ["x"]}, check)["tier"]
        == "high"
    )


def test_the_worker_offers_the_tools_reads_through_the_daemon_and_keeps_the_reads_as_evidence(
    project, monkeypatch, sqlite_session
):
    """``_execute_task`` carries the repository map, the model reads a file
    before delivering, and the run's verification shows the read beside the
    modified file."""
    daemon = _load_daemon()
    monkeypatch.setattr(
        mcp_tools.Toolbox, "load", classmethod(lambda cls, *a, **k: None)
    )
    developer_tools.bind(object(), object(), lambda company_id: str(project))
    monkeypatch.setattr(developer_tools, "_request_from_thread", _Requester(daemon))
    model = _Model(
        [
            _answer_with_calls(_call("workspace__read", path="src/greet.py")),
            _answer(
                "```python path=src/greet.py\ndef greet(name):\n    return 'Hi ' + name\n```\nDone."
            ),
        ]
    )
    monkeypatch.setattr(worker_execute, "LLMProxyClient", lambda base_url=None: model)
    agent = {"role": "Developer", "model": "m"}
    try:
        content = worker_execute._execute_task(
            {"id": 7, "title": "Greet", "description": "x"}, agent, "S1", company_id=1
        )
    finally:
        developer_tools.unbind()
    assert "Done." in content
    prompt = model.requests[0]["messages"][1]["content"]
    assert "## Repository" in prompt and "src/greet.py" in prompt
    assert [s["function"]["name"] for s in model.requests[0]["tools"]] == [
        "workspace__list",
        "workspace__read",
        "workspace__search",
        "workspace__find_file",
    ]
    read = agent["tool_calls"][0]
    assert (
        read["tool"] == "read"
        and read["path"] == "src/greet.py"
        and len(read["sha256"]) == 64
    )

    company = Company(company_name="Tools Co")
    sqlite_session.add(company)
    sqlite_session.commit()
    run = AgentRun(
        company_id=company.id,
        external_ref="task-7",
        producer="p",
        artifact_digest="d",
        artifact_size=1,
        claims={"files": ["src/greet.py"]},
    )
    sqlite_session.add(run)
    sqlite_session.commit()
    verification = verify_agent_run(
        sqlite_session,
        run,
        {
            "tool_calls": agent["tool_calls"],
            "workspace_writes": [
                {
                    "path": "src/greet.py",
                    "status": "written",
                    "change": "modified",
                    "sha256": "x",
                }
            ],
        },
    )
    names = [c["name"] for c in verification.checks]
    assert "tools_used" in names and "context_read" in names
    context = next(c for c in verification.checks if c["name"] == "context_read")
    assert (
        context["modified_read"] == ["src/greet.py"]
        and context["modified_unread"] == []
    )
    assert next(c for c in verification.checks if c["name"] == "tools_used")[
        "observation"
    ].startswith("1 MCP tool call(s): workspace.read")


def test_the_history_check_refuses_a_read_recorded_without_a_hash(sqlite_session):
    from history_check import _run_facts, check_task
    from models.agent_run import Verification

    task = {"id": 1, "status": "in_progress", "comments": [], "depends_on": []}
    fine = {"version": 1, "status": "verified", "reads_without_hash": []}
    assert check_task(task, [fine], []) == []
    bad = {"version": 2, "status": "verified", "reads_without_hash": ["src/a.py"]}
    assert check_task(task, [bad], []) == [
        "read_without_hash: run v2 records a read of src/a.py with no sha256"
    ]
    verification = Verification(
        company_id=1,
        agent_run_id=1,
        verdict="verified",
        checks=[],
        evidence={
            "tool_calls": [
                {
                    "server": "workspace",
                    "tool": "read",
                    "path": "src/a.py",
                    "sha256": "aa",
                },
                {"server": "workspace", "tool": "read", "path": "src/b.py"},
                {
                    "server": "workspace",
                    "tool": "read",
                    "path": "src/c.py",
                    "is_error": True,
                },
                {"server": "tracker", "tool": "read", "path": "x"},
            ]
        },
    )
    assert _run_facts(verification)["reads_without_hash"] == ["src/b.py"]
    assert _run_facts(None)["reads_without_hash"] == []
