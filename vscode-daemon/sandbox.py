"""Running agent-written code isolated from the person's machine.

The backend asks the daemon to run the project's tests (and the Developer's
narrower test runs) with a ``sandbox`` request: the mode and its limits. The
daemon runs the command that way and reports what it actually applied, which
the backend keeps on the run's evidence; it never reports a mode it did not
use, and it never falls back to the host on its own when the mode it was
asked for cannot run (the run then fails with the reason, and the tests read
as unverified, not as passed).

Modes:

- ``host``: the command runs as the daemon's user, with its files, its
  network and its environment, as before. Nothing is isolated.
- ``container``: an ephemeral container per run (Docker or Podman, whichever
  is on the PATH; ``FABRIQUE_SANDBOX_RUNTIME`` picks one) with the task's
  worktree mounted at ``/workspace`` and nothing else of the host, network
  off unless the request opens it, CPU, memory and process limits, every
  capability dropped, no privilege escalation, the host user's uid, and only
  the environment variables the company listed (by name; the values come
  from the daemon's own environment, never from the request).
- ``bwrap``: bubblewrap on Linux: the system directories read-only, the
  worktree read-write, new namespaces (network too, unless opened), a fresh
  ``/tmp``, the same environment rule. No CPU or memory limit (bubblewrap
  has none); the report says so.

The person running the daemon can refuse the host for everything the
backend sends: ``FABRIQUE_SANDBOX_REQUIRED=container`` (or ``bwrap``) makes a
``host`` request run in that mode instead, and says so in the report.

In container mode the report also says which image ran (``image_identity``,
``sandbox_image.py``: its content ID and registry digest, observed by the
daemon, the container run from that ID) and, with ``cache`` in the request,
the project's dependency cache volume and its lifecycle (``cache``,
``sandbox_cache.py``). A request's ``purpose`` (``test``, ``install``,
``check`` or ``assistant``) says what the command is for: a failed install
resets the cache. A ``workspace.execute`` without ``sandbox`` is normalized as
``host`` (``shell_exec``), so the floor reaches it too.
"""

from __future__ import annotations

import logging
import math
import os
import shutil
import subprocess
import threading
import uuid
from typing import Any

import run_logs
import sandbox_cache
import sandbox_image

logger = logging.getLogger("fabrique-daemon.sandbox")

MODES = ("host", "container", "bwrap")
DEFAULT_IMAGE = "python:3.12-slim"
DEFAULT_CPUS = 2.0
DEFAULT_MEMORY_MB = 2048
DEFAULT_PIDS = 512
CONTAINER_WORKDIR = "/workspace"
# What a sandboxed command finds in its environment besides the listed names.
BASE_ENV = {
    "PATH": "/usr/local/sbin:/usr/local/bin:/usr/sbin:/usr/bin:/sbin:/bin",
    "HOME": "/tmp",
    "LANG": "C.UTF-8",
    "PYTHONDONTWRITEBYTECODE": "1",
    "CI": "true",
}
# What the daemon keeps of each stream: the tail, read while the command runs,
# so a command that prints without end cannot fill the daemon's memory.
MAX_OUTPUT_BYTES = 1024 * 1024
# ``docker run`` / ``podman run`` exit with this when the runtime itself
# failed (no daemon, no image, a bad flag): the command never started.
RUNTIME_FAILURE_EXIT = 125
_BWRAP_RO = ("/usr", "/bin", "/lib", "/lib64", "/lib32", "/etc", "/opt", "/sbin")
# What a request may say a command is for; anything else reads as ``test``.
# ``assistant``: a console assistant's consultation, advice rather than
# agent-written code; sent only by backends before issue #750 removed the
# console assistants, and still accepted from them.
PURPOSES = ("test", "install", "check", "assistant")


class SandboxUnavailable(RuntimeError):
    """The mode asked for cannot run on this machine; the message says why."""


def _number(value: Any, default: float, low: float, high: float) -> float:
    try:
        number = float(value)
    except (TypeError, ValueError):
        return default
    return min(max(number, low), high)


