"""``workspace.app.check``: start the application, probe it, stop it.

The backend asks for this after a delivery's tests (``services/app_check.py``,
opt-in with ``DELIVERY_APP_CHECK_ENABLED``): the profile's start command runs
in the request's sandbox, the daemon waits (bounded) for the port to answer,
sends one HTTP GET to the probe path, runs the profile's e2e command when
there is one (with ``APP_URL`` pointing at the running application), then
stops the application and replies with what it observed: whether it started,
listened, answered and with which status, how the e2e command ended, the
tail of the application's log, and the sandbox applied. Never the agent's
word; the backend's Verifier reads this reply.

The same rules as ``workspace.execute``: the command policy (the request's
and the daemon's own ``FABRIQUE_COMMAND_DENY``) refuses a start or e2e
command before anything runs, and a sandbox the daemon cannot apply is an
error, never a run on the host. ``host`` and ``container`` modes are
supported; ``bwrap`` (whose network namespace a probe cannot reach) is
refused with the reason, as is a port something else already listens on
(the probe would reach the wrong program).
"""

from __future__ import annotations

import asyncio
import os
import signal
import socket
import subprocess
import tempfile
import time
import urllib.error
import urllib.request
import uuid
from typing import Any, Dict, Optional

import command_policy
import sandbox
import sandbox_image
import shell_exec
import win_job

# Bounds on what a request may ask for, and on what the reply keeps.
MAX_READY_SECONDS = 300
MAX_E2E_SECONDS = 900
DEFAULT_READY_SECONDS = 60
DEFAULT_E2E_SECONDS = 300
PROBE_TIMEOUT_SECONDS = 10
STOP_GRACE_SECONDS = 5
LOG_TAIL_CHARS = 4000
POLL_SECONDS = 0.2
# Container mode: how long ``docker run -d`` may take (a cold image is pulled),
# and each later runtime call (inspect, exec, logs, rm). The backend's reply
# wait (``services/app_check.py``) adds these to the ready and e2e bounds.
LAUNCH_SECONDS = 120
RUNTIME_CALL_SECONDS = 30
_WINDOWS = os.name == "nt"

# Run inside a container to probe the application there: whichever HTTP
# client the image has. Prints the status code; fails when nothing answers.
_CONTAINER_PROBE = (
    'u="$APP_URL$PROBE_PATH"; '
    "if command -v curl >/dev/null 2>&1; then "
    'c=$(curl -s -o /dev/null -w "%{http_code}" --max-time 5 "$u") && [ "$c" != 000 ] && echo "$c"; '
    "elif command -v python3 >/dev/null 2>&1; then "
    "python3 -c 'import sys,urllib.request,urllib.error\n"
    "try: print(urllib.request.urlopen(sys.argv[1], timeout=5).status)\n"
    "except urllib.error.HTTPError as e: print(e.code)' \"$u\"; "
    "elif command -v node >/dev/null 2>&1; then "
    "node -e 'require(\"http\").get(process.argv[1],r=>{console.log(r.statusCode);process.exit(0)})"
    ".on(\"error\",()=>process.exit(1))' \"$u\"; "
    "else echo 'no HTTP client in the image' >&2; exit 2; fi"
)


def _bounded(value: Any, default: int, high: int) -> int:
    try:
        number = int(float(value))
    except (TypeError, ValueError):
        return default
    return min(max(number, 1), high)


def _tail(text: str, total: int | None = None) -> Dict[str, Any]:
    total = len(text) if total is None else total
    return {
        "output_tail": text[-LOG_TAIL_CHARS:],
        "output_bytes": total,
        "output_truncated": total > LOG_TAIL_CHARS,
    }


def port_in_use(port: int) -> bool:
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as probe:
        probe.settimeout(0.5)
        return probe.connect_ex(("127.0.0.1", port)) == 0


def http_probe(url: str) -> Dict[str, Any]:
    """One GET: the status code, or the error when nothing answered."""
    try:
        with urllib.request.urlopen(url, timeout=PROBE_TIMEOUT_SECONDS) as response:
            return {"url": url, "status": response.status}
    except urllib.error.HTTPError as e:
        return {"url": url, "status": e.code}
    except (urllib.error.URLError, OSError, ValueError) as e:
        return {"url": url, "status": None, "error": str(getattr(e, "reason", e))}


