"""Sandboxed execution (``vscode-daemon/sandbox.py``, ``services/sandbox.py``):
the daemon runs agent-written code in the mode the backend asks for and
reports what it applied; it never falls back to the host on its own; the
person running the daemon can refuse the host; the backend sends the
company's mode with the delivery test run and the Developer's test runs,
keeps the daemon's report on the evidence, and the Verifier records it
(``sandbox``), the host as a finding and never a refutation."""

from __future__ import annotations

import os
import stat
import sys

import pytest
import sandbox as daemon_sandbox  # the daemon's module, on sys.path via conftest
import shell_exec
from config import settings
from factories import company as _company
from factories import enforcement  # noqa: F401 - the fixture, by name
from factories import member_headers as _member
from models.company import Company
from services.evidence import verification
from services.loop import delivery_checks
from services.tools import developer_tools
from services.workspace import sandbox, workspace_daemon

FAKE_IMAGE_ID = "a" * 64
FAKE_DIGEST = "b" * 64


@pytest.fixture
def fake_runtime(tmp_path, monkeypatch):
    """A ``docker`` on the PATH that records its arguments and runs the
    command it was given on the host, in the mounted folder (no real
    container, the same argument shape). ``image inspect`` answers with
    ``FAKE_IMAGE_ID`` and a registry digest, unlogged."""
    log = tmp_path / "docker.log"
    script = tmp_path / "bin" / "docker"
    script.parent.mkdir()
    script.write_text(
        "#!/bin/sh\n"
        'if [ "$1" = "image" ]; then\n'
        f'  echo \'[{{"Id": "sha256:{FAKE_IMAGE_ID}", '
        f'"RepoDigests": ["python@sha256:{FAKE_DIGEST}"]}}]\'\n'
        "  exit 0\n"
        "fi\n"
        f'printf "%s\\n" "$@" > {log}\n'
        'if [ "$1" = "kill" ]; then exit 0; fi\n'
        # The workdir is the -v source; the command is everything after "-c".
        'src=""; prev=""; for a in "$@"; do\n'
        '  if [ "$prev" = "-v" ]; then src="${a%%:/workspace:rw}"; fi\n'
        '  prev="$a"\n'
        "done\n"
        'while [ "$1" != "-c" ]; do shift; done; shift\n'
        'cd "$src" && exec /bin/sh -c "$1"\n'
    )
    script.chmod(script.stat().st_mode | stat.S_IEXEC)
    monkeypatch.setenv("PATH", f"{script.parent}{os.pathsep}{os.environ['PATH']}")
    monkeypatch.delenv("FABRIQUE_SANDBOX_RUNTIME", raising=False)
    monkeypatch.delenv("FABRIQUE_SANDBOX_REQUIRED", raising=False)
    return log


def test_container_mode_runs_isolated_and_reports_what_it_applied(
    fake_runtime, tmp_path, monkeypatch
):
    work = tmp_path / "work"
    work.mkdir()
    (work / "marker.txt").write_text("in the worktree\n")
    monkeypatch.setenv("ALLOWED_TOKEN", "t0k3n")
    monkeypatch.setenv("SECRET_NOT_LISTED", "nope")
    result = shell_exec.execute_shell_command(
        "cat marker.txt",
        str(work),
        {
            "mode": "container",
            "image": "python:3.12-slim",
            "env": ["ALLOWED_TOKEN", "bad name;"],
        },
    )
    assert result["success"] and result["stdout"] == "in the worktree\n"
    args = fake_runtime.read_text().splitlines()
    joined = " ".join(args)
    assert args[:2] == ["run", "--rm"]
    assert "--network none" in joined and "--cap-drop ALL" in joined
    assert "--security-opt no-new-privileges" in joined
    assert (
        "--memory 2048m" in joined
        and "--pids-limit 512" in joined
        and "--cpus 2" in joined
    )
    assert f"{work}:/workspace:rw" in joined and "-w /workspace" in joined
    assert "ALLOWED_TOKEN=t0k3n" in args and not any(
        "SECRET_NOT_LISTED" in a for a in args
    )
    assert not any(a.startswith("PATH=/") and "nvm" in a for a in args)
    applied = result["sandbox"]
    assert applied == {
        "mode": "container",
        "forced_by_daemon": False,
        "isolated": True,
        "network": "off",
        "env": ["ALLOWED_TOKEN"],
        "workdir": "/workspace",
        "runtime": "docker",
        "image": "python:3.12-slim",
        "cpus": 2.0,
        "memory_mb": 2048,
        "pids_limit": 512,
        "capabilities": "all dropped",
        "no_new_privileges": True,
        # The daemon's observation, and the container run from that ID.
        "image_identity": {
            "id": f"sha256:{FAKE_IMAGE_ID}",
            "digest": f"python@sha256:{FAKE_DIGEST}",
            "observed_by": "daemon",
            "observed": "before_run",
        },
    }
    assert args[args.index("/bin/sh") - 1] == f"sha256:{FAKE_IMAGE_ID}"


