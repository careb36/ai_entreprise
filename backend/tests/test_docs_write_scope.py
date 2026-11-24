"""Where a documentation turn writes follows the step (issue #766).

The docs-only write policy (``services/tools/docs_write_policy.py``) reads the
step table (``contracts/tool_steps.write_scope``): the documentation step,
and the TechWriter within any step that writes, write documentation only; a
role's write scope narrows ``W`` and never offers it where the step does not.
``toolbox_for`` applies it with the turn's step, a task whose kind is
documentation is executed as the documentation step, and the files an answer
delivers in its own blocks (``workspace.generate``) follow the same rule: a
path outside the documentation is not delivered and the run's evidence says
which and why (``docs_write``).
"""

from __future__ import annotations

import asyncio

import agent_tools_helpers
import developer_tools_helpers
import pytest
import worker_stages
from agent_tools_helpers import _DaemonOverNATS
from config import settings
from models.agent_run import AgentRun, Verification
from models.company import Company, ProjectDiscovery
from models.sprint import BacklogItem, Sprint
from services.contracts import tool_steps
from services.loop.worker import deliver as worker_deliver
from services.loop.worker import execute_tools
from services.tools import developer_tools, mcp_tools, workspace_edits
from services.workspace import stack_image
from sqlmodel import select

project = agent_tools_helpers.project
daemon = agent_tools_helpers.daemon


@pytest.fixture()
def bound(monkeypatch, project, daemon):
    """``toolbox_for`` bound to *project*, edits allowed, no MCP or sandbox."""
    requester = developer_tools_helpers._Requester(daemon)
    monkeypatch.setattr(developer_tools, "_request_from_thread", requester)
    monkeypatch.setattr(
        developer_tools,
        "_bridge",
        {"nats": object(), "resolve_root": lambda company_id: str(project)},
    )
    monkeypatch.setattr(
        mcp_tools.Toolbox, "load", classmethod(lambda cls, *a, **k: None)
    )
    monkeypatch.setattr(developer_tools, "_detect_tests", lambda *a: (None, {}))
    monkeypatch.setattr(workspace_edits, "allowed", lambda *a: True)
    monkeypatch.setattr(
        developer_tools.sandbox, "request_for_company_id", lambda *a: None
    )
    monkeypatch.setattr(stack_image, "prepare_from_thread", lambda *a: None)
    monkeypatch.setattr(settings, "EDIT_LINT_ENABLED", False)
    return requester


def test_the_step_table_narrows_where_a_turn_writes(monkeypatch):
    E, D = tool_steps.EXECUTE, tool_steps.DOCUMENT
    assert tool_steps.write_scope(E, "Developer") is None
    assert tool_steps.write_scope(E, "TechWriter") == tool_steps.DOCS
    assert tool_steps.write_scope(D, "Developer") == tool_steps.DOCS
    # A scope never offers W: a step without it has no scope at all.
    assert tool_steps.write_scope(tool_steps.QA_REVIEW, "TechWriter") is None
    assert tool_steps.record(E, "TechWriter")["write_scope"] == "docs"
    assert "write_scope" not in tool_steps.record(E, "Developer")
    assert tool_steps.execution_step("Documentation") == D
    assert tool_steps.execution_step(None) == E
    assert tool_steps.writable(E, "TechWriter", "docs/a.py")
    assert not tool_steps.writable(E, "TechWriter", "src/app.py")
    assert tool_steps.writable(E, "Developer", "src/app.py")
    monkeypatch.setattr(tool_steps, "ROLE_WRITE_SCOPE", {})
    assert tool_steps.write_scope(E, "TechWriter") is None


def test_toolbox_for_applies_the_scope_of_its_step(bound, monkeypatch):
    def docs_only(role, **kw):
        box = developer_tools.toolbox_for(1, role, **kw).workspace
        return box.docs_only

    assert docs_only("TechWriter") is True
    assert docs_only("Developer") is False
    assert docs_only("Developer", step=tool_steps.DOCUMENT) is True
    # The TechWriter's narrowing is the table's, not a role list of the policy.
    monkeypatch.setattr(tool_steps, "ROLE_WRITE_SCOPE", {})
    assert docs_only("TechWriter") is False
    assert docs_only("TechWriter", step=tool_steps.DOCUMENT) is True


