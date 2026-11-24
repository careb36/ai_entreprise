"""A declared program in a real container (PROTOCOL.md §5.12).

Skipped when no Docker or Podman answers, or when no small image with
``/bin/sh`` is already on the machine (``--pull=never``: the tests fetch
nothing). ``FABRIQUE_TEST_PROGRAM_IMAGE`` names one to use."""

from __future__ import annotations

import os
import shutil
import subprocess
import time

import program_run
import pytest

CANDIDATES = ("alpine:3.20", "busybox:latest", "python:3.12-slim", "nats:2.15-alpine")


def _runtime() -> str | None:
    for name in ("docker", "podman"):
        path = shutil.which(name)
        if path and subprocess.run([path, "info"], capture_output=True, timeout=20, check=False).returncode == 0:
            return path
    return None


def _image(runtime: str) -> str | None:
    wanted = os.getenv("FABRIQUE_TEST_PROGRAM_IMAGE")
    for image in ([wanted] if wanted else CANDIDATES):
        probe = subprocess.run(
            [runtime, "run", "--rm", "--pull=never", "--network", "none", image, "/bin/sh", "-c", "true"],
            capture_output=True, timeout=60, check=False,
        )
        if probe.returncode == 0:
            return image
    return None


RUNTIME = _runtime()
IMAGE = _image(RUNTIME) if RUNTIME else None
pytestmark = pytest.mark.skipif(IMAGE is None, reason="no container runtime with a local image that has /bin/sh")


@pytest.fixture
def work(tmp_path, monkeypatch):
    monkeypatch.setenv("FABRIQUE_ARTIFACTS_ROOT", str(tmp_path / "artifacts"))
    monkeypatch.setenv("FABRIQUE_ARTIFACTS_MIN_FREE_MB", "0")
    monkeypatch.setenv("FABRIQUE_SANDBOX_RUNTIME", os.path.basename(RUNTIME or "docker"))
    monkeypatch.delenv("FABRIQUE_SANDBOX_REQUIRED", raising=False)
    folder = tmp_path / "work"
    folder.mkdir()
    (folder / "data.txt").write_text("committed\n")
    return folder


def _run(folder, script, timeout=60, artifacts_mb=16):
    (folder / "prog.sh").write_text(script)
    request = {"mode": "container", "image": IMAGE, "purpose": "program", "network": True,
               "workdir": "ro", "program": {"artifacts_mb": artifacts_mb}}
    return program_run.run("sh prog.sh", str(folder), request, timeout)


def test_a_socket_gets_no_route_with_the_network_off(work):
    reply = _run(work, "wget -q -T 3 -O /dev/null http://1.1.1.1/ && echo reached || echo no-route")
    assert "no-route" in reply["stdout"] and "reached" not in reply["stdout"]


def test_a_write_to_the_worktree_fails_and_one_to_artifacts_succeeds(work):
    reply = _run(work, "echo changed > /workspace/data.txt; echo rc=$?; echo kept > /artifacts/out.txt")
    assert (work / "data.txt").read_text() == "committed\n"
    assert "rc=0" not in reply["stdout"] and "read-only" in reply["stderr"].lower()
    assert [f["path"] for f in reply["program"]["artifacts"]["files"]] == ["out.txt"]


def test_the_artifacts_watch_kills_a_run_that_fills_the_disk(work):
    started = time.monotonic()
    reply = _run(work, "while true; do dd if=/dev/zero bs=262144 count=1 >> /artifacts/big 2>/dev/null; sleep 0.05; done",
                 artifacts_mb=1)
    assert time.monotonic() - started < 30
    assert reply["program"]["artifacts"]["killed_for_size"] is True and reply["success"] is False