def test_a_mode_that_cannot_run_is_an_error_never_a_quiet_host_run(
    tmp_path, monkeypatch
):
    monkeypatch.setenv("PATH", str(tmp_path))  # no docker, no podman, no bwrap
    monkeypatch.delenv("FABRIQUE_SANDBOX_RUNTIME", raising=False)
    result = shell_exec.execute_shell_command(
        "echo ran", str(tmp_path), {"mode": "container"}
    )
    assert result == {
        "success": False,
        "error": "sandbox unavailable: container mode needs Docker or Podman on the daemon's machine; none was found",
    }
    result = shell_exec.execute_shell_command(
        "echo ran", str(tmp_path), {"mode": "bwrap"}
    )
    assert result["error"].startswith(
        "sandbox unavailable: bwrap mode needs bubblewrap"
    )
    result = shell_exec.execute_shell_command(
        "echo ran", str(tmp_path), {"mode": "chroot"}
    )
    assert result["error"] == "sandbox unavailable: unknown sandbox mode 'chroot'"


def test_host_mode_says_it_is_unisolated_and_the_daemon_can_refuse_it(
    fake_runtime, tmp_path, monkeypatch
):
    result = shell_exec.execute_shell_command(
        "echo hi", str(tmp_path), {"mode": "host"}
    )
    assert result["stdout"].strip() == "hi"
    assert result["sandbox"] == {
        "mode": "host",
        "forced_by_daemon": False,
        "isolated": False,
        "network": "host",
        "env": "inherited",
        "image_identity": {
            "id": None,
            "digest": None,
            "reason": "host mode runs no image: the daemon's own machine ran it",
        },
    }
    # The person running the daemon refuses the host for everything.
    monkeypatch.setenv("FABRIQUE_SANDBOX_REQUIRED", "container")
    result = shell_exec.execute_shell_command(
        "echo hi", str(tmp_path), {"mode": "host"}
    )
    assert (
        result["sandbox"]["mode"] == "container"
        and result["sandbox"]["forced_by_daemon"] is True
    )
    assert fake_runtime.exists()
    # A command without a sandbox request is held to the floor too (#525).
    fake_runtime.unlink()
    result = shell_exec.execute_shell_command("echo hi", str(tmp_path))
    assert result["stdout"].strip() == "hi" and fake_runtime.exists()
    assert result["sandbox"]["mode"] == "container"
    assert result["sandbox"]["forced_by_daemon"] is True
    # Without the floor, it runs on the host and its reply is unchanged.
    monkeypatch.delenv("FABRIQUE_SANDBOX_REQUIRED")
    fake_runtime.unlink()
    assert "sandbox" not in shell_exec.execute_shell_command("echo hi", str(tmp_path))
    assert not fake_runtime.exists()


