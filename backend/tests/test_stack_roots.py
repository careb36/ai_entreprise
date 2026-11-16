"""A stack profile per folder (self-hosting retrospective, F5 and follow-up 5):
each folder with its own manifest is detected with its own install and test
commands, a company names or overrides folders and adds commands every
delivery runs, a delivery runs the suites of the folders the daemon saw it
write in, and the Verifier's ``required_commands`` check judges every run
(``services/workspace/stack_roots.py``, ``services/loop/delivery_suites.py``,
``services/evidence/command_runs.py``). Real commands in ``tmp_path``."""

from __future__ import annotations

import asyncio
import hashlib
import importlib.util
import json
import sys
from pathlib import Path

import pytest
from config import settings
from factories import company as _company
from factories import enforcement  # noqa: F401 - the fixture, by name
from factories import member_headers as _member
from models.company import Company
from services.evidence import command_runs, delivery_gate
from services.loop import delivery_checks
from services.release import integration
from services.workspace import (
    dependency_install,
    project_profile,
    stack_profiles,
    stack_roots,
)
from test_app_check import _Daemon, _write

PY = sys.executable
REPO = Path(__file__).resolve().parents[2]


@pytest.fixture(autouse=True)
def _clean_env(monkeypatch):
    monkeypatch.delenv("FABRIQUE_COMMAND_DENY", raising=False)
    monkeypatch.delenv("FABRIQUE_SANDBOX_REQUIRED", raising=False)
    for name in (
        "DELIVERY_INSTALL_COMMAND",
        "DELIVERY_LINT_COMMAND",
        "DELIVERY_START_COMMAND",
        "DELIVERY_E2E_COMMAND",
        "DELIVERY_TEST_COMMAND",
    ):
        monkeypatch.setattr(settings, name, "")
    monkeypatch.setattr(settings, "DELIVERY_SANDBOX", "host")
    monkeypatch.setattr(settings, "DELIVERY_APP_CHECK_ENABLED", False)
    monkeypatch.setattr(settings, "DELIVERY_INSTALL_ENABLED", False)


def _files(root: Path, names: dict[str, str]) -> Path:
    for name, text in names.items():
        path = root / name
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(text)
    return root


def _monorepo(root: Path) -> Path:
    return _files(
        root,
        {
            "README.md": "",
            "docs/guide.md": "",
            "backend/pyproject.toml": "[tool.poetry]\nname='b'\n[tool.pytest.ini_options]\n",
            "frontend/package.json": json.dumps({"scripts": {"test": "vitest run"}}),
            "frontend/package-lock.json": "{}",
            "frontend/node_modules/dep/package.json": "{}",
            "services/api/go.mod": "module api\n",
            "services/api/inner/package.json": "{}",
            "examples/demo/package.json": "{}",
        },
    )


def _ok(marker: str) -> str:
    return f"{PY} -c \"open('{marker}', 'a').write('x'); print('ran {marker}')\""


def _ctx(root: Path, overrides=None, writes=None, acceptance=None):
    evidence = {"workspace_root": str(root)}
    if writes is not None:
        evidence["workspace_writes"] = [_write(p, "s") for p in writes]
    if acceptance is not None:
        evidence["acceptance"] = acceptance
    daemon = _Daemon()
    ctx = project_profile.Context(daemon, {"id": None}, evidence, None)
    ctx._company = {"id": None, "overrides": overrides or {}, "installs": {}}
    return ctx, daemon, evidence


async def _deliver(ctx):
    await delivery_checks._run_tests(ctx.nats, ctx.sprint, ctx.evidence, None, ctx)
    from services.loop import delivery_suites

    await delivery_suites.run(ctx, (ctx.evidence.get("test_run") or {}).get("command"))


def _executed(daemon) -> list[str]:
    return [p["command"] for s, p in daemon.requests if s == "workspace.execute"]


# --- detection and the stored shape -------------------------------------------


