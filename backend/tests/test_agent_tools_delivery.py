"""The Developer's edits through the toolbox and the worker.

The toolbox offers the edit only where edits may go and records it as an
observation that comes back to deliver; the worker delivers the edits and the
daemon sees a modified file, verified; the history check refuses an edit
recorded without its hashes; the minimum-deliverable gate counts the edited
files; a secret written with the edit tool refutes the delivery, and QA's
tests written with it can be restored.

The Developer's tools from SWE-agent's agent-computer interface (issue #359).
The daemon's functions (``vscode-daemon/workspace_read.py``,
``workspace_edit.py``) run for real against ``tmp_path``; the shared fakes are
in ``agent_tools_helpers``.
"""

from __future__ import annotations

import asyncio
import hashlib

import agent_tools_helpers
import worker_stages
from agent_tools_helpers import _call, _DaemonOverNATS, _Requester, _sha
from config import settings
from models.agent_run import AgentRun, Verification
from models.company import Company, ProjectDiscovery
from models.sprint import BacklogItem, Sprint
from services.evidence import risk
from services.loop.worker import deliver as worker_deliver
from services.loop.worker import quality_gate
from services.tools import developer_tools, mcp_tools, workspace_edits
from sqlmodel import select

# The shared fixtures.
daemon = agent_tools_helpers.daemon
project = agent_tools_helpers.project


def test_the_toolbox_offers_the_edit_only_where_edits_may_go(project, daemon):
    plain = developer_tools.WorkspaceToolbox(_Requester(daemon), str(project))
    names = [s["function"]["name"] for s in plain.schemas()]
    assert "workspace__find_file" in names and "workspace__edit" not in names
    refused, text = plain.call(_call("workspace__edit", path="src/notes.txt"), 1)
    assert refused["reason"] == "unknown tool" and text == mcp_tools.NO_TOOL_NOTE
    editing = developer_tools.WorkspaceToolbox(
        _Requester(daemon), str(project), can_edit=True
    )
    assert [s["function"]["name"] for s in editing.schemas()][-1] == "workspace__edit"
    assert editing.tools["workspace__edit"]["read_only"] is False
    assert editing.tools["workspace__read"]["read_only"] is True


def test_an_edit_through_the_toolbox_is_an_observation_and_comes_back_to_deliver(
    project, daemon
):
    requester = _Requester(daemon)
    box = developer_tools.WorkspaceToolbox(
        requester, str(project), can_edit=True, edit_suffixes={".py", ".json"}
    )
    target = project / "src" / "greet.py"
    original = target.read_bytes()
    observation, text = box.call(
        _call(
            "workspace__edit",
            path="src/greet.py",
            search="VERSION = 1",
            replace="VERSION = 9",
        ),
        1,
    )
    assert observation["tool"] == "edit" and observation["read_only"] is False
    assert observation["is_error"] is False and observation["path"] == "src/greet.py"
    assert observation["sha256_before"] == hashlib.sha256(original).hexdigest()
    assert (
        observation["sha256_after"] == _sha(target)
        and observation["checked"] == "python"
    )
    assert text.startswith("Applied to src/greet.py (checked: python)")
    # A later read sees the edit; the record keeps that file's hash.
    read, text = box.call(_call("workspace__read", path="src/greet.py"), 2)
    assert "VERSION = 9" in text and read["sha256"] == observation["sha256_after"]
    broken, text = box.call(
        _call("workspace__edit", path="src/greet.py", start_line=1, new_text="def ("), 3
    )
    assert broken["is_error"] is True and "would not parse" in text
    wrong_type, text = box.call(
        _call("workspace__edit", path="src/notes.txt", search="one", replace="1"), 3
    )
    assert wrong_type["is_error"] is True and "does not write .txt" in text
    found, text = box.call(_call("workspace__find_file", pattern="*.json"), 4)
    assert found["match_count"] == 1 and text == "src/config.json"
    # The workspace edit never raises the tier: its change reaches the disk
    # only through the delivery, classified by its paths.
    low = {"tier": "low", "reasons": []}
    assert risk.raise_for_tool_calls(low, [observation]) == low
    box.close()
    assert target.read_bytes() == original
    assert requester.subjects[-1] == "workspace.edit.finish"
    assert [f["path"] for f in box.edited_files] == ["src/greet.py"]
    assert "VERSION = 9" in box.edited_files[0]["content"]
    targets = workspace_edits.with_tool_edits(
        [{"path": "README.md", "content": "# x\n"}], box.edited_files
    )
    assert [(t["path"], t.get("source")) for t in targets] == [
        ("README.md", None),
        ("src/greet.py", "workspace_edit"),
    ]
    # A file the answer delivers itself keeps its own block.
    own = [{"path": "src/greet.py", "content": "X = 1\n"}]
    assert workspace_edits.with_tool_edits(own, box.edited_files) == own
    # Nothing edited: close asks the daemon nothing.
    quiet = developer_tools.WorkspaceToolbox(requester, str(project), can_edit=True)
    calls = len(requester.subjects)
    quiet.close()
    assert len(requester.subjects) == calls and quiet.edited_files == []


