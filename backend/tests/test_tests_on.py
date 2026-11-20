"""Tests on, in the project's own container (issue #766).

A run on this repository in the sandbox showed what was missing: each task
starts from a worktree with nothing installed and installs ran only when a
delivery changed a manifest; a Poetry virtualenv was lost with the install's
container without the environment volume; the daemon stopped a suite after
120 s; the folders' suites of a repository whose root has none did not count
as its tests; and the Developer's ``run_tests`` was not offered there at all.
"""

from __future__ import annotations

import asyncio
import json
from pathlib import Path

import pytest
from config import settings
from factories import company as _company
from models.company import Company
from services.evidence import required_checks
from services.tools import developer_tool_schemas, test_runs
from services.tools.developer_tools import WorkspaceToolbox
from services.workspace import dependency_install, project_profile, sandbox


class _Daemon:
    """Answers every ``workspace.execute`` with exit 0 and keeps the requests."""

    def __init__(self, exit_code: int = 0):
        self.requests: list[tuple[str, dict]] = []
        self.exit_code = exit_code

    async def request(self, subject, payload, timeout):
        self.requests.append((subject, payload))
        return self.reply(subject, payload)

    def reply(self, subject, payload):
        if subject == "workspace.execute":
            purpose = (payload.get("sandbox") or {}).get("purpose")
            code = self.exit_code if purpose == "install" else 0
            return {"exit_code": code, "stdout": "ok\n", "stderr": "", "duration_ms": 5}
        return {"error": {"status": 404, "message": "not served here"}}

    def executed(self):
        return [p for s, p in self.requests if s == "workspace.execute"]


def _monorepo(root: Path) -> Path:
    for name, text in {
        "README.md": "",
        "backend/pyproject.toml": "[tool.poetry]\nname='b'\n[tool.pytest.ini_options]\n",
        "frontend/package.json": json.dumps({"scripts": {"test": "vitest run"}}),
        "frontend/package-lock.json": "{}",
    }.items():
        path = root / name
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(text)
    return root


@pytest.fixture
def isolated(monkeypatch):
    monkeypatch.setattr(settings, "DELIVERY_SANDBOX", "container")
    monkeypatch.setattr(settings, "DELIVERY_SANDBOX_IMAGE", "")
    monkeypatch.setattr(settings, "DELIVERY_INSTALL_ENABLED", True)
    monkeypatch.setattr(settings, "DELIVERY_TEST_COMMAND", "")
    monkeypatch.setattr(settings, "DELIVERY_INSTALL_COMMAND", "")


def _ctx(session, company, root, writes, installs=None):
    evidence = {
        "workspace_root": str(root),
        "workspace_writes": [
            {"path": p, "status": "written", "sha256": "s"} for p in writes
        ],
    }
    daemon = _Daemon()
    ctx = project_profile.Context(
        daemon, {"company_id": company.id}, evidence, session.get_bind()
    )
    ctx._company = {"id": company.id, "overrides": {}, "installs": installs or {}}
    return ctx, daemon, evidence


def _detected(monkeypatch, root):
    from services.workspace import stack_profiles, stack_roots

    async def detect(nats, workspace_root):
        return stack_profiles.detect(str(root)), stack_roots.detect_folders(str(root))

    monkeypatch.setattr(project_profile, "detect_project", detect)


def test_defaults_turn_the_tests_and_the_environment_volume_on():
    fields = type(settings).model_fields
    assert fields["DELIVERY_TESTS_ENABLED"].default is True
    assert fields["DELIVERY_SANDBOX_ENV"].default is True
    assert fields["DELIVERY_TEST_RUN_SECONDS"].default == 600


def test_a_test_run_may_take_minutes_within_the_daemon_s_ceiling(monkeypatch):
    monkeypatch.setattr(settings, "DELIVERY_TEST_RUN_SECONDS", 600)
    monkeypatch.setattr(settings, "DELIVERY_TEST_TIMEOUT_SECONDS", 130.0)
    assert sandbox.test_seconds() == 600 and sandbox.test_wait() == 630.0
    monkeypatch.setattr(settings, "DELIVERY_TEST_RUN_SECONDS", 5000)
    assert sandbox.test_seconds() == sandbox.DAEMON_MAX_SECONDS


def test_a_folder_never_installed_in_a_container_installs_before_its_suite(
    sqlite_session, tmp_path, monkeypatch, isolated
):
    root = _monorepo(tmp_path)
    _detected(monkeypatch, root)
    company = _company(sqlite_session)
    ctx, daemon, evidence = _ctx(sqlite_session, company, root, ["backend/app/x.py"])

    asyncio.run(dependency_install.install_if_needed(ctx))

    sent = daemon.executed()
    assert [p["command"] for p in sent] == ["cd backend && poetry install"]
    assert sent[0]["sandbox"]["purpose"] == "install"
    assert sent[0]["sandbox"]["network"] is True
    record = evidence["command_runs"][0]
    assert record["first_install"] is True and record["folder"] == "backend"
    sqlite_session.expire_all()
    installs = sqlite_session.get(Company, company.id).dependency_installs
    assert installs == {f"container:{root}": {"backend/": "installed"}}

    # The next delivery in the same worktree finds the mark: no install.
    again, daemon2, _ = _ctx(
        sqlite_session, company, root, ["backend/app/y.py"], installs=installs
    )
    asyncio.run(dependency_install.install_if_needed(again))
    assert daemon2.executed() == []