def test_each_folder_with_its_own_manifest_is_detected_with_its_commands(tmp_path):
    found = stack_roots.detect_folders(str(_monorepo(tmp_path)))
    assert sorted(found) == ["backend", "frontend", "services/api"]
    assert found["backend"]["install"] == "poetry install"
    assert found["backend"]["test"] == "poetry run python -m pytest -q"
    assert found["frontend"]["install"] == "npm ci --no-audit --no-fund"
    assert found["frontend"]["test"] == "npm test --silent"
    assert found["services/api"]["test"] == "go test ./..."
    # A single-root project is never searched below its root.
    ctx, _daemon, _evidence = _ctx(
        _files(tmp_path / "one", {"go.mod": "module x\n", "web/package.json": "{}"})
    )
    assert asyncio.run(ctx.folders()) == []


def test_an_old_profile_is_one_root_and_a_company_names_and_overrides_folders(tmp_path):
    old = {"test": "make test", "install": ""}
    assert stack_roots.validate_overrides(old) == old
    assert stack_profiles.resolve(None, old)["test"] == "make test"
    assert stack_roots.resolve_folders(None, {}, old) == []
    named = {
        "test": "",
        "folders": {
            "backend/": {"test": "pytest -q", "install": ""},
            "frontend": {"stack": "node"},
        },
        "every_delivery": [
            "python3 scripts/check.py",
            {"command": "make lint", "folder": "backend"},
        ],
    }
    checked = stack_roots.validate_overrides(named)
    assert set(checked["folders"]) == {"backend", "frontend"}
    assert checked["every_delivery"] == [
        {"command": "python3 scripts/check.py"},
        {"command": "make lint", "folder": "backend"},
    ]
    # The root keeps its own fields; the extension never reaches it.
    assert stack_profiles.resolve(None, checked)["test"] is None
    detected = stack_roots.detect_folders(str(_monorepo(tmp_path)))
    folders = {
        f["folder"]: f for f in stack_roots.resolve_folders(None, detected, checked)
    }
    assert (
        folders["backend"]["test"] == "pytest -q"
        and folders["backend"]["install"] is None
    )
    assert folders["backend"]["source"]["test"] == "company"
    assert folders["frontend"]["test"] == "npm test --silent"
    assert folders["services/api"]["source"]["test"] == "detected"
    for bad, reason in (
        ({"folders": {"../up": {}}}, "not a folder"),
        ({"folders": {"a;rm": {}}}, "not a folder"),
        ({"folders": {".git": {}}}, "not a folder"),
        ({"folders": {"backend": {"start": "x"}}}, "unknown field"),
        ({"every_delivery": "make"}, "list"),
        ({"every_delivery": [{"command": "a\nb"}]}, "one line"),
    ):
        with pytest.raises(ValueError, match=reason):
            stack_roots.validate_overrides(bad)


@pytest.mark.usefixtures("enforcement")
def test_a_manager_stores_folders_and_every_delivery_commands(
    sqlite_session, test_client
):
    company = _company(sqlite_session)
    manager = _member(sqlite_session, company.id, "Manager")
    url = f"/v1/companies/{company.id}/stack_profile"
    profile = {
        "test": "",
        "folders": {"backend": {"test": "pytest"}},
        "every_delivery": ["make check"],
    }
    saved = test_client.put(url, json={"profile": profile}, headers=manager)
    assert saved.status_code == 200, saved.text
    body = saved.json()
    assert body["overrides"]["folders"] == {"backend": {"test": "pytest"}}
    assert [f["folder"] for f in body["folders_without_detection"]] == ["backend"]
    assert body["every_delivery"] == [{"command": "make check"}]
    bad = test_client.put(
        url, json={"profile": {"folders": {"/etc": {}}}}, headers=manager
    )
    assert bad.status_code == 422 and "not a folder" in bad.text
    sqlite_session.expire_all()
    assert sqlite_session.get(Company, company.id).stack_profile["every_delivery"]


# --- what a delivery runs ------------------------------------------------------


def _three(tmp_path, root_test=None):
    _files(tmp_path, {"a/x.txt": "", "b/x.txt": "", "c/x.txt": ""})
    folders = {name: {"test": _ok(f"{name}.ran")} for name in ("a", "b", "c")}
    overrides = {"folders": folders, "test": root_test if root_test is not None else ""}
    return overrides


