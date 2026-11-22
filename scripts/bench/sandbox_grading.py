"""Grading candidate code in the daemon's sandbox (docs/design/rsi-loop.md,
PR 12; the threat model is docs/archive/design/SANDBOX_GRADING_THREAT_MODEL.md).

The self-hosting harness's target measure (``scripts/self_host/spec_source.py``
``run_measure``) and the bench's acceptance tests (``bench_grade.grade``) run
person-written tests that import code the factory produced. That code is the
party being measured, so it runs here in the same container sandbox the
workspace daemon uses for agent commands (``vscode-daemon/sandbox.py``: its
``normalize``, ``container_runtime``, ``container_command``, ``run_bounded``,
``report`` and ``sandbox_image.pinned``), never in the runner's own Python:

- network off, every capability dropped, no privilege escalation, CPU,
  memory and process limits, the host user's uid, an environment of the
  daemon's base variables only (no ``PYTHONPATH``, ``PYTEST_ADDOPTS`` or
  anything else of the runner's or the candidate's);
- the root file system read-only (``--read-only``), the candidate's tree
  mounted read-only at ``/candidate``, the graders (from the pinned commit)
  read-only at ``/graders``, a fresh empty scratch at ``/workspace`` and the
  daemon's ``/tmp`` tmpfs the only places it can write;
- an image already on the machine, run from its content ID and never pulled
  while grading (``--pull=never``): the operator builds it (docs/OPERATIONS.md);
- a wall-clock timeout, after which the container is killed.

The verdict is read from outside: the container's exit status as the
runtime reports it, and pytest's JUnit report printed on standard output by
the runner's own wrapper after pytest has exited and after every other
process in the container was killed (``FRAME``). The runner never reads a
file the candidate could write. The report must be the last thing on the
output, carry this run's nonce and the same exit status, or nothing is read.

Every way this can go wrong reads as ``unmeasured`` with a reason, never as
passed: no runtime, a runtime that cannot start the container, no image, a
mode without limits (``bwrap``), a timeout, a kill (the memory limit), an
exit status pytest does not give for a finished run, a missing or
inconsistent report. What it does not stop: the code under test runs inside
the grading pytest process, so code written to forge pytest's result from
within (``os._exit(0)`` after rewriting the report in-process) is caught
only by the consistency checks; the threat model records it as residual.

Taken from (design only): OpenHands' Docker runtime per session (an
ephemeral container, the workspace mounted, resource limits) and OpenAI
Codex cloud's sandboxed runs (network off, the command and its output cited
by the runner).
"""

from __future__ import annotations

import base64
import binascii
import hashlib
import importlib
import inspect
import os
import secrets
import shlex
import subprocess
import sys
import uuid
from pathlib import Path
from typing import Any

sys.path.insert(0, str(Path(__file__).resolve().parent))
import graders
from junit_xml import FAIL as CASE_FAILED
from junit_xml import SKIP as CASE_SKIPPED
from junit_xml import JUnitError, outcomes

SANDBOX, IN_PROCESS = "sandbox", "in_process"
PASS, FAIL, UNMEASURED = "pass", "fail", "unmeasured"
IMAGE_ENV = "FABRIQUE_GRADING_IMAGE"
DAEMON_DIR_ENV = "FABRIQUE_DAEMON_DIR"
# The digest ``graders.py daemon`` computed for the copy it took from B: when
# set, the folder must match it before anything in it is imported.
DAEMON_SHA256_ENV = "FABRIQUE_DAEMON_SHA256"
# The writable scratch at /workspace: a tmpfs of this size (charged to the
# memory limit, like the /tmp tmpfs), never a folder of the host's disk.
SCRATCH_MB = 256
# How long the runtime may take to kill a container that timed out.
KILL_SECONDS = 30
# Free space (KiB) under which /workspace or /tmp counts as filled up.
FULL_KIB = 1024
CANDIDATE, GRADERS = "/candidate", "/graders"
LIMITS = {"cpus": 2.0, "memory_mb": 2048, "pids_limit": 256}
FRAME = "@@FABRIQUE-GRADE"
TAIL_LINES = 30
IN_PROCESS_WARNING = (
    "graded in-process: the candidate's code ran in the runner's own Python, "
    "with the runner's files, network and environment; for local development "
    "only, never a measurement for a cycle"
)


