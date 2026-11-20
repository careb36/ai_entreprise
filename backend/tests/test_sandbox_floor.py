"""
The daemon's sandbox floor reaches every command it runs (issue #525).

``FABRIQUE_SANDBOX_REQUIRED`` on the daemon's machine used to apply only to a
``workspace.execute`` that carried a ``sandbox``; a request without one (the
console assistants' consultations) ran on the host. Now:

- a request without ``sandbox`` is read as ``{"mode": "host"}`` and the floor
  raises it (``forced_by_daemon``), through the NATS handler as well;
- when the floor's runtime is unavailable the command is refused, never run
  on the host;
- without the floor nothing changes: the host, and no ``sandbox`` in the
  reply of a request that did not ask;
- ``assistant`` is a purpose the daemon keeps;
- the other daemon paths that run a command (the application check) apply
  the floor too, and nothing else in the daemon reaches the host shell.

Run with:
    cd backend && PYTHONPATH=.:app python3 -m pytest tests/test_sandbox_floor.py -q
"""

from __future__ import annotations

import asyncio
import re
from pathlib import Path

import app_run
import pytest
import sandbox as daemon_sandbox  # the daemon's module, on sys.path via conftest
import shell_exec
from test_daemon_protocol import _NC, _Msg
from test_sandbox import fake_runtime  # noqa: F401 - the fixture

DAEMON = Path(__file__).resolve().parents[2] / "vscode-daemon"
MISSING_RUNTIME = "fabrique-no-such-runtime"


def test_a_request_without_a_sandbox_is_contained_by_the_floor(
    fake_runtime,  # noqa: F811 - the fixture imported above.
    tmp_path,
    monkeypatch,
):
    monkeypatch.setenv("FABRIQUE_SANDBOX_REQUIRED", "container")
    result = shell_exec.execute_shell_command("echo advice", str(tmp_path))
    assert result["success"] and result["stdout"] == "advice\n"
    args = fake_runtime.read_text().splitlines()
    assert args[:2] == ["run", "--rm"] and "none" in args  # network off
    assert result["sandbox"]["mode"] == "container"
    assert result["sandbox"]["forced_by_daemon"] is True
    assert result["sandbox"]["isolated"] is True


@pytest.mark.parametrize("floor", ["container", "bwrap"])
def test_the_floor_s_runtime_missing_refuses_never_the_host(
    floor, tmp_path, monkeypatch
):
    monkeypatch.setenv("FABRIQUE_SANDBOX_REQUIRED", floor)
    monkeypatch.setenv("FABRIQUE_SANDBOX_RUNTIME", MISSING_RUNTIME)
    monkeypatch.setenv("PATH", str(tmp_path / "empty"))  # no bwrap either
    marker = tmp_path / "ran-on-the-host"
    for sandbox in (None, {"mode": "host"}, {"mode": "host", "purpose": "assistant"}):
        result = shell_exec.execute_shell_command(
            f"touch {marker}", str(tmp_path), sandbox
        )
        assert result["success"] is False
        assert result["error"].startswith("sandbox unavailable: ")
        assert "exit_code" not in result and "stdout" not in result
        assert not marker.exists()


def test_without_the_floor_nothing_changes(tmp_path, monkeypatch):
    monkeypatch.delenv("FABRIQUE_SANDBOX_REQUIRED", raising=False)
    plain = shell_exec.execute_shell_command("echo hi", str(tmp_path))
    # The daemon's clock is the one addition (issue #766).
    assert plain.pop("duration_ms") >= 0
    assert plain == {
        "success": True,
        "stdout": "hi\n",
        "stderr": "",
        "exit_code": 0,
    }
    asked = shell_exec.execute_shell_command(
        "echo hi", str(tmp_path), {"mode": "host", "purpose": "assistant"}
    )
    assert asked["stdout"] == "hi\n" and asked["sandbox"]["mode"] == "host"
    assert asked["sandbox"]["forced_by_daemon"] is False
    # A floor the daemon does not know is no floor (as before).
    monkeypatch.setenv("FABRIQUE_SANDBOX_REQUIRED", "host")
    assert "sandbox" not in shell_exec.execute_shell_command("echo hi", str(tmp_path))


def test_assistant_is_a_purpose_the_daemon_keeps():
    assert daemon_sandbox.normalize({"purpose": "assistant"})["purpose"] == "assistant"
    assert daemon_sandbox.normalize({"purpose": "anything"})["purpose"] == "test"
    assert daemon_sandbox.normalize(None)["mode"] == "host"


def test_the_nats_handler_applies_the_floor_to_a_request_without_a_sandbox(
    fake_runtime,  # noqa: F811 - the fixture imported above.
    tmp_path,
    monkeypatch,
):
    import daemon

    monkeypatch.setenv("FABRIQUE_SANDBOX_REQUIRED", "container")
    nc = _NC()
    handlers = dict(daemon.subscriptions(nc, lambda: True))
    msg = _Msg(
        "workspace.execute",
        {"workspace_root": str(tmp_path), "command": "echo hi", "sprint_id": 7},
    )
    asyncio.run(handlers["workspace.execute"](msg))
    reply = [body for subject, body in nc.published if subject == msg.reply]
    assert reply and reply[0]["stdout"] == "hi\n"
    assert reply[0]["sandbox"]["forced_by_daemon"] is True
    assert fake_runtime.exists()


def test_the_application_check_applies_the_floor_without_a_sandbox(
    tmp_path, monkeypatch
):
    monkeypatch.setenv("FABRIQUE_SANDBOX_REQUIRED", "container")
    monkeypatch.setenv("FABRIQUE_SANDBOX_RUNTIME", MISSING_RUNTIME)
    marker = tmp_path / "started-on-the-host"
    result = app_run.check_application(
        {
            "workspace_root": str(tmp_path),
            "start_command": f"touch {marker}",
            "port": 59321,
        }
    )
    assert result["error"].startswith("sandbox unavailable: ")
    assert not marker.exists()


def test_nothing_else_in_the_daemon_reaches_the_host_shell():
    """The host shell is reached through ``build_shell_command``: only
    ``shell_exec`` (after ``normalize``) and ``app_run`` (whose host path runs
    after ``normalize`` too) build it, and only ``execute_shell_command``
    calls ``run_on_host``. A new caller must come through the floor."""
    builders, host_runs = set(), set()
    for path in DAEMON.glob("*.py"):
        text = path.read_text(encoding="utf-8")
        if re.search(r"\bbuild_shell_command\(", text):
            builders.add(path.name)
        if re.search(r"\brun_on_host\(", text):
            host_runs.add(path.name)
    assert builders == {"shell_exec.py", "app_run.py"}
    assert host_runs == {"shell_exec.py"}
    source = (DAEMON / "shell_exec.py").read_text(encoding="utf-8")
    body = source.split("def execute_shell_command(", 1)[1].split("\ndef ", 1)[0]
    assert body.index("normalize(") < body.index("run_on_host(")
    app = (DAEMON / "app_run.py").read_text(encoding="utf-8")
    check = app.split("def check_application(", 1)[1]
    assert check.index("sandbox.normalize(") < check.index("_run_host(")
    # The browser probe runs node in the project's folder on the host only
    # from the application check's host path, which is past ``normalize``.
    on_host = {
        p.name
        for p in DAEMON.glob("*.py")
        if re.search(r"\bbrowser_probe\.on_host\(", p.read_text(encoding="utf-8"))
    }
    assert on_host <= {"app_run.py"}
    host_path = app.split("def _run_host(", 1)[1].split("\ndef ", 1)[0]
    assert "browser_probe.on_host(" in host_path or not on_host