def test_a_delivery_runs_only_the_suites_of_the_folders_the_daemon_saw_it_write(
    tmp_path,
):
    overrides = _three(tmp_path)
    ctx, daemon, evidence = _ctx(
        tmp_path, overrides, writes=["b/app.py", "b/deep/m.py"]
    )
    asyncio.run(_deliver(ctx))
    assert _executed(daemon) == [f"cd b && {_ok('b.ran')}"]
    record = evidence["command_runs"][0]
    assert record["folder"] == "b" and record["exit_code"] == 0
    output = "ran b.ran\n"
    assert record["output_sha256"] == hashlib.sha256(output.encode()).hexdigest()
    assert evidence["folders_run"]["folders"] == ["b"]
    # No root suite: the folders' suites are the test run.
    assert evidence["test_run"]["summary_of"] == "command_runs"
    assert evidence["test_run"]["exit_code"] == 0
    assert command_runs.check(evidence)["outcome"] == "pass"
    assert not (tmp_path / "a" / "a.ran").exists()


def test_a_file_outside_the_folders_runs_every_suite_or_the_root_s(tmp_path):
    overrides = _three(tmp_path)
    ctx, daemon, _evidence = _ctx(tmp_path, overrides, writes=["docs/notes.md"])
    asyncio.run(_deliver(ctx))
    assert [c.split(" && ")[0] for c in _executed(daemon)] == ["cd a", "cd b", "cd c"]
    with_root = _three(tmp_path / "r", root_test=_ok("root.ran"))
    ctx, daemon, evidence = _ctx(tmp_path / "r", with_root, writes=["docs/notes.md"])
    asyncio.run(_deliver(ctx))
    assert _executed(daemon) == [_ok("root.ran")]
    assert "command_runs" not in evidence and "summary_of" not in evidence["test_run"]


def test_every_delivery_commands_run_whatever_the_delivery_touched(tmp_path):
    overrides = _three(tmp_path)
    overrides["every_delivery"] = [
        _ok("check.ran"),
        {"command": _ok("lint.ran"), "folder": "c"},
    ]
    ctx, daemon, evidence = _ctx(tmp_path, overrides, writes=["a/m.py"])
    asyncio.run(_deliver(ctx))
    assert _executed(daemon)[1:] == [_ok("check.ran"), f"cd c && {_ok('lint.ran')}"]
    kinds = [(r["kind"], r["folder"]) for r in evidence["command_runs"]]
    assert kinds == [("suite", "a"), ("every_delivery", ""), ("every_delivery", "c")]
    assert (tmp_path / "check.ran").exists() and (tmp_path / "c" / "lint.ran").exists()
    runs = command_runs.check(evidence)["runs"]
    assert all(len(r["output_sha256"]) == 64 for r in runs)


def test_one_failing_folder_refutes_the_delivery_and_sends_it_back(tmp_path):
    overrides = _three(tmp_path)
    overrides["folders"]["b"]["test"] = (
        f"{PY} -c \"import sys; print('1 failed'); sys.exit(1)\""
    )
    ctx, _daemon, evidence = _ctx(tmp_path, overrides, writes=["a/m.py", "b/m.py"])
    asyncio.run(_deliver(ctx))
    check = command_runs.check(evidence)
    assert check["outcome"] == "refuted" and "in b/ exited 1" in check["observation"]
    assert evidence["test_run"]["exit_code"] == 1
    problem = delivery_gate._GATE_PROBLEMS[command_runs.CHECK_NAME]
    assert problem in delivery_gate.CODE_PROBLEMS
    assert delivery_checks._earlier_failure(evidence) == "the tests failed"
    # The integration reads the same runs.
    assert (
        integration.status_of(
            None, {"exit_code": 0, "command_runs": evidence["command_runs"]}
        )
        == "tests_failed"
    )


def test_a_missing_runtime_leaves_the_delivery_unverifiable(tmp_path):
    overrides = _three(tmp_path)
    overrides["folders"]["a"]["test"] = "fabrique-no-such-runner --run"
    overrides["every_delivery"] = [f"{PY} -m fabrique_no_such_module"]
    ctx, _daemon, evidence = _ctx(tmp_path, overrides, writes=["a/m.py"])
    asyncio.run(_deliver(ctx))
    outcomes = [
        command_runs.outcome_of(r, evidence)[0] for r in evidence["command_runs"]
    ]
    assert outcomes == ["unverifiable", "unverifiable"]
    assert command_runs.check(evidence)["outcome"] == "unverifiable"
    assert evidence["test_run"]["exit_code"] is None
    assert "could not run" in evidence["test_run"]["reason"]
    # A test that fails on an import is a failure, not a missing runtime.
    broken = {
        "command": "pytest",
        "exit_code": 1,
        "output_tail": "ModuleNotFoundError: No module named 'app'",
    }
    assert command_runs.outcome_of(broken)[0] == "refuted"