class Unavailable(RuntimeError):
    """The sandbox cannot grade here; the message says why."""


def daemon_sandbox():
    """The daemon's ``sandbox`` module, from ``FABRIQUE_DAEMON_DIR`` or this
    repository's ``vscode-daemon/``. With ``FABRIQUE_DAEMON_SHA256`` (what
    ``run.sh --graders`` sets for the copy ``graders.py daemon`` took from B's
    objects) the folder's ``graders.daemon_digest`` must match before anything
    is imported. The modules are imported from that folder alone, under their
    own names only while loading (``sandbox``, ``sandbox_image``,
    ``sandbox_cache``, ``run_logs``): a module of the same name loaded earlier
    from elsewhere never stands in, and none is left in ``sys.modules``."""
    configured = os.environ.get(DAEMON_DIR_ENV, "").strip()
    folder = (
        Path(configured)
        if configured
        else Path(__file__).resolve().parents[2] / "vscode-daemon"
    ).resolve()
    if not (folder / "sandbox.py").is_file():
        raise Unavailable(
            f"the daemon's sandbox (sandbox.py) is not in {folder}; set {DAEMON_DIR_ENV}"
        )
    expected = os.environ.get(DAEMON_SHA256_ENV, "").strip()
    if expected and graders.daemon_digest(folder) != expected:
        raise Unavailable(
            f"the daemon in {folder} does not match its sha256 {expected}: not loaded"
        )
    names = [n.removesuffix(".py") for n in graders.DAEMON_FILES]
    saved = {n: sys.modules.pop(n) for n in names if n in sys.modules}
    sys.path.insert(0, str(folder))
    try:
        sandbox = importlib.import_module("sandbox")
        strays = [
            n
            for n in names
            if n in sys.modules
            and Path(getattr(sys.modules[n], "__file__", "") or "").parent != folder
        ]
    finally:
        sys.path.remove(str(folder))
        for n in names:
            sys.modules.pop(n, None)
        sys.modules.update(saved)
    if strays:
        raise Unavailable(f"{', '.join(strays)} did not come from {folder}")
    return sandbox


def wrapper(
    argv: list[str], cwd: str, nonce: str, env: dict[str, str] | None = None
) -> str:
    """The shell the container runs: pytest with its JUnit report in a fresh
    folder of ``/tmp``, then, as PID 1 of the container, every other process
    killed, then the frame: the exit status, the nonce and the report in
    base64. Nothing the candidate passes reaches this text."""
    exports = "".join(f"export {k}={shlex.quote(v)}\n" for k, v in (env or {}).items())
    return (
        f"cd {shlex.quote(cwd)} || exit 97\n"
        "unset PYTEST_ADDOPTS PYTEST_PLUGINS PYTHONPATH PYTHONSTARTUP PYTHONHOME\n"
        "export PYTEST_DISABLE_PLUGIN_AUTOLOAD=1 PYTHONDONTWRITEBYTECODE=1\n"
        f"{exports}"
        "r=$(mktemp -d /tmp/fabrique-grade.XXXXXXXX) || exit 98\n"
        f'{shlex.join(argv)} "--junitxml=$r/report.xml" </dev/null\n'
        "code=$?\n"
        'if [ "$$" = 1 ]; then kill -9 -1 2>/dev/null; kill -9 -1 2>/dev/null; fi\n'
        # Filled up: /workspace or /tmp with less than FULL_KIB free (or
        # unreadable) after the run; the runner reads it as unmeasured.
        "full=0\n"
        "for d in /workspace /tmp; do\n"
        '  [ -d "$d" ] || continue\n'
        "  a=$(df -Pk \"$d\" 2>/dev/null | awk 'NR==2 {print $4}')\n"
        '  case "$a" in ""|*[!0-9]*) full=1 ;; '
        f'*) [ "$a" -lt {FULL_KIB} ] && full=1 ;; esac\n'
        "done\n"
        f"printf '\\n{FRAME} %s %s %s\\n' \"$code\" {nonce} \"$full\"\n"
        'base64 "$r/report.xml" 2>/dev/null\n'
        f"printf '{FRAME}-END %s\\n' {nonce}\n"
        'rm -rf "$r"\n'
        'exit "$code"\n'
    )


