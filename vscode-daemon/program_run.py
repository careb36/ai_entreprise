"""A declared program of the project: ``workspace.execute`` with ``sandbox.purpose: "program"``.

PROTOCOL.md §5.12, ``docs/design/project-programs-and-screenshots.md``
(slice 1). The backend sends a program a person declared in the company's
stack profile, its typed parameters already rendered as plain words. This
daemon runs it in a container only, whatever else the request says:

- ``host`` and ``bwrap`` are refused (``sandbox.normalize`` raises
  ``SandboxUnavailable``): ``host`` has no isolation, ``bwrap`` no CPU or
  memory limit. The daemon's floor never raises a program request to a
  container either: the request itself must say ``container``.
- the network is off (``--network none``), whatever ``network`` says;
- the worktree is mounted read-only at ``/workspace``; the writable places
  are ``/tmp`` (a 512 MiB tmpfs, charged to memory) and ``/artifacts``, a
  folder this daemon creates per run under ``FABRIQUE_ARTIFACTS_ROOT``,
  outside every worktree;
- time, CPU, memory (no swap) and processes are the request's, each capped by
  this machine's maxima (``FABRIQUE_PROGRAM_MAX_*``), which no request can
  raise; ``--pull=never``: nothing is fetched to run it;
- before it starts, the free space under the artifacts root must be at least
  ``FABRIQUE_ARTIFACTS_MIN_FREE_MB``; while it runs, a watch measures
  ``/artifacts`` every second and kills the container past the cap
  (``artifact_limit: watch``: a bind mount has no per-folder quota in
  Docker or Podman); run folders older than
  ``FABRIQUE_ARTIFACTS_RETENTION_DAYS`` are removed first;
- the command is checked again here: plain words only (letters, digits and
  ``_ . = : / + -``, no ``..``, no absolute path), so nothing a shell reads
  as syntax can be in it. The command policy has already read it.

When it ends, the daemon lists ``/artifacts`` without following a symlink
(a link is listed as one, never read) and replies a manifest of each file's
path, size and sha256 read back from disk. The declared metrics file, when
it is a regular file of at most ``METRICS_FILE_MAX`` bytes, is sent whole
(base64) for the backend to hash and parse; this daemon never parses a
metric. ``stdout`` and ``stderr`` stay apart in the reply.
"""

from __future__ import annotations

import base64
import hashlib
import logging
import os
import re
import shutil
import subprocess
import threading
import time
import uuid
from pathlib import Path
from typing import Any

import run_logs
import sandbox
import sandbox_cache
import sandbox_image

logger = logging.getLogger("fabrique-daemon.program")

ARTIFACTS_MOUNT = "/artifacts"
MIB = 1024 * 1024
METRICS_FILE_MAX = 256 * 1024
MAX_MANIFEST = 1000
WATCH_SECONDS = 1.0
# This machine's maxima: (variable, default, low, high).
MAXIMA = {
    "seconds": ("FABRIQUE_PROGRAM_MAX_SECONDS", 900, 1, 86400),
    "cpus": ("FABRIQUE_PROGRAM_MAX_CPUS", 4, 0.1, 256),
    "memory_mb": ("FABRIQUE_PROGRAM_MAX_MEMORY_MB", 8192, 64, 1048576),
    "pids_limit": ("FABRIQUE_PROGRAM_MAX_PIDS", 512, 16, 65536),
    "artifacts_mb": ("FABRIQUE_PROGRAM_MAX_ARTIFACTS_MB", 2048, 1, 1048576),
}
_WORD = re.compile(r"[A-Za-z0-9_.=:/+-]{1,200}")
_SEGMENT = re.compile(r"[A-Za-z0-9_][A-Za-z0-9_.-]{0,80}")
_FILE = re.compile(r"[A-Za-z0-9_][A-Za-z0-9_.-]{0,63}\.json")


def _env_number(name: str, default: float, low: float, high: float) -> float:
    try:
        value = float(os.getenv(name, str(default)))
    except ValueError:
        value = default
    return min(max(value, low), high)


def _number(value: Any, default: float) -> float:
    try:
        number = float(value)
    except (TypeError, ValueError):
        return default
    return number if number == number else default  # NaN reads as the default.


def maxima() -> dict[str, float]:
    """This machine's caps, from its own environment only."""
    return {key: _env_number(*spec) for key, spec in MAXIMA.items()}


def plain_command(command: Any) -> bool:
    """One simple command of plain words (the backend renders nothing else)."""
    words = str(command or "").split(" ")
    return bool(words) and all(
        _WORD.fullmatch(w) and ".." not in w and not w.startswith("/") and "=/" not in w and "//" not in w
        for w in words
    )


def clean_folder(raw: Any) -> str | None:
    """The program's folder under the worktree, or ``None`` when it is not plain."""
    text = raw.strip().strip("/") if isinstance(raw, str) else ""
    if not text:
        return ""
    parts = text.split("/")
    return text if all(_SEGMENT.fullmatch(p) and p != ".." for p in parts) else None