def test_edits_go_to_the_task_s_worktree_or_a_project_without_the_git_workflow(
    sqlite_session, tmp_path
):
    engine = sqlite_session.get_bind()
    company = Company(company_name=f"Edits Co {tmp_path.name}", git_workflow=False)
    branches = Company(company_name=f"Branches Co {tmp_path.name}", git_workflow=True)
    sqlite_session.add_all([company, branches])
    sqlite_session.commit()
    bridge = {"resolve_root": lambda company_id: "/proj"}
    assert workspace_edits.allowed(bridge, company.id, "/proj", engine) is True
    # The person's checkout of a company that delivers on branches: never.
    assert workspace_edits.allowed(bridge, branches.id, "/proj", engine) is False
    # Its task's own worktree: yes.
    assert (
        workspace_edits.allowed(
            bridge, branches.id, "/proj/.git/fabrique-worktrees/t", engine
        )
        is True
    )
    assert workspace_edits.allowed(bridge, company.id, "/proj", None) is False
    assert ".py" in workspace_edits.delivery_suffixes()


def test_the_worker_delivers_the_tool_edits_and_the_daemon_sees_a_modified_file(
    project, daemon, monkeypatch, sqlite_session, tmp_path
):
    bind = sqlite_session.get_bind()
    ProjectDiscovery.__table__.create(bind, checkfirst=True)
    worker_stages.use_engine(monkeypatch, bind)
    monkeypatch.setenv("AUDIT_LOG_PATH", str(tmp_path / "audit"))
    monkeypatch.setattr(settings, "DELIVERY_TESTS_ENABLED", False)
    company = Company(company_name=f"Deliver Co {tmp_path.name}")
    sqlite_session.add(company)
    sqlite_session.commit()
    sqlite_session.add(
        ProjectDiscovery(company_id=company.id, project_path=str(project))
    )
    sprint = Sprint(company_id=company.id, sprint_name="T", scope_size="S")
    sqlite_session.add(sprint)
    sqlite_session.commit()
    item = BacklogItem(sprint_id=sprint.id, title="Version", status="in_progress")
    sqlite_session.add(item)
    sqlite_session.commit()

    box = developer_tools.WorkspaceToolbox(
        _Requester(daemon), str(project), can_edit=True
    )
    edit, _text = box.call(
        _call(
            "workspace__edit",
            path="src/greet.py",
            search="VERSION = 1",
            replace="VERSION = 2",
        ),
        1,
    )
    box.close()
    agent = {
        "role": "Developer",
        "served_model": "m",
        "tool_calls": [edit],
        "tool_edits": box.edited_files,
    }
    asyncio.run(
        worker_deliver._deliver_artifacts(
            _DaemonOverNATS(daemon),
            {"id": item.id, "title": item.title},
            agent,
            {"id": sprint.id, "company_id": company.id},
            "Bumped the version with the edit tool.",
        )
    )
    assert "VERSION = 2" in (project / "src" / "greet.py").read_text()
    sqlite_session.expire_all()
    run = sqlite_session.exec(
        select(AgentRun).where(
            AgentRun.company_id == company.id,
            AgentRun.external_ref == f"task-{item.id}",
        )
    ).first()
    verification = sqlite_session.exec(
        select(Verification).where(Verification.agent_run_id == run.id)
    ).first()
    writes = {w["path"]: w for w in verification.evidence["workspace_writes"]}
    # Restored before the delivery, so the daemon's diff shows the change.
    assert writes["src/greet.py"]["status"] == "written"
    assert writes["src/greet.py"]["change"] == "modified"
    assert writes["src/greet.py"]["sha256"] == edit["sha256_after"]
    # Tests are off in this test: the code change is not shown to work, so
    # the verdict says so instead of passing by omission (issue #766).
    assert verification.verdict == "unverifiable"
    tests_run = next(c for c in verification.checks if c["name"] == "tests_run")
    assert tests_run["outcome"] == "unverifiable"
    assert "tests are disabled" in tests_run["observation"]
    assert verification.evidence["verdict_policy"] == {
        "version": "v1",
        "required": ["tests_run"],
    }