def read_frame(
    stdout: str, nonce: str, exit_code: int | None
) -> tuple[str | None, str | None]:
    """``(report, None)`` from the last frame of *stdout*, or ``(None, why)``.
    The frame must be the last thing printed, carry *nonce* and the exit
    status the runtime reported."""
    start = stdout.rfind(f"\n{FRAME} ")
    if start < 0:
        return None, "the run printed no report frame"
    head, _, rest = stdout[start + 1 :].partition("\n")
    parts = head.split()
    end = f"{FRAME}-END {nonce}"
    body, found, after = rest.partition(end)
    if len(parts) != 4 or parts[2] != nonce or not found or after.strip():
        return None, "the report frame is not this run's, or is not the last output"
    if parts[1] != str(exit_code):
        return None, f"the frame says exit {parts[1]}, the runtime {exit_code}"
    if parts[3] != "0":
        return None, (
            f"the scratch (/workspace, {SCRATCH_MB} MB) or /tmp filled up: "
            "the disk limit, not a result"
        )
    try:
        report = base64.b64decode("".join(body.split()), validate=True).decode("utf-8")
    except (binascii.Error, UnicodeDecodeError):
        return None, "the report frame could not be decoded"
    return (report, None) if report.strip() else (None, "pytest wrote no report")


def unavailable(reason: str, sandbox: dict[str, Any] | None = None) -> dict[str, Any]:
    """A run that never happened: no exit status, the reason."""
    return {
        "ran": False,
        "reason": reason,
        "exit_code": None,
        "timed_out": False,
        "stdout": "",
        "stderr": "",
        "report": None,
        "sandbox": sandbox,
    }


