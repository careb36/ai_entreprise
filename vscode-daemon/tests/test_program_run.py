"""A declared program (``sandbox.purpose: program``, PROTOCOL.md §5.12).

The container runtime is a fake ``docker`` on the PATH (a Python script), as
in ``test_sandbox_env.py``: it records each call, runs the container's
command on the host with ``/artifacts`` and ``/workspace`` mapped to their
host folders through ``$ARTIFACTS`` and ``$WORKSPACE``, and kills a running
"container" on ``docker kill``. No Docker is needed; the real-container
tests are in ``test_program_run_container.py``."""

from __future__ import annotations

import base64
import hashlib
import json
import os
import stat
import sys
import textwrap
import time

import abilities
import program_run
import pytest
import sandbox
import shell_exec

PY = sys.executable
IMAGE_ID = "d" * 64


@pytest.fixture
def docker(tmp_path, monkeypatch):
    state = tmp_path / "docker-state"
    state.mkdir()
    script = tmp_path / "bin" / "docker"
    script.parent.mkdir()
    script.write_text(f"#!{PY}\n" + textwrap.dedent(f"""
        import json, os, signal, subprocess, sys
        state = {str(state)!r}
        args = sys.argv[1:]
        open(os.path.join(state, "calls.log"), "a").write(json.dumps(args) + "\\n")
        if args[:2] == ["image", "inspect"]:
            print(json.dumps([{{"Id": "sha256:{IMAGE_ID}", "RepoDigests": []}}])); sys.exit(0)
        if args[0] == "kill":
            try:
                pid = int(open(os.path.join(state, args[1] + ".pid")).read())
                os.killpg(pid, signal.SIGKILL)
            except (OSError, ValueError):
                pass
            sys.exit(0)
        if args[0] == "run":
            name = args[args.index("--name") + 1]
            env = dict(os.environ)
            for i, a in enumerate(args):
                if a == "-v" and args[i + 1].split(":")[1] == "/artifacts":
                    env["ARTIFACTS"] = args[i + 1].split(":")[0]
                if a == "-v" and args[i + 1].split(":")[1] == "/workspace":
                    env["WORKSPACE"] = args[i + 1].split(":")[0]
            child = subprocess.Popen(["/bin/sh", "-c", args[-1]], cwd=env["WORKSPACE"], env=env,
                                     start_new_session=True)
            open(os.path.join(state, name + ".pid"), "w").write(str(child.pid))
            sys.exit(child.wait() % 256)
        sys.exit(0)
    """))
    script.chmod(script.stat().st_mode | stat.S_IEXEC)
    monkeypatch.setenv("PATH", f"{script.parent}{os.pathsep}{os.environ['PATH']}")
    monkeypatch.setenv("FABRIQUE_ARTIFACTS_ROOT", str(tmp_path / "artifacts"))
    monkeypatch.setenv("FABRIQUE_ARTIFACTS_MIN_FREE_MB", "0")
    monkeypatch.setenv("FABRIQUE_SANDBOX_CACHE_STATE", str(tmp_path / "cache-state"))
    for name in ("FABRIQUE_SANDBOX_RUNTIME", "FABRIQUE_SANDBOX_REQUIRED", *(v[0] for v in program_run.MAXIMA.values())):
        monkeypatch.delenv(name, raising=False)
    return state


def _calls(state):
    path = state / "calls.log"
    return [json.loads(line) for line in path.read_text().splitlines()] if path.exists() else []


def _runs(state):
    return [c for c in _calls(state) if c[0] == "run"]


def _request(**over):
    program = over.pop("program", {"artifacts_mb": 16})
    return {"mode": "container", "image": "python:3.12-slim", "purpose": "program", "network": False,
            "workdir": "ro", "scope": "company:7", "program": program, **over}


def _run(work, command, timeout=30, **over):
    return program_run.run(command, str(work), _request(**over), timeout)


def _flag(run, name):
    return run[run.index(name) + 1]


# ---------------------------------------------------------------------------
# Container only, network off, the worktree read-only
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("mode", ["host", "bwrap"])
def test_a_program_in_host_or_bwrap_mode_is_refused_before_anything_runs(docker, tmp_path, mode):
    with pytest.raises(sandbox.SandboxUnavailable, match=f"only in container mode, not {mode}"):
        _run(tmp_path, "python3 evaluate.py", mode=mode)
    reply = shell_exec.execute_shell_command("python3 evaluate.py", str(tmp_path), _request(mode=mode))
    assert reply == {"success": False, "error": f"sandbox unavailable: a program runs only in container mode, not {mode}"}
    assert _runs(docker) == []


