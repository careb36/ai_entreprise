"""
Requests to the workspace daemon about the user's own machine.

The backend runs in a container; the projects live on the developer's macOS,
Linux or Windows machine, where the workspace daemon runs.  Every question
about that machine's files — list a folder, inspect a project — is a NATS
request to the daemon.  Paths are the host's own absolute paths and are passed
through as opaque strings: the backend never resolves them locally.
"""

import asyncio
import contextlib
import json
import os
import tempfile
from collections.abc import Iterator
from pathlib import PurePosixPath
from typing import Any

from config import settings

from services.workspace import daemon_liveness, stack_profiles

STATUS_TIMEOUT_SECONDS = 3.0
BROWSE_TIMEOUT_SECONDS = 10.0
INSPECT_TIMEOUT_SECONDS = 30.0


DAEMON_UNAVAILABLE_DETAIL = (
    "The workspace daemon is not connected. Start it on the computer that has "
    "your projects: ./scripts/start-daemon.sh (macOS/Linux) or "
    ".\\scripts\\start-daemon.ps1 (Windows)."
)


class DaemonUnavailable(Exception):
    """No workspace daemon answered (not running, or NATS is down).

    ``absent`` is True only when NATS said that nobody listens on the subject
    ("no responders"): a daemon that is not running. A timeout or an
    unreachable NATS leaves it False: whether a daemon runs is then unknown.
    """

    def __init__(self, message: str, absent: bool = False):
        super().__init__(message)
        self.absent = absent


class DaemonIncompatible(DaemonUnavailable):
    """The daemon answers but speaks a protocol this backend does not.

    A ``DaemonUnavailable``, so every caller that handles a missing daemon
    handles this one too; its message is the reason (which side to update),
    never a generic timeout (``daemon_liveness.compatibility``).
    """


def _no_responders(exc: BaseException) -> bool:
    return (
        type(exc).__name__ == "NoRespondersError" or "no responders" in str(exc).lower()
    )


class DaemonError(Exception):
    """The daemon answered with an error; ``status`` follows HTTP."""

    def __init__(self, status: int, message: str):
        super().__init__(message)
        self.status = status
        self.message = message


async def daemon_request(
    nats_client: Any, subject: str, payload: dict[str, Any], timeout: float
) -> dict[str, Any]:
    if nats_client is None:
        raise DaemonUnavailable("NATS is not connected")
    incompatible = (
        None if subject == "workspace.status" else daemon_liveness.incompatibility()
    )
    if incompatible:
        raise DaemonIncompatible(incompatible)
    try:
        reply = await nats_client.request(subject, payload, timeout=timeout)
    except Exception as e:
        raise DaemonUnavailable(
            f"{type(e).__name__}: {e}", absent=_no_responders(e)
        ) from e
    if not isinstance(reply, dict):
        raise DaemonError(502, "The workspace daemon sent an unreadable reply")
    error = reply.get("error")
    if isinstance(error, dict):
        raise DaemonError(
            int(error.get("status") or 502),
            str(error.get("message") or "workspace daemon error"),
        )
    return reply


async def daemon_presence(nats_client: Any) -> dict[str, Any]:
    """Whether a daemon is ``connected``, ``absent``, ``unknown`` or ``incompatible``, and why.

    Unknown is not absent: when NATS cannot be reached, no reply arrives in
    time or the daemon answers with an error, the backend cannot tell whether
    a daemon runs, and says so rather than reporting it stopped. A daemon
    that answers with a protocol version outside ``SUPPORTED_PROTOCOL`` is
    ``incompatible``, with the reason; one that reports no version is an
    older daemon, still ``connected`` (``protocol.state: unknown``).
    """
    try:
        status = await daemon_request(
            nats_client, "workspace.status", {}, STATUS_TIMEOUT_SECONDS
        )
    except DaemonUnavailable as exc:
        if exc.absent:
            return {
                "state": "absent",
                "reason": "no daemon is listening",
                "status": None,
            }
        if nats_client is None:
            return {
                "state": "unknown",
                "reason": "the backend is not connected to NATS",
                "status": None,
            }
        if (
            isinstance(exc.__cause__, TimeoutError)
            or "timeout" in type(exc.__cause__).__name__.lower()
        ):
            reason = f"no answer within {STATUS_TIMEOUT_SECONDS:g} s"
        else:
            reason = str(exc)
        return {"state": "unknown", "reason": reason, "status": None}
    except DaemonError as exc:
        return {
            "state": "unknown",
            "reason": f"the daemon answered with an error: {exc.message}",
            "status": None,
        }
    daemon_liveness.observe_status(status)
    protocol = daemon_liveness.compatibility(status)
    if protocol["state"] == "incompatible":
        return {
            "state": "incompatible",
            "reason": protocol["reason"],
            "status": status,
            "protocol": protocol,
        }
    return {
        "state": "connected",
        "reason": None,
        "status": status,
        "protocol": protocol,
    }