def run_pytest(
    argv: list[str],
    *,
    image: str | None,
    mounts: dict[str, Path],
    cwd: str,
    timeout: float,
    env: dict[str, str] | None = None,
    mode: str = "container",
    limits: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """Run pytest (*argv*, the interpreter as the image names it) in the
    sandbox with *mounts* (container path → host folder, each read-only) and
    read its exit status and report from outside. Returns ``ran``,
    ``reason`` (why the result cannot be read, else None), ``exit_code``,
    ``timed_out``, ``stdout``, ``stderr``, ``report`` (the JUnit XML or None)
    and ``sandbox`` (what was applied, observed here)."""
    if mode != "container":
        return unavailable(
            f"grading needs the container sandbox; {mode!r} applies no CPU or memory limit"
        )
    if not image:
        return unavailable(f"no grading image named (--grading-image or {IMAGE_ENV})")
    try:
        sandbox = daemon_sandbox()
        box = sandbox.normalize(
            {
                "mode": "container",
                "image": image,
                "network": False,
                "env": [],
                "purpose": "check",
                **LIMITS,
                **(limits or {}),
            }
        )
        if "scratch_mb" not in inspect.signature(sandbox.container_command).parameters:
            raise Unavailable(
                "the daemon's sandbox.py cannot give a bounded scratch (no scratch_mb)"
            )
        runtime = sandbox.container_runtime()
    except (Unavailable, RuntimeError, ValueError) as exc:
        return unavailable(str(exc))
    run_box, identity = sandbox.sandbox_image.pinned(runtime, box)
    applied = sandbox.report(box, runtime)
    applied.update(
        {
            "image_identity": identity,
            "read_only_root": True,
            "pull": "never",
            "mounts": {path: "ro" for path in mounts},
            "scratch": f"{sandbox.CONTAINER_WORKDIR} tmpfs {SCRATCH_MB} MB",
        }
    )
    if not identity.get("id"):
        return unavailable(
            f"the grading image is not on this machine and is never pulled while grading: {identity.get('reason')}",
            applied,
        )
    nonce = secrets.token_hex(16)
    extra = ["--read-only", "--pull=never"]
    for path, host in mounts.items():
        extra += ["-v", f"{Path(host).resolve()}:{path}:ro"]
    name = f"fabrique-grade-{uuid.uuid4().hex[:12]}"
    # No folder of the host is writable: /workspace is a bounded tmpfs.
    command = sandbox.container_command(
        runtime,
        run_box,
        wrapper(argv, cwd, nonce, env),
        "/nonexistent",
        name,
        extra,
        scratch_mb=SCRATCH_MB,
    )
    run = sandbox.run_bounded(command, timeout, own_group=True)
    if run["timed_out"]:
        return {
            **unavailable(f"stopped after {timeout:g} s (the time limit)", applied),
            "ran": True,
            "timed_out": True,
            "stdout": run["stdout"],
            "stderr": run["stderr"],
            "cleanup": kill(runtime, name),
        }
    report, why = read_frame(run["stdout"], nonce, run["exit_code"])
    if report is None and run["exit_code"] == sandbox.RUNTIME_FAILURE_EXIT:
        tail = run["stderr"].strip()[-500:] or "no message"
        return unavailable(
            f"{os.path.basename(runtime)} could not start the container: {tail}",
            applied,
        )
    return {
        "ran": True,
        "reason": why,
        "exit_code": run["exit_code"],
        "timed_out": False,
        "stdout": run["stdout"],
        "stderr": run["stderr"],
        "report": report,
        "sandbox": applied,
    }


def kill(runtime: str, name: str) -> str | None:
    """Kill the container *name* within ``KILL_SECONDS``; None when the
    runtime said it did, else what went wrong (recorded, never raised: the
    run stays unmeasured either way)."""
    tool = os.path.basename(runtime)
    try:
        done = subprocess.run(
            [runtime, "kill", name],
            capture_output=True,
            text=True,
            timeout=KILL_SECONDS,
            check=False,
        )
    except subprocess.TimeoutExpired:
        return f"{tool} kill {name} did not answer within {KILL_SECONDS} s"
    except OSError as exc:
        return f"{tool} kill {name} could not run: {exc}"
    if done.returncode != 0:
        said = (done.stderr or "").strip()[-200:] or f"exit {done.returncode}"
        return f"{tool} kill {name} failed: {said}"
    return None


def measure_verdict(run: dict[str, Any]) -> tuple[str, str | None]:
    """``pass``, ``fail`` or ``unmeasured`` (with why) for a target measure.

    ``pass``: exit status 0 and a report whose every test passed. ``fail``:
    exit status 1 or 2 (a test failed, or one could not be collected) and a
    report naming a test that did not pass, or exit 0 with a test skipped.
    Anything else is ``unmeasured``: never read as a pass."""
    code = run.get("exit_code")
    if not run.get("ran") or run.get("timed_out"):
        return UNMEASURED, run.get("reason") or "the measure did not run"
    if code not in (0, 1, 2):
        killed = (
            " (killed: the memory or process limit, or a signal)"
            if code in (137, -9)
            else ""
        )
        return UNMEASURED, f"exit status {code}{killed}: pytest did not finish a run"
    if run.get("report") is None:
        return UNMEASURED, run.get("reason") or "no report"
    try:
        cases = outcomes(run["report"])
    except JUnitError as exc:
        return UNMEASURED, f"the report could not be read: {exc}"
    if not cases:
        return UNMEASURED, "the report names no test"
    failed = sum(1 for _, o in cases if o == CASE_FAILED)
    skipped = sum(1 for _, o in cases if o == CASE_SKIPPED)
    if (code == 0) == (failed > 0):  # pytest exits 0 only with nothing failed
        return UNMEASURED, f"exit status {code} and a report with {failed} failed disagree"
    if failed:
        return FAIL, f"{failed} of {len(cases)} test(s) failed"
    if skipped:
        return FAIL, f"{skipped} of {len(cases)} test(s) skipped: the measure did not pass"
    if code == 0:
        return PASS, None
    return (
        UNMEASURED,
        f"exit status {code} and a report where every test passed disagree",
    )


def output_record(run: dict[str, Any]) -> dict[str, Any]:
    """The output's sha256 (computed here) and its tail."""
    output = (run.get("stdout") or "") + (run.get("stderr") or "")
    return {
        "output_sha256": hashlib.sha256(output.encode("utf-8")).hexdigest(),
        "tail": output.strip().splitlines()[-TAIL_LINES:],
    }