def test_a_sandboxed_command_that_hangs_is_killed_and_says_so(
    fake_runtime, tmp_path, monkeypatch
):
    result = daemon_sandbox.run_sandboxed(
        "sleep 5", str(tmp_path), {"mode": "container"}, 1
    )
    assert result["timed_out"] is True and result["timeout_seconds"] == 1
    assert result["sandbox"]["mode"] == "container"
    assert fake_runtime.read_text().splitlines()[0] == "kill"


def test_limits_are_bounded_and_network_opens_only_when_asked():
    box = daemon_sandbox.normalize(
        {
            "mode": "container",
            "cpus": 999,
            "memory_mb": 1,
            "pids_limit": "x",
            "network": True,
        }
    )
    assert box["cpus"] == 64 and box["memory_mb"] == 64 and box["pids_limit"] == 512
    argv = daemon_sandbox.container_command("docker", box, "true", "/w", "n")
    assert argv[argv.index("--network") + 1] == "bridge"


def test_the_backend_asks_for_the_companys_mode(monkeypatch):
    monkeypatch.setattr(settings, "DELIVERY_SANDBOX", "container")
    assert sandbox.mode_for(None) == "container"
    company = Company(
        company_name="x", delivery_sandbox="bwrap", sandbox_env=["NPM_TOKEN"]
    )
    request = sandbox.request_for(company)
    assert request["mode"] == "bwrap" and request["env"] == ["NPM_TOKEN"]
    assert (
        request["network"] is False
        and request["image"] == settings.DELIVERY_SANDBOX_IMAGE
    )
    monkeypatch.setattr(settings, "DELIVERY_SANDBOX", "nonsense")
    assert sandbox.mode_for(Company(company_name="y")) == "host"


def test_the_evidence_keeps_the_daemons_report_and_the_verifier_records_it():
    evidence: dict = {}
    workspace_daemon.record_test_run(
        evidence,
        "python -m pytest -q",
        {
            "exit_code": 0,
            "stdout": "1 passed",
            "sandbox": {
                "mode": "container",
                "isolated": True,
                "network": "off",
                "runtime": "docker",
                "image": "python:3.12-slim",
                "cpus": 2.0,
                "memory_mb": 2048,
                "pids_limit": 512,
                "capabilities": "all dropped",
                "env": [],
            },
        },
    )
    assert evidence["test_run"]["sandbox"]["mode"] == "container"
    check = sandbox.check_sandbox(evidence)
    assert (
        check["name"] == "sandbox"
        and check["outcome"] == "pass"
        and check["isolated"] is True
    )
    # An older daemon reported no image identity: said so, never a success.
    assert check["observation"] == (
        "ran in container; docker image python:3.12-slim (image identity "
        "unknown: the daemon reported no image identity); network off; 2 CPUs; "
        "2048 MB; 512 processes; capabilities all dropped; no environment passed"
    )
    assert check["image_identity"]["id"] is None
    host = sandbox.check_sandbox(
        {
            "test_run": {
                "command": "pytest",
                "sandbox": {"mode": "host", "isolated": False},
            }
        }
    )
    assert (
        host["outcome"] == "pass"
        and host["finding"] is True
        and "unisolated" in host["observation"]
    )
    # A run without the daemon's report: where it ran is unknown, not "host".
    old = sandbox.check_sandbox({"test_run": {"command": "pytest", "exit_code": 0}})
    assert old["finding"] is True and old["isolated"] is None
    assert "unknown" in old["observation"] and "host" not in old["observation"]
    # Nothing ran (no reply, runtime unavailable): nothing to report.
    assert (
        sandbox.check_sandbox(
            {"test_run": {"command": "pytest", "reason": "sandbox unavailable"}}
        )
        is None
    )
    assert sandbox.check_sandbox({"test_run": {"command": None}}) is None
    assert sandbox.check_sandbox({}) is None
    # The Verifier carries it beside tests_run, never as the verdict.
    assert (
        verification.aggregate_verdict([check, host]) == verification.VERDICT_VERIFIED
    )