def test_the_history_check_refuses_an_edit_recorded_without_its_hashes():
    from history_check import _run_facts
    from models.agent_run import Verification

    def facts(*calls):
        verification = Verification(
            company_id=1,
            agent_run_id=1,
            verdict="verified",
            checks=[],
            evidence={"tool_calls": list(calls)},
        )
        return _run_facts(verification)["reads_without_hash"]

    edit = {"server": "workspace", "tool": "edit", "path": "src/a.py"}
    assert facts(edit | {"sha256_before": "aa", "sha256_after": "bb"}) == []
    assert facts(edit | {"sha256_before": "aa"}) == ["src/a.py"]
    assert facts(edit | {"is_error": True}) == []


def test_the_minimum_deliverable_gate_counts_the_edited_files():
    item = {"title": "Bump the version", "description": "x"}
    edited = [{"path": "src/a.py", "content": "V = 2\n", "changed": True}]
    assert quality_gate._validate_minimum_deliverable(item, "Done.", edited) == (
        True,
        "ok",
    )
    assert quality_gate._validate_minimum_deliverable(item, "", edited) == (True, "ok")
    assert quality_gate._validate_minimum_deliverable(item, "Done.")[0] is False
    # An edit left alone (a conflict) is no delivery.
    conflict = [{"path": "src/a.py", "error": "changed", "changed": False}]
    assert (
        quality_gate._validate_minimum_deliverable(item, "Done.", conflict)[0] is False
    )


def test_a_secret_written_through_the_edit_tool_refutes_the_delivery(sqlite_session):
    from services.evidence.verification import verify_agent_run

    run = AgentRun(
        company_id=1,
        external_ref="task-secret",
        producer="le-fabrique-task-worker",
        claims={"files": ["src/keys.py"]},
        artifact_text="Added the client.",
        artifact_digest="d",
        artifact_size=17,
    )
    sqlite_session.add(run)
    sqlite_session.commit()
    # Built at run time so the source holds no credential-shaped literal.
    secret = 'API_KEY = "' + "sk-" + "abcdefghijklmnopqrstuvwxyz1234" + '"\n'
    evidence = workspace_edits.observe_tools(
        {
            "workspace_root": "/p",
            "expected": {},
            "workspace_writes": [
                {
                    "path": "src/keys.py",
                    "status": "written",
                    "sha256": "aa",
                    "change": "modified",
                }
            ],
        },
        {
            "tool_calls": [
                {
                    "server": "workspace",
                    "tool": "edit",
                    "path": "src/keys.py",
                    "sha256_before": "00",
                    "sha256_after": "aa",
                    "read_only": False,
                }
            ],
            "tool_edits": [
                {
                    "path": "src/keys.py",
                    "content": secret,
                    "sha256": "aa",
                    "changed": True,
                }
            ],
        },
    )
    verification = verify_agent_run(sqlite_session, run, evidence)
    checks = {c["name"]: c for c in verification.checks}
    assert checks["standards"]["outcome"] == "refuted"
    assert verification.verdict == "refuted"
    # Re-verifying the same delivery keeps the edited files: they live on the evidence.
    again = verify_agent_run(sqlite_session, run, dict(verification.evidence))
    assert {c["name"]: c for c in again.checks}["standards"]["outcome"] == "refuted"


def test_qa_tests_written_with_the_edit_tool_can_be_restored(
    sqlite_session, monkeypatch
):
    from services.evidence import acceptance

    company = Company(company_name="Restore Co")
    sqlite_session.add(company)
    sqlite_session.commit()
    sprint = Sprint(company_id=company.id, sprint_name="S", scope_size="S")
    sqlite_session.add(sprint)
    sqlite_session.commit()
    task = BacklogItem(
        sprint_id=sprint.id, title="Tests", kind="acceptance_tests", status="done"
    )
    sqlite_session.add(task)
    sqlite_session.commit()
    run = AgentRun(
        company_id=company.id,
        external_ref=f"task-{task.id}",
        producer="le-fabrique-task-worker",
        claims={"files": ["tests/test_req_1.py"]},
        artifact_text="Wrote the test with the edit tool.",
        artifact_digest="d",
        artifact_size=34,
    )
    sqlite_session.add(run)
    sqlite_session.commit()
    body = "def test_req_1_1():\n    assert False\n"
    sqlite_session.add(
        Verification(
            company_id=company.id,
            agent_run_id=run.id,
            verdict="verified",
            checks=[],
            evidence={
                "tool_edit_files": [
                    {"path": "tests/test_req_1.py", "content": body, "sha256": "x"}
                ]
            },
        )
    )
    sqlite_session.commit()
    sources = acceptance.test_sources_of(
        sqlite_session.get_bind(), sprint.id, ["tests/test_req_1.py", "tests/other.py"]
    )
    assert sources == {"tests/test_req_1.py": body}