def normalize(request: Any) -> dict[str, Any]:
    """The sandbox to apply, from the request and the daemon's own floor."""
    raw = request if isinstance(request, dict) else {}
    mode = str(raw.get("mode") or "host").strip().lower()
    if mode not in MODES:
        raise SandboxUnavailable(f"unknown sandbox mode {mode!r}")
    required = os.getenv("FABRIQUE_SANDBOX_REQUIRED", "").strip().lower()
    forced = False
    if required in ("container", "bwrap") and mode == "host":
        mode, forced = required, True
    names = raw.get("env") or []
    env_names = sorted(
        {str(n).strip() for n in names if isinstance(n, str) and str(n).strip().replace("_", "").isalnum()}
    ) if isinstance(names, list) else []
    purpose = str(raw.get("purpose") or "test").strip().lower()
    return {
        "mode": mode,
        "forced_by_daemon": forced,
        "network": bool(raw.get("network", False)),
        # No image and ``stack_image``: the project's own, built from its
        # stacks (``stack_image.py``, issue #766); a backend from before
        # sends neither and gets the old default.
        "image": str(raw.get("image") or ("" if raw.get("stack_image") is True else DEFAULT_IMAGE)),
        "stack_image": raw.get("stack_image") is True and not raw.get("image"),
        "cpus": _number(raw.get("cpus"), DEFAULT_CPUS, 0.1, 64),
        "memory_mb": int(_number(raw.get("memory_mb"), DEFAULT_MEMORY_MB, 64, 262144)),
        "pids_limit": int(_number(raw.get("pids_limit"), DEFAULT_PIDS, 16, 65536)),
        "env": env_names,
        "purpose": purpose if purpose in PURPOSES else "test",
        "cache": raw.get("cache") is True,
        "environment": raw.get("environment") is True,
        "scope": sandbox_cache.clean_scope(raw.get("scope")),
    }


def resolve_image(runtime: str, box: dict[str, Any], cwd: str) -> tuple[dict[str, Any], dict[str, Any] | None]:
    """The box with its image named: the project's stack image when the
    request asked for it (built now when missing), else as it was. Raises
    ``SandboxUnavailable`` with the reason when that image cannot be had:
    never the old default in its place."""
    if not box.get("stack_image"):
        return box, None
    import stack_image

    built = stack_image.ensure(runtime, cwd)
    if not built.get("image"):
        raise SandboxUnavailable(f"no stack image for this project: {built.get('error')}")
    return {**box, "image": built["image"]}, built


def sandbox_env(names: list[str]) -> dict[str, str]:
    """The base environment plus the listed names the daemon itself has."""
    env = dict(BASE_ENV)
    for name in names:
        if name in os.environ:
            env[name] = os.environ[name]
    return env


def container_runtime() -> str:
    """Docker or Podman; ``FABRIQUE_SANDBOX_RUNTIME`` picks one explicitly."""
    configured = os.getenv("FABRIQUE_SANDBOX_RUNTIME", "").strip()
    candidates = [configured] if configured else ["docker", "podman"]
    for name in candidates:
        path = shutil.which(name)
        if path:
            return path
    raise SandboxUnavailable(
        "container mode needs Docker or Podman on the daemon's machine; none was found"
    )


GIT_READ_PATHS = ("HEAD", "objects", "refs", "packed-refs")


def git_mounts(cwd: str) -> list[str]:
    """A worktree's repository, read-only at its own path (issue #766).

    In a ``git worktree`` (every task's, ``fabrique-worktrees/<name>``),
    ``.git`` is a file naming a folder of the main repository, which the
    container did not see: every ``git`` call in the tests failed with "not
    a git repository". What git reads is mounted read-only where the file
    says (``HEAD``, ``objects``, ``refs``, ``packed-refs`` and the worktree's
    own folder), so git reads work and nothing can be changed from inside.
    Never the whole folder: ``config`` may hold a remote's credentials, and
    the other tasks' worktrees live there too."""
    marker = os.path.join(cwd, ".git")
    try:
        if not os.path.isfile(marker):
            return []
        with open(marker, encoding="utf-8") as handle:
            line = handle.readline().strip()
    except OSError:
        return []
    if not line.startswith("gitdir:"):
        return []
    gitdir = os.path.normpath(os.path.join(cwd, line[len("gitdir:"):].strip()))
    common = gitdir
    try:
        with open(os.path.join(gitdir, "commondir"), encoding="utf-8") as handle:
            common = os.path.normpath(os.path.join(gitdir, handle.readline().strip()))
    except OSError:
        pass
    root = os.path.abspath(cwd)
    if not os.path.isdir(common) or common == root or common.startswith(root + os.sep):
        return []
    paths = [os.path.join(common, name) for name in GIT_READ_PATHS]
    if gitdir != common:
        paths.append(gitdir)
    args: list[str] = []
    for path in paths:
        if os.path.exists(path):  # docker would create a missing one on the host
            args += ["-v", f"{path}:{path}:ro"]
    return args