def _group_alive(pgid: int) -> bool:
    try:
        os.killpg(pgid, 0)
    except (ProcessLookupError, PermissionError):
        return False
    return True


def _await_group(pgid: int) -> None:
    """The shell may exit before the server it started: wait for the whole group.

    A zombie still answers ``killpg(pgid, 0)`` but holds no socket, so the wait
    is bounded and ends in SIGKILL rather than a hang."""
    deadline = time.monotonic() + STOP_GRACE_SECONDS
    while _group_alive(pgid) and time.monotonic() < deadline:
        time.sleep(POLL_SECONDS / 5)
    if _group_alive(pgid):
        try:
            os.killpg(pgid, signal.SIGKILL)
        except (ProcessLookupError, PermissionError):
            return
        deadline = time.monotonic() + 1
        while _group_alive(pgid) and time.monotonic() < deadline:
            time.sleep(POLL_SECONDS / 5)


def _stop_host(process: subprocess.Popen, port: int, job: Optional[int] = None) -> bool:
    """Stop the application and everything it started; ``True`` only once the
    port no longer answers, so a program that outlived the stop is reported.

    On Windows *job* is the Job Object the application runs in (``win_job``):
    terminating it reaches every process it started, also after the wrapper
    exited; without one the stop falls back to ``taskkill /T``."""
    stopped = _signal_leader(process, job)
    if not _WINDOWS:
        _await_group(process.pid)
    return stopped and not port_in_use(port)


def _signal_leader(process: subprocess.Popen, job: Optional[int] = None) -> bool:
    if _WINDOWS:
        # terminate() would stop only the cmd/PowerShell wrapper: end the job,
        # which holds orphans too, else kill the tree while the wrapper lives.
        if not win_job.terminate(job):
            sandbox.kill_tree(process)
        try:
            process.wait(timeout=STOP_GRACE_SECONDS)
        except subprocess.TimeoutExpired:
            return False
        return True
    if process.poll() is not None:
        try:
            os.killpg(process.pid, signal.SIGTERM)
        except (ProcessLookupError, PermissionError):
            pass
        return True
    try:
        os.killpg(process.pid, signal.SIGTERM)
        process.wait(timeout=STOP_GRACE_SECONDS)
    except (subprocess.TimeoutExpired, ProcessLookupError, PermissionError):
        try:
            os.killpg(process.pid, signal.SIGKILL)
            process.wait(timeout=STOP_GRACE_SECONDS)
        except (subprocess.TimeoutExpired, ProcessLookupError, PermissionError):
            return False
    return True


def _run_host(req: Dict[str, Any], box: Dict[str, Any]) -> Dict[str, Any]:
    port, cwd = req["port"], req["cwd"]
    if port_in_use(port):
        return {"error": f"port {port} is already in use on the daemon's machine"}
    env = shell_exec.build_command_env()
    env.update({"PORT": str(port), "APP_URL": req["app_url"]})
    out: Dict[str, Any] = {"sandbox": sandbox.report(box)}
    with tempfile.TemporaryFile() as log:
        process = subprocess.Popen(
            shell_exec.build_shell_command(req["start"]),
            cwd=cwd, env=env, stdout=log, stderr=subprocess.STDOUT,
            start_new_session=not _WINDOWS,
            creationflags=win_job.creationflags(),
        )
        # Windows: contained in a kill-on-close job before it may start anything.
        job = win_job.contain(process)
        out["started"] = True
        began = time.monotonic()
        try:
            while True:
                if process.poll() is not None:
                    out["exit_code"] = process.returncode
                    break
                if port_in_use(port):
                    out["listening"] = True
                    out["ready_seconds"] = round(time.monotonic() - began, 2)
                    break
                if time.monotonic() - began > req["ready_seconds"]:
                    break
                time.sleep(POLL_SECONDS)
            if out.get("listening"):
                out["probe"] = http_probe(req["app_url"] + req["probe_path"])
                if req.get("e2e"):
                    # Its own group: a timed-out runner's children go with it.
                    run = sandbox.run_bounded(
                        shell_exec.build_shell_command(req["e2e"]), req["e2e_seconds"],
                        cwd=cwd, env=env, own_group=True,
                    )
                    out["e2e"] = _e2e_record(req, run)
        finally:
            out["stopped"] = _stop_host(process, port, job)
        log.seek(0, os.SEEK_END)
        size = log.tell()
        log.seek(max(0, size - LOG_TAIL_CHARS))
        out["log"] = _tail(log.read().decode("utf-8", errors="replace"), size)
    return out