def test_only_acceptance_tests_of_criteria_not_built_yet_are_excused(tmp_path):
    overrides = _three(tmp_path)
    failing = (
        "print('tests/test_req.py::test_req_2_1_holds FAILED'); import sys; sys.exit(1)"
    )
    overrides["folders"]["a"]["test"] = f'{PY} -c "{failing}"'
    acceptance = {"all": ["REQ-2.1"], "excused": ["REQ-2.1"]}
    ctx, _daemon, evidence = _ctx(
        tmp_path, overrides, writes=["a/m.py"], acceptance=acceptance
    )
    asyncio.run(_deliver(ctx))
    assert command_runs.check(evidence)["outcome"] == "pass"


def test_a_folder_manifest_is_installed_by_that_folder_s_install(
    sqlite_session, tmp_path, monkeypatch
):
    monkeypatch.setattr(settings, "DELIVERY_INSTALL_ENABLED", True)
    company = _company(sqlite_session)
    _files(tmp_path, {"api/requirements.txt": "flask\n"})
    overrides = {"folders": {"api": {"install": _ok("installed"), "test": ""}}}
    evidence = {
        "workspace_root": str(tmp_path),
        "workspace_writes": [_write("api/requirements.txt", "h1")],
    }
    ctx = project_profile.Context(
        _Daemon(), {"company_id": company.id}, evidence, sqlite_session.get_bind()
    )
    ctx._company = {"id": company.id, "overrides": overrides, "installs": {}}
    asyncio.run(dependency_install.install_if_needed(ctx))
    record = evidence["command_runs"][0]
    assert (
        record["kind"] == "install"
        and record["folder"] == "api"
        and record["exit_code"] == 0
    )
    assert "dependency_install" not in evidence
    assert (tmp_path / "api" / "installed").exists()
    sqlite_session.expire_all()
    installed = sqlite_session.get(Company, company.id).dependency_installs
    assert installed == {str(tmp_path): {"api/requirements.txt": "h1"}}


def test_a_single_root_project_records_nothing_new(tmp_path):
    _files(tmp_path, {"go.mod": "module x\n", "web/package.json": "{}"})
    ctx, daemon, evidence = _ctx(
        tmp_path, {"test": _ok("root.ran")}, writes=["web/a.js"]
    )
    asyncio.run(_deliver(ctx))
    assert _executed(daemon) == [_ok("root.ran")]
    assert "command_runs" not in evidence and "folders_run" not in evidence
    assert command_runs.check(evidence) is None


# --- the self-hosting harness ---------------------------------------------------


def _harness():
    path = REPO / "scripts" / "self_host" / "self_host.py"
    spec = importlib.util.spec_from_file_location("self_host_profile", path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_the_harness_sets_la_fabrique_s_folders_and_every_delivery_checks():
    harness = _harness()
    profile = stack_roots.validate_overrides(
        harness.company_profile("folders", "unused")
    )
    folders = {f["folder"]: f for f in stack_roots.resolve_folders(None, {}, profile)}
    assert set(folders) >= {"backend", "frontend", "llm-proxy"}
    assert (
        "pytest" in folders["backend"]["test"]
        and "test:run" in folders["frontend"]["test"]
    )
    assert "pytest" in folders["llm-proxy"]["test"]
    assert profile["test"] == ""
    checks = [c["command"] for c in profile["every_delivery"]]
    assert checks == [
        "python3 scripts/check_architecture.py",
        "python3 scripts/check_frontend_architecture.py",
        "python3 scripts/check_file_sizes.py",
    ]
    assert all((REPO / c.split()[1]).is_file() for c in checks)
    assert (
        stack_roots.folder_of("backend/app/main.py", list(folders.values()))
        == "backend"
    )
    # The e2e's one root command, as before.
    old = harness.company_profile("sprint-tests", "pytest tests")
    assert old == {
        "test": "pytest tests",
        "install": "",
        "lint": "",
        "start": "",
        "e2e": "",
    }