def container_command(
    runtime: str, box: dict[str, Any], command: str, cwd: str, name: str, extra: list[str] | None = None, scratch_mb: int | None = None
) -> list[str]:
    """The ``docker run`` (or ``podman run``) for one sandboxed command;
    *extra* (the cache volume's mount and variables) goes before the image.
    With *scratch_mb* the working directory is a tmpfs of that size (charged
    to the memory limit) instead of *cwd* mounted read-write, so nothing on
    the host's disk is writable (the bench's and the measure's grade,
    ``scripts/bench/sandbox_grading.py``)."""
    args = [
        runtime, "run", "--rm", "--name", name,
        "--network", "bridge" if box["network"] else "none",
        "--cpus", f"{box['cpus']:g}",
        "--memory", f"{box['memory_mb']}m",
        "--memory-swap", f"{box['memory_mb']}m",
        "--pids-limit", str(box["pids_limit"]),
        "--cap-drop", "ALL",
        "--security-opt", "no-new-privileges",
        "--tmpfs", "/tmp:rw,exec,size=512m",
        *(
            ["--tmpfs", f"{CONTAINER_WORKDIR}:rw,exec,size={int(scratch_mb)}m"]
            if scratch_mb
            else ["-v", f"{os.path.abspath(cwd)}:{CONTAINER_WORKDIR}:rw", *git_mounts(cwd)]
        ),
        "-w", CONTAINER_WORKDIR,
    ]
    if hasattr(os, "getuid"):
        if os.path.basename(runtime).startswith("podman") and os.getuid() != 0:
            # Rootless Podman maps --user to a subordinate uid that cannot
            # write the person's worktree; keep-id keeps their own uid/gid.
            args += ["--userns", "keep-id"]
        else:
            args += ["--user", f"{os.getuid()}:{os.getgid()}"]
    env = sandbox_env(box["env"])
    # Tools that size themselves by the core count see the host's cores, not
    # --cpus: pytest-xdist's ``-n auto`` started a worker per host core in a
    # 2-CPU, 2 GB container and lost them (issue #766). Its own variable says
    # how many the limit allows; a project's env may still set it.
    env.setdefault("PYTEST_XDIST_AUTO_NUM_WORKERS", str(max(1, math.ceil(box["cpus"]))))
    for key, value in env.items():
        args += ["-e", f"{key}={value}"]
    return [*args, *(extra or []), box["image"], "/bin/sh", "-c", command]


def bwrap_command(box: dict[str, Any], command: str, cwd: str) -> list[str]:
    """The ``bwrap`` invocation for one sandboxed command."""
    path = shutil.which("bwrap")
    if not path:
        raise SandboxUnavailable("bwrap mode needs bubblewrap (bwrap) on the daemon's machine")
    args = [path, "--die-with-parent", "--unshare-all", "--new-session"]
    if box["network"]:
        args.append("--share-net")
    for directory in _BWRAP_RO:
        if os.path.isdir(directory):
            args += ["--ro-bind", directory, directory]
    args += [
        "--proc", "/proc", "--dev", "/dev", "--tmpfs", "/tmp",
        "--bind", os.path.abspath(cwd), CONTAINER_WORKDIR,
        "--chdir", CONTAINER_WORKDIR, "--clearenv",
    ]
    for key, value in sandbox_env(box["env"]).items():
        args += ["--setenv", key, value]
    return [*args, "/bin/sh", "-c", command]


