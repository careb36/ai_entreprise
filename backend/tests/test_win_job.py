"""Windows: the application's whole process tree stops with a Job Object (issue #396)."""

from __future__ import annotations

import os
import socket
import subprocess
import sys

import app_run  # the daemon's modules, on sys.path via conftest
import pytest
import win_job


def test_outside_windows_jobs_are_a_no_op():
    if os.name == "nt":
        pytest.skip("POSIX behaviour")
    assert win_job.creationflags() == 0
    process = subprocess.Popen([sys.executable, "-c", "pass"])
    try:
        assert win_job.contain(process) is None
    finally:
        process.wait()
    assert win_job.terminate(None) is False
    assert win_job.terminate(1234) is False


class _Wrapper:
    """A wrapper that already exited, as cmd.exe does after ``start /b``."""

    pid = 4242
    returncode = 0

    def poll(self):
        return 0

    def wait(self, timeout=None):
        return 0


def test_on_windows_the_stop_terminates_the_job_and_skips_taskkill(monkeypatch):
    calls = []
    monkeypatch.setattr(app_run, "_WINDOWS", True)
    monkeypatch.setattr(app_run, "port_in_use", lambda port: False)
    monkeypatch.setattr(
        app_run.win_job, "terminate", lambda job: calls.append(("job", job)) or True
    )
    monkeypatch.setattr(
        app_run.sandbox, "kill_tree", lambda process: calls.append(("taskkill",))
    )
    assert app_run._stop_host(_Wrapper(), 8080, job=77) is True
    assert calls == [("job", 77)]


def test_without_a_job_the_stop_falls_back_to_taskkill(monkeypatch):
    calls = []
    monkeypatch.setattr(app_run, "_WINDOWS", True)
    monkeypatch.setattr(app_run, "port_in_use", lambda port: False)
    monkeypatch.setattr(app_run.win_job, "terminate", lambda job: False)
    monkeypatch.setattr(
        app_run.sandbox, "kill_tree", lambda process: calls.append(("taskkill",))
    )
    assert app_run._stop_host(_Wrapper(), 8080, job=None) is True
    assert calls == [("taskkill",)]


def test_a_port_still_answering_after_the_stop_is_reported(monkeypatch):
    monkeypatch.setattr(app_run, "_WINDOWS", True)
    monkeypatch.setattr(app_run, "port_in_use", lambda port: True)
    monkeypatch.setattr(app_run.win_job, "terminate", lambda job: True)
    assert app_run._stop_host(_Wrapper(), 8080, job=77) is False


def _free_port() -> int:
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


@pytest.mark.skipif(os.name != "nt", reason="Job Objects exist only on Windows")
def test_a_child_left_by_an_exited_wrapper_is_stopped_with_its_port(tmp_path):
    """cmd.exe starts the server in the background and exits at once."""
    (tmp_path / "index.html").write_text("hello")
    port = _free_port()
    start = f'start "" /b "{sys.executable}" -m http.server %PORT% --bind 127.0.0.1'
    reply = app_run.check_application(
        {
            "workspace_root": str(tmp_path),
            "start_command": start,
            "port": port,
            "ready_timeout_seconds": 20,
            "sandbox": {"mode": "host"},
        }
    )
    assert reply["listening"] and reply["stopped"]
    assert not app_run.port_in_use(port)