async def inspect_project(
    nats_client: Any, path: str, snapshot: bool = False
) -> dict[str, Any]:
    return await daemon_request(
        nats_client,
        "workspace.inspect",
        {"path": path, "snapshot": snapshot},
        INSPECT_TIMEOUT_SECONDS,
    )


def _safe_relative(path: str) -> PurePosixPath | None:
    relative = PurePosixPath(path)
    if relative.is_absolute() or not relative.parts or ".." in relative.parts:
        return None
    return relative


@contextlib.contextmanager
def materialized_snapshot(snapshot: dict[str, Any]) -> Iterator[str]:
    """
    A temporary folder that mirrors the daemon's snapshot of a project.

    Every listed file exists (empty unless the daemon sent its text), so the
    filesystem-based stack and test detection run unchanged on a project that
    is on another machine.
    """
    files = snapshot.get("files") or []
    contents = snapshot.get("contents") or {}
    with tempfile.TemporaryDirectory(prefix="fabrique-snapshot-") as root:
        for path in files:
            relative = _safe_relative(path) if isinstance(path, str) else None
            if relative is None:
                continue
            target = os.path.join(root, *relative.parts)
            os.makedirs(os.path.dirname(target), exist_ok=True)
            text = contents.get(path)
            with open(target, "w", encoding="utf-8") as fh:
                if isinstance(text, str):
                    fh.write(text)
        yield root


def without_snapshot(project: dict[str, Any]) -> dict[str, Any]:
    return {key: value for key, value in project.items() if key != "snapshot"}


# How much of a command's output is kept as evidence. The daemon's own timeout
# can leave a long log; the tail is what explains a failure, and the byte count
# lets the verifier say the tail is a tail.
TEST_OUTPUT_TAIL_CHARS = 2000
STDERR_HEADING = "[the end of stderr]\n"
STDOUT_HEADING = "\n[the end of stdout]\n"