def test_a_documentation_task_is_executed_as_the_documentation_step(monkeypatch):
    asked = {}

    def fake_toolbox_for(company_id, role, **kw):
        asked.update(kw)
        return developer_tools.WorkspaceToolbox(lambda *a: {}, "/w", can_edit=True)

    monkeypatch.setattr(developer_tools, "toolbox_for", fake_toolbox_for)
    agent = {"role": "Developer"}
    limits = {"tool_rounds": 3, "result_max_chars": 1000}
    item = {"id": 7, "kind": "documentation"}
    execute_tools.task_toolbox(
        1, "Developer", item, agent, limits, rework=False, prepared=None, engine=None
    )
    assert asked["step"] == tool_steps.DOCUMENT
    assert agent["tools_offered"]["step"] == "documentation"
    assert agent["tools_offered"]["write_scope"] == "docs"
    execute_tools.task_toolbox(
        1,
        "Developer",
        {"id": 8},
        agent,
        limits,
        rework=False,
        prepared=None,
        engine=None,
    )
    assert asked["step"] == tool_steps.EXECUTE
    assert "write_scope" not in agent["tools_offered"]


DELIVERABLE = (
    "The guide.\n\n"
    "```markdown path=docs/guide.md\n# Guide\n\nHow to run it.\n```\n\n"
    "```python path=src/app.py\nprint('not documentation')\n```\n"
)


def _deliver(sqlite_session, monkeypatch, tmp_path, project, daemon, role, kind=None):
    bind = sqlite_session.get_bind()
    ProjectDiscovery.__table__.create(bind, checkfirst=True)
    worker_stages.use_engine(monkeypatch, bind)
    monkeypatch.setenv("AUDIT_LOG_PATH", str(tmp_path / "audit"))
    monkeypatch.setattr(settings, "DELIVERY_TESTS_ENABLED", False)
    company = Company(company_name=f"Docs Co {tmp_path.name} {role}")
    sqlite_session.add(company)
    sqlite_session.commit()
    sqlite_session.add(
        ProjectDiscovery(company_id=company.id, project_path=str(project))
    )
    sprint = Sprint(company_id=company.id, sprint_name="T", scope_size="S")
    sqlite_session.add(sprint)
    sqlite_session.commit()
    item = BacklogItem(sprint_id=sprint.id, title="Document", status="in_progress")
    sqlite_session.add(item)
    sqlite_session.commit()
    agent = {"role": role, "served_model": "m", "tool_calls": [], "tool_edits": []}
    payload = asyncio.run(
        worker_deliver._deliver_artifacts(
            _DaemonOverNATS(daemon),
            {"id": item.id, "title": item.title, "kind": kind},
            agent,
            {"id": sprint.id, "company_id": company.id},
            DELIVERABLE,
        )
    )
    sqlite_session.expire_all()
    run = sqlite_session.exec(
        select(AgentRun).where(AgentRun.external_ref == f"task-{item.id}")
    ).first()
    verification = sqlite_session.exec(
        select(Verification).where(Verification.agent_run_id == run.id)
    ).first()
    return payload, verification.evidence


@pytest.mark.parametrize(("role", "kind"), [("TechWriter", None), ("QA", "docs")])
def test_the_answers_blocks_outside_the_docs_are_not_delivered(
    project, daemon, monkeypatch, sqlite_session, tmp_path, role, kind
):
    payload, evidence = _deliver(
        sqlite_session, monkeypatch, tmp_path, project, daemon, role, kind
    )
    assert (project / "docs" / "guide.md").read_text().startswith("# Guide")
    assert not (project / "src" / "app.py").exists()
    assert evidence["docs_write"] == {
        "refused": ["src/app.py"],
        "reason": worker_deliver.DOCS_ONLY_REASON,
    }
    assert [w["path"] for w in evidence["workspace_writes"]] == ["docs/guide.md"]
    assert "Not delivered" in payload and "src/app.py" in payload


def test_a_developers_blocks_are_all_delivered(
    project, daemon, monkeypatch, sqlite_session, tmp_path
):
    _payload, evidence = _deliver(
        sqlite_session, monkeypatch, tmp_path, project, daemon, "Developer"
    )
    assert (project / "src" / "app.py").exists()
    assert "docs_write" not in evidence