def test_on_the_host_installs_still_follow_the_manifests(
    sqlite_session, tmp_path, monkeypatch, isolated
):
    monkeypatch.setattr(settings, "DELIVERY_SANDBOX", "host")
    root = _monorepo(tmp_path)
    _detected(monkeypatch, root)
    company = _company(sqlite_session)
    ctx, daemon, evidence = _ctx(sqlite_session, company, root, ["backend/app/x.py"])

    asyncio.run(dependency_install.install_if_needed(ctx))

    assert daemon.executed() == [] and "command_runs" not in evidence


def test_a_failed_first_install_leaves_no_mark(
    sqlite_session, tmp_path, monkeypatch, isolated
):
    root = _monorepo(tmp_path)
    _detected(monkeypatch, root)
    company = _company(sqlite_session)
    ctx, daemon, evidence = _ctx(sqlite_session, company, root, ["frontend/src/a.ts"])
    daemon.exit_code = 1

    asyncio.run(dependency_install.install_if_needed(ctx))

    assert evidence["command_runs"][0]["exit_code"] == 1
    sqlite_session.expire_all()
    assert not sqlite_session.get(Company, company.id).dependency_installs


def test_the_folders_suites_are_the_tests_of_a_root_without_one():
    paths = ["backend/app/x.py"]
    evidence = {
        "test_run": {"command": None, "reason": "no test command found"},
        "command_runs": [
            {"kind": "suite", "folder": "backend", "command": "pytest", "exit_code": 0}
        ],
    }
    assert required_checks.missing(paths, evidence, []) == []
    # A suite that could not run is no observation.
    evidence["command_runs"][0]["exit_code"] = None
    assert required_checks.missing(paths, evidence, [])[0]["name"] == "tests_run"
    # An install is not a test run.
    evidence["command_runs"] = [{"kind": "install", "exit_code": 0}]
    assert required_checks.missing(paths, evidence, [])


PLANS = {
    "backend": {"test": "poetry run python -m pytest -q", "install": "poetry install"},
    "frontend": {"test": "npm test --silent", "install": "npm ci"},
}


def test_run_tests_is_offered_with_the_folders_of_a_monorepo():
    toolbox = WorkspaceToolbox(lambda *a: {}, "/p", folder_tests=PLANS)
    schema = next(
        s for s in toolbox.schemas() if s["function"]["name"].endswith("run_tests")
    )
    parameters = schema["function"]["parameters"]
    assert parameters["properties"]["folder"]["enum"] == ["backend", "frontend"]
    assert parameters["required"] == ["folder"]
    assert "`backend`" in schema["function"]["description"]
    # Nothing to run: not offered.
    bare = WorkspaceToolbox(lambda *a: {}, "/p")
    assert not [
        s for s in bare.schemas() if s["function"]["name"].endswith("run_tests")
    ]
    # A single-root project keeps the schema as it was.
    single = developer_tool_schemas.run_tests_schema(
        developer_tool_schemas.SCHEMAS[-1], {"": {"test": "pytest"}}
    )
    assert single is developer_tool_schemas.SCHEMAS[-1]


def _call(toolbox, **arguments):
    return toolbox.call(
        {
            "function": {
                "name": f"{developer_tool_schemas.SERVER}__run_tests",
                "arguments": json.dumps(arguments),
            }
        },
        1,
    )


def test_the_first_run_of_a_folder_installs_it_in_the_container(monkeypatch):
    sent: list[dict] = []

    def requester(subject, payload, timeout):
        sent.append({**payload, "timeout": timeout})
        return {"exit_code": 0, "stdout": "3 passed\n", "stderr": ""}

    box = {"mode": "container", "stack_image": True, "network": False}
    toolbox = WorkspaceToolbox(requester, "/p", folder_tests=PLANS, sandbox=box)

    _observation, text = _call(
        toolbox,
        folder="backend",
        command="poetry run python -m pytest -q tests/test_x.py",
    )
    _call(toolbox, folder="backend")

    assert [p["command"] for p in sent] == [
        "cd backend && poetry install",
        "cd backend && poetry run python -m pytest -q tests/test_x.py",
        "cd backend && poetry run python -m pytest -q",
    ]
    assert sent[0]["sandbox"]["purpose"] == "install" and sent[0]["sandbox"]["network"]
    assert sent[1]["sandbox"] == box
    assert sent[1]["timeout_seconds"] == sandbox.test_seconds()
    assert "3 passed" in text


def test_a_failed_install_is_what_the_model_sees_and_no_test_runs():
    sent: list[str] = []

    def requester(subject, payload, timeout):
        sent.append(payload["command"])
        return {"exit_code": 1, "stdout": "", "stderr": "resolver said no"}

    toolbox = WorkspaceToolbox(
        requester, "/p", folder_tests=PLANS, sandbox={"mode": "container"}
    )
    _observation, text = _call(toolbox, folder="frontend")

    assert sent == ["cd frontend && npm ci"]
    assert "exited 1, so the tests were not run" in text and "resolver said no" in text


def test_a_monorepo_call_without_a_folder_names_the_choices():
    with pytest.raises(RuntimeError, match="`backend`, `frontend`"):
        test_runs.target(PLANS, None)
    with pytest.raises(RuntimeError, match="no test command for folder `docs`"):
        test_runs.target(PLANS, "docs")
    assert test_runs.target(PLANS, "backend/")[0] == "backend"


def test_on_the_host_the_tool_installs_nothing():
    assert (
        test_runs.install_first(lambda *a: {}, "/p", {"mode": "host"}, "backend", "x")
        is None
    )
    assert test_runs.install_first(lambda *a: {}, "/p", None, "backend", "x") is None