def output_tail(stdout: str | None, stderr: str | None, limit: int) -> str:
    """The end of a command's output in at most *limit* characters, the end of
    stdout last and given most of the room.

    A test runner prints its result on stdout (pytest's failed tests and
    count) and its warnings on stderr. The tail of ``stdout + stderr`` was
    stderr alone whenever stderr was long: in sprint 4 (2026-11-23) every
    refuted run showed pytest-asyncio's deprecation warning and never which
    test failed. Output that fits is kept whole, as before; otherwise stderr
    keeps up to a quarter of the room and stdout the rest."""
    stdout, stderr = stdout or "", stderr or ""
    if len(stdout) + len(stderr) <= limit:
        return stdout + stderr
    if not stderr.strip():
        return stdout[-limit:]
    if not stdout.strip():
        return stderr[-limit:]
    room = limit - len(STDERR_HEADING) - len(STDOUT_HEADING)
    out_room = min(len(stdout), room - min(len(stderr), room // 4))
    err_room = room - out_room
    return f"{STDERR_HEADING}{stderr[-err_room:]}{STDOUT_HEADING}{stdout[-out_room:]}"


def record_test_run(evidence: dict[str, Any], command: str, result: Any) -> None:
    """Store a ``workspace.execute`` reply as test evidence.

    Keeps the command, exit code and output tail in ``test_run``; restates the
    conclusion in the shape ``check_claimed_tests_evidenced`` reads in ``ci``.
    The exit status comes from the daemon, never from the agent.

    A command the daemon killed keeps ``timed_out`` and the output captured
    before the kill: "it hung" is a result, and the verifier refutes it rather
    than leaving the claim unsettled.
    """
    reply = result if isinstance(result, dict) else {}
    exit_code = reply.get("exit_code")
    timed_out = bool(reply.get("timed_out"))
    output = f"{reply.get('stdout') or ''}{reply.get('stderr') or ''}"
    test_run: dict[str, Any] = {
        "command": command,
        "exit_code": exit_code,
        "output_tail": output_tail(
            reply.get("stdout"), reply.get("stderr"), TEST_OUTPUT_TAIL_CHARS
        ),
        "output_bytes": len(output),
        "output_truncated": len(output) > TEST_OUTPUT_TAIL_CHARS,
        "timed_out": timed_out,
        "reason": reply.get("error"),
    }
    if isinstance(reply.get("sandbox"), dict):
        # Where the daemon ran it (services/sandbox.py): its own report.
        test_run["sandbox"] = reply["sandbox"]
    if isinstance(reply.get("duration_ms"), int):
        # The daemon's clock around the command (issue #766).
        test_run["duration_ms"] = reply["duration_ms"]
    if timed_out:
        test_run["timeout_seconds"] = reply.get("timeout_seconds")
        conclusion = "timed_out"
    elif isinstance(exit_code, int):
        conclusion = "success" if exit_code == 0 else "failure"
    else:
        evidence["test_run"] = test_run
        return
    evidence["test_run"] = test_run
    evidence["ci"] = {
        "source": "workspace daemon",
        "ref": command,
        "conclusion": conclusion,
    }


_NPM_DEFAULT_TEST = stack_profiles.NPM_DEFAULT_TEST


async def detect_project_test_command(
    nats_client: Any, workspace_root: str
) -> str | None:
    """``detect_test_command`` on the project's machine.

    The project lives where the daemon runs, so detection reads the daemon's
    snapshot of it; a folder this server can see itself is the fallback.
    """
    if settings.DELIVERY_TEST_COMMAND.strip():
        return settings.DELIVERY_TEST_COMMAND.strip()
    try:
        project = await inspect_project(nats_client, workspace_root, snapshot=True)
    except (DaemonUnavailable, DaemonError):
        project = None
    if project and project.get("is_dir"):

        def _detect_in_snapshot() -> str | None:
            with materialized_snapshot(project.get("snapshot") or {}) as mirror:
                return detect_test_command(mirror)

        return await asyncio.to_thread(_detect_in_snapshot)
    return await asyncio.to_thread(detect_test_command, workspace_root)


def detect_test_command(workspace_root: str) -> str | None:
    """
    The command that runs this project's tests, or ``None``.

    ``DELIVERY_TEST_COMMAND`` wins; otherwise an npm ``test`` script (not npm's
    placeholder) means ``npm test``, and pytest configuration or a ``tests/``
    folder with ``test_*.py`` files means ``python -m pytest``.
    """
    if settings.DELIVERY_TEST_COMMAND.strip():
        return settings.DELIVERY_TEST_COMMAND.strip()
    root = os.path.abspath(workspace_root)
    package_json = os.path.join(root, "package.json")
    if os.path.isfile(package_json):
        try:
            with open(package_json, encoding="utf-8") as fh:
                script = (json.load(fh).get("scripts") or {}).get("test")
        except (OSError, ValueError, AttributeError):
            script = None
        if isinstance(script, str) and script.strip() and script != _NPM_DEFAULT_TEST:
            return "npm test --silent"
    for name, marker in (
        ("pytest.ini", None),
        ("pyproject.toml", "[tool.pytest"),
        ("setup.cfg", "[tool:pytest"),
    ):
        path = os.path.join(root, name)
        if not os.path.isfile(path):
            continue
        if marker is None:
            return "python -m pytest -q"
        try:
            with open(path, encoding="utf-8") as fh:
                if marker in fh.read():
                    return "python -m pytest -q"
        except OSError:
            continue
    tests_dir = os.path.join(root, "tests")
    if os.path.isdir(tests_dir) and any(
        name.startswith("test_") and name.endswith(".py")
        for _dir, _subdirs, names in os.walk(tests_dir)
        for name in names
    ):
        return "python -m pytest -q"

    # Go.
    if os.path.isfile(os.path.join(root, "go.mod")):
        return "go test ./..."
    # Rust.
    if os.path.isfile(os.path.join(root, "Cargo.toml")):
        return "cargo test"
    # Java (Maven / Gradle).
    if os.path.isfile(os.path.join(root, "pom.xml")):
        return "mvn -q test"
    if os.path.isfile(os.path.join(root, "build.gradle")) or os.path.isfile(
        os.path.join(root, "build.gradle.kts")
    ):
        return "gradle test"
    # Ruby.
    if os.path.isfile(os.path.join(root, "Gemfile")) and os.path.isdir(
        os.path.join(root, "spec")
    ):
        return "bundle exec rspec"
    return None


def detect_install_command(workspace_root: str) -> str | None:
    """The command that installs this project's dependencies, or None."""
    root = os.path.abspath(workspace_root)
    if os.path.isfile(os.path.join(root, "package.json")):
        return "npm install"
    if os.path.isfile(os.path.join(root, "requirements.txt")):
        return "pip install -r requirements.txt"
    pyproject = os.path.join(root, "pyproject.toml")
    if os.path.isfile(pyproject):
        try:
            with open(pyproject, encoding="utf-8", errors="replace") as fh:
                text = fh.read()
        except OSError:
            text = ""
        # A Poetry project installs its dependencies with Poetry, whether it
        # is a package or not (``package-mode = false`` cannot be built by
        # pip); an editable pip install is for a PEP 621 package.
        if "[tool.poetry]" in text:
            return "poetry install"
        if "[project]" in text:
            return "pip install -e ."
    if os.path.isfile(os.path.join(root, "Cargo.toml")):
        return "cargo build"
    if os.path.isfile(os.path.join(root, "go.mod")):
        return "go mod download"
    if os.path.isfile(os.path.join(root, "pom.xml")):
        return "mvn -q dependency:resolve"
    if os.path.isfile(os.path.join(root, "build.gradle")) or os.path.isfile(
        os.path.join(root, "build.gradle.kts")
    ):
        return "gradle testClasses"
    if os.path.isfile(os.path.join(root, "Gemfile")):
        return "bundle install"
    return None