def artifacts_root() -> Path:
    configured = os.getenv("FABRIQUE_ARTIFACTS_ROOT", "").strip()
    return Path(configured).expanduser() if configured else Path.home() / ".fabrique" / "artifacts"


def prune(root: Path, now: float | None = None) -> int:
    """Remove run folders older than the retention; how many went."""
    days = _env_number("FABRIQUE_ARTIFACTS_RETENTION_DAYS", 14, 1, 3650)
    cutoff = (now or time.time()) - days * 86400
    removed = 0
    for scope in root.iterdir() if root.is_dir() else []:
        if not scope.is_dir() or scope.is_symlink():
            continue
        for run in scope.iterdir():
            try:
                if run.is_dir() and not run.is_symlink() and run.lstat().st_mtime < cutoff:
                    shutil.rmtree(run)
                    removed += 1
            except OSError:
                continue
    return removed


def free_mb(path: Path) -> float:
    return shutil.disk_usage(path).free / MIB


def folder_bytes(path: Path) -> int:
    """Bytes under *path*, never following a symlink."""
    total = 0
    for directory, _dirs, files in os.walk(path, followlinks=False):
        for name in files:
            try:
                total += os.lstat(os.path.join(directory, name)).st_size
            except OSError:
                continue
    return total


def manifest(path: Path) -> dict[str, Any]:
    """Each file under *path*: ``path``, ``bytes``, ``sha256`` read back now; a
    symlink is listed as ``link: true`` and never followed or hashed."""
    files: list[dict[str, Any]] = []
    total, truncated = 0, False
    for directory, dirs, names in os.walk(path, followlinks=False):
        for name in sorted(dirs):  # A linked folder is listed, never entered.
            full = Path(directory, name)
            if full.is_symlink():
                files.append({"path": full.relative_to(path).as_posix(), "link": True})
        for name in sorted(names):
            if len(files) >= MAX_MANIFEST:
                truncated = True
                break
            full = Path(directory, name)
            relative = full.relative_to(path).as_posix()
            if full.is_symlink():
                files.append({"path": relative, "link": True})
                continue
            if not full.is_file():
                continue
            digest = hashlib.sha256()
            size = 0
            try:
                with open(full, "rb") as handle:
                    for chunk in iter(lambda h=handle: h.read(MIB), b""):
                        digest.update(chunk)
                        size += len(chunk)
            except OSError as exc:
                files.append({"path": relative, "error": type(exc).__name__})
                continue
            total += size
            files.append({"path": relative, "bytes": size, "sha256": digest.hexdigest()})
    return {"files": files, "total_bytes": total, "truncated": truncated}


def metrics_file(path: Path, name: Any) -> dict[str, Any] | None:
    """The declared metrics file, whole, for the backend to hash and parse."""
    if not name:
        return None
    if not isinstance(name, str) or not _FILE.fullmatch(name):
        return {"name": str(name)[:64], "reason": "not a plain .json file name"}
    full = path / name
    if full.is_symlink() or not full.is_file():
        return {"name": name, "reason": f"{name} was not written as a regular file"}
    size = full.stat().st_size
    if size > METRICS_FILE_MAX:
        return {"name": name, "reason": f"{name} is over {METRICS_FILE_MAX} bytes"}
    return {"name": name, "content_b64": base64.b64encode(full.read_bytes()).decode("ascii")}


class Watch:
    """Kills container *name* once *path* holds more than *limit* bytes."""

    def __init__(self, runtime: str, name: str, path: Path, limit: int) -> None:
        self.killed, self.peak = False, 0
        self._stop = threading.Event()
        self._args = (runtime, name, path, limit)
        self._thread = threading.Thread(target=self._loop, daemon=True)
        self._thread.start()

    def _loop(self) -> None:
        runtime, name, path, limit = self._args
        while not self._stop.wait(WATCH_SECONDS):
            self.peak = max(self.peak, folder_bytes(path))
            if self.peak > limit:
                self.killed = True
                subprocess.run([runtime, "kill", name], capture_output=True, check=False)
                return

    def stop(self) -> None:
        self._stop.set()
        self._thread.join(timeout=5)


def _refused(error: str) -> dict[str, Any]:
    return {"success": False, "refused": True, "error": error}