def test_the_daemon_floor_never_raises_a_host_program_to_a_container(docker, tmp_path, monkeypatch):
    monkeypatch.setenv("FABRIQUE_SANDBOX_REQUIRED", "container")
    reply = shell_exec.execute_shell_command("python3 evaluate.py", str(tmp_path), _request(mode="host"))
    assert "only in container mode" in reply["error"] and _runs(docker) == []


def test_the_network_is_off_whatever_the_request_says(docker, tmp_path):
    reply = _run(tmp_path, "true", network=True)
    run = _runs(docker)[-1]
    assert _flag(run, "--network") == "none"
    assert reply["sandbox"]["network"] == "off" and reply["sandbox"]["mode"] == "container"


def test_the_worktree_is_mounted_read_only_with_artifacts_and_tmp_writable(docker, tmp_path):
    work = tmp_path / "work"
    work.mkdir()
    reply = _run(work, "true", program={"artifacts_mb": 4, "folder": "ml"})
    run = _runs(docker)[-1]
    mounts = [run[i + 1] for i, a in enumerate(run) if a == "-v"]
    assert f"{work}:/workspace:ro" in mounts
    assert not any(m.endswith(":/workspace:rw") for m in mounts)
    artifacts = next(m for m in mounts if ":/artifacts:" in m)
    assert artifacts.endswith(":/artifacts:rw") and str(work) not in artifacts
    assert "company-7" in artifacts
    assert _flag(run, "--tmpfs").startswith("/tmp:")
    assert _flag(run, "-w") == "/workspace/ml"
    assert "--pull=never" in run
    assert reply["sandbox"]["workdir_mode"] == "ro"


# ---------------------------------------------------------------------------
# Limits the backend cannot raise
# ---------------------------------------------------------------------------


def test_limits_are_capped_by_the_daemon_maxima(docker, tmp_path, monkeypatch):
    monkeypatch.setenv("FABRIQUE_PROGRAM_MAX_CPUS", "1")
    monkeypatch.setenv("FABRIQUE_PROGRAM_MAX_MEMORY_MB", "256")
    monkeypatch.setenv("FABRIQUE_PROGRAM_MAX_PIDS", "64")
    monkeypatch.setenv("FABRIQUE_PROGRAM_MAX_ARTIFACTS_MB", "2")
    reply = _run(tmp_path, "true", cpus=8, memory_mb=8192, pids_limit=4096, program={"artifacts_mb": 999})
    run = _runs(docker)[-1]
    assert _flag(run, "--cpus") == "1" and _flag(run, "--memory") == "256m"
    assert _flag(run, "--memory-swap") == "256m" and _flag(run, "--pids-limit") == "64"
    assert reply["program"]["limits"]["artifacts_mb"] == 2


def test_the_time_limit_stops_the_run_and_the_daemon_maximum_wins(docker, tmp_path, monkeypatch):
    monkeypatch.setenv("FABRIQUE_PROGRAM_MAX_SECONDS", "1")
    started = time.monotonic()
    reply = _run(tmp_path, "sleep 30", timeout=600)
    assert time.monotonic() - started < 15
    assert reply["timed_out"] is True and reply["success"] is False and reply["exit_code"] is None
    assert reply["program"]["limits"]["seconds"] == 1
    assert any(c[0] == "kill" for c in _calls(docker))


# ---------------------------------------------------------------------------
# The disk: a floor before, a watch during
# ---------------------------------------------------------------------------


def test_writing_past_the_artifacts_cap_kills_the_run(docker, tmp_path):
    slow = 'while true; do head -c 262144 /dev/zero >> "$ARTIFACTS/big.bin"; sleep 0.05; done'
    started = time.monotonic()
    reply = _with_command(docker, tmp_path, slow, program={"artifacts_mb": 1})
    assert time.monotonic() - started < 20
    artifacts = reply["program"]["artifacts"]
    assert artifacts["killed_for_size"] is True and artifacts["artifact_limit"] == "watch"
    assert artifacts["peak_bytes"] > 1024 * 1024
    assert reply["success"] is False and "passed 1 MiB" in reply["error"]
    assert any(c[0] == "kill" for c in _calls(docker))