def _e2e_record(req: Dict[str, Any], run: Dict[str, Any]) -> Dict[str, Any]:
    text = f"{run.get('stdout') or ''}{run.get('stderr') or ''}"
    record = {"command": req["e2e"], "exit_code": run.get("exit_code"), "timed_out": bool(run.get("timed_out"))}
    if record["timed_out"]:
        record["timeout_seconds"] = req["e2e_seconds"]
    record.update(_tail(text))
    if run.get("output_truncated"):
        # The runner's own bound cut the stream: its head is gone too.
        record["stream_truncated"] = True
    if record["output_truncated"]:
        # A list reporter prints one line per test first and the failures'
        # details after: the head keeps the lines the backend reads back.
        record["output_head"] = text[:LOG_TAIL_CHARS]
    return record


def _docker(runtime: str, *args: str, timeout: float = RUNTIME_CALL_SECONDS) -> subprocess.CompletedProcess:
    return subprocess.run([runtime, *args], capture_output=True, text=True, timeout=timeout, check=False)


def _remove(runtime: str, name: str) -> bool:
    try:
        return _docker(runtime, "rm", "-f", name).returncode == 0
    except (OSError, subprocess.SubprocessError):
        return False


def _run_container(req: Dict[str, Any], box: Dict[str, Any]) -> Dict[str, Any]:
    runtime = sandbox.container_runtime()
    name = f"fabrique-app-{uuid.uuid4().hex[:12]}"
    # From the image's content ID, observed now; reported below (``sandbox_image``).
    run_box, identity = sandbox_image.pinned(runtime, box)
    argv = sandbox.container_command(runtime, run_box, req["start"], req["cwd"], name)
    argv.remove("--rm")  # Kept until its log is read; removed below.
    argv[2:2] = ["-d", "-e", f"PORT={req['port']}", "-e", f"APP_URL={req['app_url']}"]
    out: Dict[str, Any] = {"sandbox": sandbox.report(box, runtime)}
    out["sandbox"]["image_identity"] = identity
    # From the launch on, the container is removed by name whatever happens:
    # a launch that timed out may still have created it, and a log that cannot
    # be read must not leave it running with the worktree mounted.
    try:
        try:
            started = subprocess.run(argv, capture_output=True, text=True, timeout=LAUNCH_SECONDS, check=False)
        except subprocess.TimeoutExpired:
            out["error"] = f"{os.path.basename(runtime)} did not start the container within {LAUNCH_SECONDS}s"
            return out
        if started.returncode != 0:
            out["error"] = (f"{os.path.basename(runtime)} could not start the container: "
                            f"{started.stderr.strip()[-500:] or 'no message'}")
            return out
        out["started"] = True
        out["sandbox"]["image_identity"] = sandbox_image.observed_after(runtime, box["image"], identity)
        try:
            _watch_container(runtime, name, req, out)
        except subprocess.SubprocessError as exc:
            out["error"] = f"the container runtime did not answer: {exc}"
    finally:
        try:
            if out.get("started"):
                logs = _docker(runtime, "logs", "--tail", "400", name)
                out["log"] = _tail(f"{logs.stdout}{logs.stderr}")
        except (OSError, subprocess.SubprocessError) as exc:
            out["log"] = _tail(f"(the container's log could not be read: {exc})")
        finally:
            out["stopped"] = _remove(runtime, name)
    return out