def run(command: str, cwd: str, request: Any, timeout: Any, keep_full: bool = False) -> dict[str, Any]:
    """Run a declared program as the module docstring says; raises
    ``sandbox.SandboxUnavailable`` when no container can run it."""
    box = sandbox.normalize(request)  # Refuses host and bwrap for a program.
    spec = request.get("program") if isinstance(request.get("program"), dict) else {}
    if not plain_command(command):
        return _refused("a program's command must be plain words (letters, digits and _ . = : / + -)")
    folder = clean_folder(spec.get("folder"))
    if folder is None:
        return _refused("a program's folder must be a plain relative path")
    caps = maxima()
    seconds = int(min(max(_number(timeout, caps["seconds"]), 1), caps["seconds"]))
    box = {
        **box,
        "network": False,
        "cache": False,
        "cpus": min(box["cpus"], caps["cpus"]),
        "memory_mb": int(min(box["memory_mb"], caps["memory_mb"])),
        "pids_limit": int(min(box["pids_limit"], caps["pids_limit"])),
    }
    limit_mb = int(min(max(_number(spec.get("artifacts_mb"), caps["artifacts_mb"]), 1), caps["artifacts_mb"]))
    root = artifacts_root()
    root.mkdir(parents=True, exist_ok=True, mode=0o700)
    pruned = prune(root)
    floor = _env_number("FABRIQUE_ARTIFACTS_MIN_FREE_MB", 1024, 0, 10**9)
    free = free_mb(root)
    if free < floor:
        return _refused(f"only {free:.0f} MiB free under the artifacts root, below the floor of {floor:.0f} MiB")
    # One plain folder name per company: never "." or ".." (clean_scope allows dots).
    scope = re.sub(r"[^A-Za-z0-9_-]", "-", box.get("scope") or "default")
    run_id = uuid.uuid4().hex[:16]
    folder_path = root / scope / run_id
    folder_path.mkdir(parents=True, mode=0o700)
    runtime = sandbox.container_runtime()
    box, stack = sandbox.resolve_image(runtime, box, cwd)
    run_box, identity = sandbox_image.pinned(runtime, box)
    env = None
    if box["environment"]:
        env = sandbox_cache.prepare_env(runtime, box["image"], cwd, "test", box["scope"], sandbox.BASE_ENV["PATH"], run_box["image"])
    extra = ["--pull=never", "-v", f"{folder_path}:{ARTIFACTS_MOUNT}:rw", *(env or {}).get("args", [])]
    name = f"fabrique-program-{run_id[:12]}"
    argv = sandbox.container_command(runtime, run_box, command, cwd, name, extra, read_only=True, folder=folder)
    applied = sandbox.report(box, runtime)
    applied.update(workdir_mode="ro", purpose=sandbox.PROGRAM)
    if stack is not None:
        applied["stack_image"] = stack
    logger.info("Program (container): %s in %s", command, cwd)
    watch = Watch(runtime, name, folder_path, limit_mb * MIB)
    try:
        outcome = sandbox.run_bounded(argv, seconds, keep_full=keep_full)
    finally:
        watch.stop()
    if outcome["timed_out"]:
        subprocess.run([runtime, "kill", name], capture_output=True, check=False)
    spools = {run_logs.KEY: outcome.pop(run_logs.KEY)} if run_logs.KEY in outcome else {}
    applied["image_identity"] = sandbox_image.observed_after(runtime, box["image"], identity)
    if env is not None:
        applied["environment"] = sandbox_cache.finish_env(runtime, env, "test", outcome["exit_code"] == 0)
    if outcome["exit_code"] == sandbox.RUNTIME_FAILURE_EXIT and not watch.killed:
        run_logs.discard(spools)
        raise sandbox.SandboxUnavailable(
            f"{os.path.basename(runtime)} could not start the container: {outcome['stderr'].strip()[-500:] or 'no message'}"
        )
    listed = manifest(folder_path)
    program = {
        "run_id": run_id,
        "limits": {"seconds": seconds, "cpus": box["cpus"], "memory_mb": box["memory_mb"],
                   "pids_limit": box["pids_limit"], "artifacts_mb": limit_mb},
        "maxima": caps,
        "free_mb_before": round(free, 1),
        "pruned_runs": pruned,
        "stdout_truncated": outcome["output_truncated"],
        "artifacts": {**listed, "mount": ARTIFACTS_MOUNT, "limit_mb": limit_mb, "artifact_limit": "watch",
                      "killed_for_size": watch.killed, "peak_bytes": max(watch.peak, listed["total_bytes"])},
    }
    found = metrics_file(folder_path, spec.get("metrics_file"))
    if found is not None:
        program["metrics_file"] = found
    result: dict[str, Any] = {
        "success": outcome["exit_code"] == 0 and not watch.killed and not outcome["timed_out"],
        "stdout": outcome["stdout"],
        "stderr": outcome["stderr"],
        "output_truncated": outcome["output_truncated"],
        "exit_code": outcome["exit_code"],
        "sandbox": applied,
        "program": program,
        **spools,
    }
    if outcome["timed_out"]:
        result.update(timed_out=True, timeout_seconds=seconds, error=f"program timed out after {seconds}s")
    elif watch.killed:
        result["error"] = f"/artifacts passed {limit_mb} MiB and the run was killed"
    return result