def report(box: dict[str, Any], runtime: str | None = None) -> dict[str, Any]:
    """What was applied, for the evidence."""
    out: dict[str, Any] = {"mode": box["mode"], "forced_by_daemon": box["forced_by_daemon"]}
    if box.get("cache"):
        # A run that mounts it (``run_sandboxed``) replaces this with what it did.
        out["cache"] = {"state": "not_used", "mounted": False, "reason": (
            "the dependency cache is mounted only in container mode" if box["mode"] != "container"
            else "this run does not mount the dependency cache")}
    if box.get("environment"):
        out["environment"] = {"state": "not_used", "mounted": False, "reason": (
            "the environment volume is mounted only in container mode" if box["mode"] != "container"
            else "this run does not mount the environment volume")}
    if box["mode"] == "host":
        out.update({"isolated": False, "network": "host", "env": "inherited",
                    "image_identity": sandbox_image.no_image("host mode runs no image: the daemon's own machine ran it")})
        return out
    out.update({
        "isolated": True,
        "network": "on" if box["network"] else "off",
        "env": box["env"],
        "workdir": CONTAINER_WORKDIR,
    })
    if box["mode"] == "container":
        out.update({
            "runtime": os.path.basename(runtime or ""),
            "image": box["image"],
            "cpus": box["cpus"],
            "memory_mb": box["memory_mb"],
            "pids_limit": box["pids_limit"],
            "capabilities": "all dropped",
            "no_new_privileges": True,
        })
    else:
        out.update({"cpus": None, "memory_mb": None, "limits": "bubblewrap applies no CPU or memory limit",
                    "image_identity": sandbox_image.no_image("bwrap runs no image: the host's system directories, read-only")})
    return out


class _Tail:
    """The last ``limit`` bytes of a stream, drained by a thread; with a
    *spool*, every byte up to its own cap too (``run_logs``)."""

    def __init__(self, stream: Any, limit: int, spool: run_logs.Spool | None = None) -> None:
        self.data = bytearray()
        self.total = 0
        self._limit = limit
        self.spool = spool
        self._thread = threading.Thread(target=self._drain, args=(stream,), daemon=True)
        self._thread.start()

    def _drain(self, stream: Any) -> None:
        for chunk in iter(lambda: stream.read(65536), b""):
            self.total += len(chunk)
            if self.spool is not None:
                self.spool.write(chunk)
            self.data += chunk
            if len(self.data) > self._limit:
                del self.data[: len(self.data) - self._limit]

    def text(self) -> str:
        self._thread.join(timeout=5)
        return bytes(self.data).decode("utf-8", errors="replace")


def kill_tree(process: subprocess.Popen) -> None:
    """Kill *process* and everything it started: its process group on POSIX
    (it must have been started with ``start_new_session``), its process tree
    on Windows (``taskkill /T /F``), where a shell wrapper's ``terminate()``
    would leave the program it launched running."""
    if os.name == "nt":
        subprocess.run(
            ["taskkill", "/T", "/F", "/PID", str(process.pid)],
            capture_output=True, check=False,
        )
        if process.poll() is None:
            process.kill()
        return
    import signal

    try:
        os.killpg(process.pid, signal.SIGKILL)
    except (ProcessLookupError, PermissionError):
        if process.poll() is None:
            process.kill()


def run_bounded(
    argv: list[str],
    timeout: float,
    cwd: str | None = None,
    env: dict[str, str] | None = None,
    own_group: bool = False,
    keep_full: bool = False,
) -> dict[str, Any]:
    """Run *argv*, keeping at most ``MAX_OUTPUT_BYTES`` of each stream's tail.
    Returns ``exit_code`` (``None`` when killed at *timeout*), the decoded
    ``stdout`` / ``stderr``, ``timed_out`` and ``output_truncated``.

    With *own_group* the command runs in a process group (a session) of its
    own and the whole group (the tree on Windows) is killed at the timeout and
    once it ends, so a shell's children (a test runner, a browser) never
    outlive it; otherwise only *argv*'s process is killed, as before.

    With *keep_full* each stream is also kept whole up to the full log's cap,
    under ``run_logs.KEY`` (``stdout``, ``stderr``), for the caller to upload."""
    process = subprocess.Popen(
        argv, stdout=subprocess.PIPE, stderr=subprocess.PIPE, cwd=cwd, env=env,
        start_new_session=own_group and os.name != "nt",
    )
    out = _Tail(process.stdout, MAX_OUTPUT_BYTES, run_logs.Spool() if keep_full else None)
    err = _Tail(process.stderr, MAX_OUTPUT_BYTES, run_logs.Spool() if keep_full else None)
    timed_out = False
    try:
        process.wait(timeout=timeout)
    except subprocess.TimeoutExpired:
        timed_out = True
        if own_group:
            kill_tree(process)
        else:
            process.kill()
        process.wait()
    finally:
        if own_group and not timed_out:
            # The shell is gone; what it left running in its group goes too.
            kill_tree(process)
    stdout, stderr = out.text(), err.text()
    result = {
        "exit_code": None if timed_out else process.returncode,
        "stdout": stdout,
        "stderr": stderr,
        "timed_out": timed_out,
        "output_truncated": out.total > MAX_OUTPUT_BYTES or err.total > MAX_OUTPUT_BYTES,
    }
    run_logs.keep(result, "stdout", out.spool)
    run_logs.keep(result, "stderr", err.spool)
    return result