def test_below_the_free_space_floor_the_run_is_refused(docker, tmp_path, monkeypatch):
    monkeypatch.setenv("FABRIQUE_ARTIFACTS_MIN_FREE_MB", str(10**9))
    reply = _run(tmp_path, "true")
    assert reply["refused"] is True and "below the floor" in reply["error"]
    assert _runs(docker) == []


def test_run_folders_past_the_retention_are_removed(docker, tmp_path):
    old = tmp_path / "artifacts" / "company-7" / "oldrun"
    old.mkdir(parents=True)
    week = time.time() - 15 * 86400
    os.utime(old, (week, week))
    reply = _run(tmp_path, "true")
    assert not old.exists() and reply["program"]["pruned_runs"] == 1


# ---------------------------------------------------------------------------
# The manifest and the metrics file
# ---------------------------------------------------------------------------


def test_the_manifest_hashes_files_read_back_and_lists_a_symlink_without_following_it(docker, tmp_path):
    secret = tmp_path / "outside.txt"
    secret.write_text("not for the record")
    command = f'printf hello > "$ARTIFACTS/out.txt" && ln -s {secret} "$ARTIFACTS/link.txt"'
    reply = _with_command(docker, tmp_path, command)
    files = {f["path"]: f for f in reply["program"]["artifacts"]["files"]}
    assert files["out.txt"] == {"path": "out.txt", "bytes": 5, "sha256": hashlib.sha256(b"hello").hexdigest()}
    assert files["link.txt"] == {"path": "link.txt", "link": True}
    assert reply["program"]["artifacts"]["artifact_limit"] == "watch"


def test_the_declared_metrics_file_is_sent_whole_and_a_symlinked_one_is_not(docker, tmp_path):
    body = b'{"metrics": [{"name": "accuracy", "value": 0.7, "split": "val"}]}'
    reply = _with_command(docker, tmp_path, f"printf '%s' '{body.decode()}' > \"$ARTIFACTS/metrics.json\"",
                          program={"artifacts_mb": 4, "metrics_file": "metrics.json"})
    assert base64.b64decode(reply["program"]["metrics_file"]["content_b64"]) == body
    target = tmp_path / "elsewhere.json"
    target.write_bytes(body)
    reply = _with_command(docker, tmp_path, f'ln -s {target} "$ARTIFACTS/metrics.json"',
                          program={"artifacts_mb": 4, "metrics_file": "metrics.json"})
    assert "content_b64" not in reply["program"]["metrics_file"]
    assert "regular file" in reply["program"]["metrics_file"]["reason"]


# ---------------------------------------------------------------------------
# The command and the folder are plain
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("command", ["python3 x.py; rm -rf ~", "python3 $(id)", "python3 ../x.py", "python3 /etc/x.py", "a|b"])
def test_a_command_that_is_not_plain_words_is_refused(docker, tmp_path, command):
    reply = _run(tmp_path, command)
    assert reply["refused"] is True and _runs(docker) == []


def test_a_scope_of_dots_stays_under_the_artifacts_root(docker, tmp_path):
    reply = _run(tmp_path, "true", scope="..")
    run = _runs(docker)[-1]
    artifacts = next(run[i + 1] for i, a in enumerate(run) if a == "-v" and ":/artifacts:" in run[i + 1])
    assert artifacts.startswith(str(tmp_path / "artifacts" / "--")) and reply["exit_code"] == 0


def test_a_folder_that_leaves_the_worktree_is_refused(docker, tmp_path):
    reply = _run(tmp_path, "true", program={"folder": "../up"})
    assert reply["refused"] is True and _runs(docker) == []


def test_the_daemon_announces_program_runs_on_execute():
    assert "program" in abilities.announcement()["features"]["workspace.execute"]


# The backend never sends shell syntax, so a test's shell lines go in a script
# in the worktree, and the plain command runs it (the fake runs it there).
def _with_command(docker, work, text, timeout=30, **over):
    (work / "prog.sh").write_text(text)
    return program_run.run("sh prog.sh", str(work), _request(**over), timeout)