def _watch_container(runtime: str, name: str, req: Dict[str, Any], out: Dict[str, Any]) -> None:
    """Wait for the application inside the container to answer, then run the e2e command."""
    env_args = ["-e", f"APP_URL={req['app_url']}", "-e", f"PROBE_PATH={req['probe_path']}"]
    began = time.monotonic()
    while True:
        state = _docker(runtime, "inspect", "-f", "{{.State.Running}} {{.State.ExitCode}}", name).stdout.split()
        if state and state[0] != "true":
            out["exit_code"] = int(state[1]) if len(state) > 1 and state[1].lstrip("-").isdigit() else None
            break
        probe = _docker(runtime, "exec", *env_args, name, "/bin/sh", "-c", _CONTAINER_PROBE)
        status = probe.stdout.strip().splitlines()[-1:] if probe.returncode == 0 else []
        if status and status[0].isdigit():
            out["listening"] = True
            out["ready_seconds"] = round(time.monotonic() - began, 2)
            out["probe"] = {"url": req["app_url"] + req["probe_path"], "status": int(status[0])}
            break
        if probe.returncode == 2:
            out["error"] = "the sandbox image has no HTTP client (curl, python3 or node) to probe with"
            break
        if time.monotonic() - began > req["ready_seconds"]:
            break
        time.sleep(POLL_SECONDS)
    if out.get("listening") and req.get("e2e"):
        run = sandbox.run_bounded(
            [runtime, "exec", *env_args, name, "/bin/sh", "-c", req["e2e"]], req["e2e_seconds"]
        )
        out["e2e"] = _e2e_record(req, run)


def check_application(data: Dict[str, Any]) -> Dict[str, Any]:
    """Start, wait, probe, e2e, stop; the reply ``workspace.app.check`` sends."""
    start = data.get("start_command")
    cwd = data.get("workspace_root")
    port = data.get("port")
    if not isinstance(start, str) or not start.strip() or not isinstance(cwd, str):
        return {"error": "workspace_root and start_command are required"}
    if isinstance(port, bool) or not isinstance(port, int) or not 1 <= port <= 65535:
        return {"error": "a port between 1 and 65535 is required"}
    if not os.path.isdir(cwd):
        return {"error": f"no such folder on the daemon's machine: {cwd}"}
    e2e = data.get("e2e_command") if isinstance(data.get("e2e_command"), str) else None
    policy = data.get("command_policy")
    for command in (start, e2e):
        refused = command and command_policy.refusal(command, policy)
        if refused:
            return {"refused": True, "error": refused, "command": command}
    probe_path = data.get("probe_path") if isinstance(data.get("probe_path"), str) else "/"
    req = {
        "start": start, "e2e": (e2e or "").strip() or None, "cwd": cwd, "port": port,
        "probe_path": probe_path if probe_path.startswith("/") else f"/{probe_path}",
        "app_url": f"http://127.0.0.1:{port}",
        "ready_seconds": _bounded(data.get("ready_timeout_seconds"), DEFAULT_READY_SECONDS, MAX_READY_SECONDS),
        "e2e_seconds": _bounded(data.get("e2e_timeout_seconds"), DEFAULT_E2E_SECONDS, MAX_E2E_SECONDS),
    }
    try:
        box = sandbox.normalize(data.get("sandbox"))
        if box["mode"] == "bwrap":
            raise sandbox.SandboxUnavailable("the application check runs in host or container mode, not bwrap")
        result = _run_container(req, box) if box["mode"] == "container" else _run_host(req, box)
    except sandbox.SandboxUnavailable as exc:
        return {"error": f"sandbox unavailable: {exc}"}
    except (OSError, subprocess.SubprocessError) as exc:
        return {"error": f"the application could not be run: {exc}"}
    result.update({"command": start, "port": port, "ready_timeout_seconds": req["ready_seconds"]})
    result.setdefault("listening", False)
    return result


async def handle_app_check(nc, msg, reply, request_body) -> None:
    """``workspace.app.check`` → what ``check_application`` observed."""
    body = await asyncio.to_thread(check_application, request_body(msg))
    await reply(nc, msg, body)