def test_the_developers_test_runs_go_through_the_sandbox():
    sent = []

    def requester(subject, payload, timeout):
        sent.append(payload)
        return {
            "exit_code": 0,
            "stdout": "ok",
            "sandbox": {"mode": "container", "isolated": True},
        }

    box = developer_tools.WorkspaceToolbox(
        requester,
        "/w",
        test_command="python -m pytest -q",
        sandbox={"mode": "container"},
    )
    observation, text = box.call(
        {"function": {"name": "workspace__run_tests", "arguments": "{}"}}, 1
    )
    assert sent[0]["sandbox"] == {"mode": "container"}
    assert observation["sandbox"]["mode"] == "container" and text.startswith(
        "exit status 0"
    )


@pytest.mark.usefixtures("enforcement")
def test_a_manager_sets_the_companys_sandbox_and_a_viewer_reads_it(
    sqlite_session, test_client
):
    company, other = _company(sqlite_session), _company(sqlite_session)
    manager = _member(sqlite_session, company.id, "Manager")
    viewer = _member(sqlite_session, company.id, "Viewer")
    outsider = _member(sqlite_session, other.id, "Manager")
    url = f"/v1/companies/{company.id}/sandbox"
    read = test_client.get(url, headers=viewer)
    assert read.status_code == 200, read.text
    assert read.json()["mode"] is None and read.json()["effective_mode"] == "host"
    assert test_client.get(url, headers=outsider).status_code == 403
    assert (
        test_client.put(url, json={"mode": "container"}, headers=viewer).status_code
        == 403
    )
    bad = test_client.put(url, json={"mode": "chroot"}, headers=manager)
    assert bad.status_code == 422 and "not a sandbox mode" in bad.text
    bad = test_client.put(
        url, json={"mode": "container", "env": ["OK", "1BAD"]}, headers=manager
    )
    assert bad.status_code == 422 and "not a variable name" in bad.text
    saved = test_client.put(
        url,
        json={"mode": "Container", "env": ["NPM_TOKEN", "NPM_TOKEN"]},
        headers=manager,
    )
    assert saved.status_code == 200, saved.text
    body = saved.json()
    assert body["mode"] == "container" and body["effective_mode"] == "container"
    assert body["env"] == ["NPM_TOKEN"] and body["limits"]["network"] is False
    sqlite_session.expire_all()
    assert sqlite_session.get(Company, company.id).delivery_sandbox == "container"
    cleared = test_client.put(url, json={"mode": ""}, headers=manager).json()
    assert cleared["mode"] is None and cleared["effective_mode"] == "host"


def test_the_delivery_test_run_sends_the_companys_sandbox(sqlite_session, monkeypatch):
    company = _company(sqlite_session)
    company.delivery_sandbox = "container"
    sqlite_session.add(company)
    sqlite_session.commit()
    monkeypatch.setattr(
        settings, "DELIVERY_TEST_COMMAND", f"{sys.executable} -m pytest -q"
    )

    class _Nats:
        payload = None

        async def request(self, subject, payload, timeout=5.0):
            _Nats.payload = payload
            return {
                "exit_code": 0,
                "stdout": "ok",
                "sandbox": {"mode": "container", "isolated": True},
            }

    import asyncio

    monkeypatch.setattr(
        sandbox,
        "request_for_sprint",
        lambda sprint, engine=None: sandbox.request_for(company),
    )
    evidence = {"workspace_root": "/w"}
    asyncio.run(
        delivery_checks.run_delivery_tests(
            _Nats(), {"id": 1, "company_id": company.id}, evidence
        )
    )
    assert _Nats.payload["sandbox"]["mode"] == "container"
    assert evidence["test_run"]["sandbox"] == {"mode": "container", "isolated": True}