def run_sandboxed(command: str, cwd: str, request: Any, timeout: int, keep_full: bool = False) -> dict[str, Any]:
    """Run *command* in *cwd* as the request's sandbox says; the result carries
    ``sandbox`` (what was applied). Host mode is the caller's own runner.
    With *keep_full* it also keeps each stream whole for the full log
    (``run_logs.KEY``), which the caller uploads and removes.

    Raises ``SandboxUnavailable`` when the container runtime could not start
    the container: nothing ran, so there is no exit status and no sandbox to
    report."""
    box = normalize(request)
    if box["mode"] == "host":
        raise ValueError("host mode is run by the caller")
    runtime = container_runtime() if box["mode"] == "container" else None
    name = f"fabrique-run-{uuid.uuid4().hex[:12]}"
    cache: dict[str, Any] | None = None
    env: dict[str, Any] | None = None
    stack = None
    if runtime:
        box, stack = resolve_image(runtime, box, cwd)
        # Run from the image's content ID, observed now (``sandbox_image``).
        run_box, identity = sandbox_image.pinned(runtime, box)
        if box["cache"]:
            cache = sandbox_cache.prepare(runtime, run_box["image"], cwd)
        if box["environment"]:
            # Keyed by the image asked for: a first run that pulls it keeps its key.
            env = sandbox_cache.prepare_env(runtime, box["image"], cwd, box["purpose"], box["scope"], BASE_ENV["PATH"], run_box["image"])
        # The environment's variables last: they win over the cache's for Go.
        extra = [*(cache or {}).get("args", []), *(env or {}).get("args", [])]
        argv = container_command(runtime, run_box, command, cwd, name, extra)
    else:
        argv = bwrap_command(box, command, cwd)
    applied = report(box, runtime)
    if stack is not None:
        applied["stack_image"] = stack
    logger.info("Sandboxed (%s): %s in %s", box["mode"], command, cwd)
    run = run_bounded(argv, timeout, keep_full=keep_full)
    spools = {run_logs.KEY: run.pop(run_logs.KEY)} if run_logs.KEY in run else {}
    if runtime:
        applied["image_identity"] = sandbox_image.observed_after(runtime, box["image"], identity)
    if run["timed_out"]:
        if runtime:
            subprocess.run([runtime, "kill", name], capture_output=True, check=False)
        if cache is not None:
            applied["cache"] = sandbox_cache.finish(runtime, cache, box["purpose"], False, cwd)
        if env is not None:
            applied["environment"] = sandbox_cache.finish_env(runtime, env, box["purpose"], False)
        return {
            "success": False,
            "stdout": run["stdout"],
            "stderr": run["stderr"],
            "output_truncated": run["output_truncated"],
            "timed_out": True,
            "timeout_seconds": timeout,
            "error": f"command timed out after {timeout}s",
            "sandbox": applied,
            **spools,
        }
    if runtime and run["exit_code"] == RUNTIME_FAILURE_EXIT:
        run_logs.discard(spools)
        raise SandboxUnavailable(
            f"{os.path.basename(runtime)} could not start the container: "
            f"{run['stderr'].strip()[-500:] or 'no message'}"
        )
    if cache is not None:
        applied["cache"] = sandbox_cache.finish(runtime, cache, box["purpose"], run["exit_code"] == 0, cwd)
    if env is not None:
        applied["environment"] = sandbox_cache.finish_env(runtime, env, box["purpose"], run["exit_code"] == 0)
    return {
        "success": run["exit_code"] == 0,
        "stdout": run["stdout"],
        "stderr": run["stderr"],
        "output_truncated": run["output_truncated"],
        "exit_code": run["exit_code"],
        "sandbox": applied,
        **spools,
    }
