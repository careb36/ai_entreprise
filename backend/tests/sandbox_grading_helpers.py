"""The sandbox faked at its boundary, for ``test_sandbox_grading.py`` and
``test_sandbox_grading_trust.py`` (rsi-loop PR 12).

``fake_runtime`` puts a ``docker`` on the PATH that records its arguments and
runs the command on the host with only the variables passed with ``-e``, each
read-only ``-v`` mount a symbolic link at its container path (under
``tmp_path``). ``FAKE_DOCKER_MODE`` picks a failure: ``no_image``, ``down``
(exit 125), ``oom`` (exit 137), ``hang_kill`` (``docker kill`` never answers).
"""

from __future__ import annotations

import json
import os
import stat
import sys
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(REPO / "scripts" / "bench"))
import sandbox_grading  # scripts/bench/sandbox_grading.py, by path

IMAGE = "fabrique-grading:test"

FAKE_DOCKER = r'''#!{python}
"""A container runtime's command line, faked on the host (see the module)."""
import json, os, sys

args = sys.argv[1:]
mode = os.environ.get("FAKE_DOCKER_MODE", "")
if args[:2] == ["image", "inspect"]:
    if mode == "no_image":
        sys.stderr.write("Error: No such image\n")
        sys.exit(1)
    print(json.dumps([{{"Id": "sha256:" + "a" * 64, "RepoDigests": []}}]))
    sys.exit(0)
if args[:1] == ["kill"]:
    if mode == "hang_kill":  # a runtime that does not answer
        import time
        time.sleep(60)
    sys.exit(0)
with open(os.environ["FAKE_DOCKER_LOG"], "a") as log:
    log.write(json.dumps(args) + "\n")
if mode == "down":
    sys.stderr.write("Cannot connect to the Docker daemon\n")
    sys.exit(125)
if mode == "oom":
    sys.exit(137)
with_value = {{"--name", "--network", "--cpus", "--memory", "--memory-swap",
    "--pids-limit", "--cap-drop", "--security-opt", "--tmpfs", "--user",
    "--userns", "-v", "-w", "-e"}}
env, mounts, workdir, i = {{}}, {{}}, None, 1
while i < len(args) and args[i].startswith("-"):
    flag = args[i]
    if flag in with_value:
        value = args[i + 1]
        if flag == "-e":
            key, _, val = value.partition("=")
            env[key] = val
        elif flag == "-v":
            host, ctr = value.split(":")[:2]
            mounts[ctr] = host
        elif flag == "-w":
            workdir = value
        i += 2
    else:
        i += 1
script = args[i + 3]  # image, /bin/sh, -c, script
for ctr, host in mounts.items():
    if ctr.startswith("/workspace"):
        continue
    os.makedirs(os.path.dirname(ctr), exist_ok=True)
    if not os.path.lexists(ctr):
        os.symlink(host, ctr)
os.chdir(mounts.get(workdir, "/"))
os.execve("/bin/sh", ["/bin/sh", "-c", script], env)
'''


@pytest.fixture(name="fake_runtime")
def fake_runtime_fixture(tmp_path, monkeypatch):
    """The faked ``docker``; returns a reader of the ``run`` argument lists."""
    log = tmp_path / "docker.log"
    script = tmp_path / "bin" / "docker"
    script.parent.mkdir()
    script.write_text(FAKE_DOCKER.format(python=sys.executable))
    script.chmod(script.stat().st_mode | stat.S_IEXEC)
    monkeypatch.setenv("PATH", f"{script.parent}{os.pathsep}{os.environ['PATH']}")
    monkeypatch.setenv("FAKE_DOCKER_LOG", str(log))
    monkeypatch.delenv("FABRIQUE_SANDBOX_RUNTIME", raising=False)
    monkeypatch.delenv("FABRIQUE_SANDBOX_REQUIRED", raising=False)
    monkeypatch.setenv(sandbox_grading.IMAGE_ENV, IMAGE)
    root = tmp_path / "ctr"
    monkeypatch.setattr(sandbox_grading, "CANDIDATE", str(root / "candidate"))
    monkeypatch.setattr(sandbox_grading, "GRADERS", str(root / "graders"))

    def runs() -> list[list[str]]:
        if not log.exists():
            return []
        return [json.loads(line) for line in log.read_text().splitlines()]

    return runs