def test_a_policy_that_cannot_be_read_runs_nothing(monkeypatch):
    class _Broken:
        def connect(self, *a, **k):
            raise RuntimeError("database down")

    monkeypatch.setattr(settings, "DELIVERY_SANDBOX", "host")
    with pytest.raises(sandbox.PolicyUnavailable, match="could not be read"):
        sandbox.request_for_company_id(1, _Broken())
    with pytest.raises(sandbox.PolicyUnavailable, match="could not be read"):
        sandbox.request_for_sprint({"id": 1}, _Broken())
    # No company at all is the deployment's default, as before.
    assert sandbox.request_for_company_id(None)["mode"] == "host"

    def broken(sprint, engine=None):
        raise sandbox.PolicyUnavailable("the sprint's company could not be read (X)")

    monkeypatch.setattr(sandbox, "request_for_sprint", broken)
    monkeypatch.setattr(settings, "DELIVERY_TEST_COMMAND", "pytest -q")

    subjects: list[str] = []

    class _Nats:
        async def request(self, subject, payload, timeout=5.0):
            subjects.append(subject)
            return {"exit_code": 0}

    import asyncio

    evidence = {"workspace_root": "/w"}
    asyncio.run(delivery_checks.run_delivery_tests(_Nats(), {"id": 1}, evidence))
    assert "workspace.execute" not in subjects
    assert evidence["test_run"]["reason"].startswith("the sprint's company")
    assert evidence["test_run"].get("exit_code") is None

    sent = []
    box = developer_tools.WorkspaceToolbox(
        lambda subject, payload, timeout: sent.append(payload) or {"exit_code": 0},
        "/w",
        test_command="pytest -q",
        sandbox_error="the company's sandbox could not be read (X)",
    )
    _, text = box.call(
        {"function": {"name": "workspace__run_tests", "arguments": "{}"}}, 1
    )
    assert sent == [] and "could not be read" in text


def test_sandboxed_output_is_bounded_and_decoded(fake_runtime, tmp_path, monkeypatch):
    monkeypatch.setattr(daemon_sandbox, "MAX_OUTPUT_BYTES", 1000)
    result = daemon_sandbox.run_sandboxed(
        "yes x | head -c 50000; printf END", str(tmp_path), {"mode": "container"}, 30
    )
    assert result["exit_code"] == 0 and result["output_truncated"] is True
    assert len(result["stdout"]) == 1000 and result["stdout"].endswith("END")
    # Output printed before a timeout comes back as text, ready for JSON.
    hung = daemon_sandbox.run_sandboxed(
        "printf before; sleep 5", str(tmp_path), {"mode": "container"}, 1
    )
    assert hung["timed_out"] is True and hung["stdout"] == "before"
    import json

    json.dumps(hung)


def test_a_container_that_could_not_start_is_unavailable_not_a_test_result(
    fake_runtime, tmp_path
):
    result = shell_exec.execute_shell_command(
        "echo 'Cannot connect to the Docker daemon' >&2; exit 125",
        str(tmp_path),
        {"mode": "container"},
    )
    assert "exit_code" not in result and "sandbox" not in result
    assert result["success"] is False
    assert result["error"].startswith("sandbox unavailable: docker could not start")
    assert "Cannot connect" in result["error"]


def test_rootless_podman_keeps_the_persons_uid(monkeypatch):
    box = daemon_sandbox.normalize({"mode": "container"})
    monkeypatch.setattr(daemon_sandbox.os, "getuid", lambda: 1000)
    podman = daemon_sandbox.container_command("/usr/bin/podman", box, "true", "/w", "n")
    assert "--userns" in podman and podman[podman.index("--userns") + 1] == "keep-id"
    assert "--user" not in podman
    docker = daemon_sandbox.container_command("/usr/bin/docker", box, "true", "/w", "n")
    assert docker[docker.index("--user") + 1].startswith("1000:")
    monkeypatch.setattr(daemon_sandbox.os, "getuid", lambda: 0)
    rootful = daemon_sandbox.container_command("podman", box, "true", "/w", "n")
    assert "--userns" not in rootful and "--user" in rootful
