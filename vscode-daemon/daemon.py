"""
Fabrique workspace daemon.

Runs natively on the developer's machine (macOS, Linux or Windows) — the one
where the project was cloned — and is the only Fabrique component that touches
that machine's files.  The backend reaches it over NATS:

- workspace.generate:     write files (request/reply, per-file sha256 of the bytes on disk
                          and the diff against what was there, file_diff.py); with a
                          `git` spec, as a commit on the task's branch (git_workspace.py)
- workspace.execute:      run a shell command in the project and return its output
- workspace.capabilities: the console assistant CLIs found on PATH
- workspace.status:       which machine this is (hostname, platform, home)
- workspace.browse:       list the sub-folders of a folder, for the folder picker
- workspace.inspect:      validate a project folder: git remote/branch, optional file snapshot
- workspace.hash:         sha256 of named files as they are on disk (file_hash.py)
- workspace.list / read / search / find, workspace.edit / edit.finish: the Developer's tools (workspace_read.py, workspace_edit.py)
- workspace.git.publish:  push a task's branch and open its pull/merge request (gh, glab)
- workspace.git.reviews:  the comments people left on that request (platform_reviews.py)
- workspace.deliver:      (JetStream) files queued while this daemon was offline;
                          the result goes to workspace.delivered

Start it with scripts/start-daemon.sh (macOS/Linux) or .ps1 (Windows); it reconnects to NATS on its own.
"""

import asyncio
import hashlib
import json
import logging
import os
import platform
import shutil
import stat
import string
import subprocess
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional

import delivery_journal
import file_diff
import file_edits
import file_hash
import git_workspace
from git_workspace import git_info, remote_web_url  # noqa: F401 - re-exported for callers of daemon.py
import nats_connect
import nats_scope
import platform_reviews
import sprint_integration
import shell_exec
import workspace_edit, workspace_read  # noqa: E401 - one line: this file may only shrink.
from shell_exec import COMMAND_TIMEOUT_SECONDS, execute_shell_command
_build_shell_command = shell_exec.build_shell_command  # An old private name; the logic is in shell_exec.
from nats.errors import TimeoutError as NATSTimeoutError

logging.basicConfig(level=logging.INFO, format='%(asctime)s - %(name)s - %(levelname)s - %(message)s')
logger = logging.getLogger("fabrique-daemon")

DAEMON_VERSION = "2026.10"
NATS_URL = os.getenv("NATS_URL", "nats://localhost:4222")
WORKSPACE_ROOT_DEFAULT = os.getenv("WORKSPACE_ROOT", os.path.expanduser("~/factory-workspaces"))
IS_WINDOWS = os.name == "nt"
PROTECTED_ROOT_DOCS = set()
# Console assistant commands reported by workspace.capabilities when the
# request does not name its own list.
ASSISTANT_COMMANDS_DEFAULT = os.getenv("CONSOLE_ASSISTANT_COMMANDS", "copilot,claude")
# Off-PATH install locations the backend's command wrapper falls back to.
ASSISTANT_FALLBACK_PATHS = {
    "copilot": "~/Library/Application Support/Code/User/globalStorage/github.copilot-chat/copilotCli/copilot",
}
# Every daemon instance answers in this queue group, so a request is handled once.
QUEUE_GROUP = "workspace-daemon"

# Queued-delivery streams: declared by the backend (nats_client.py), namespaced by nats_scope.


def confirm_workspace_path() -> str:
    """Ensure the default workspace root directory exists and return its path."""
    Path(WORKSPACE_ROOT_DEFAULT).mkdir(parents=True, exist_ok=True)
    return WORKSPACE_ROOT_DEFAULT


VCS_DIRS = frozenset({".git", ".svn", ".hg"})  # A hook there would run on the next commit.


def _resolve_target_path(workspace_path: str, relative_path: str) -> Optional[Path]:
    """Resolve a path under the workspace root, never into VCS metadata (asked or via a symlink)."""
    root = Path(workspace_path).resolve()
    candidate = (root / (relative_path or "")).resolve()
    try:
        parts = candidate.relative_to(root).parts + Path(relative_path or "").parts
    except ValueError:
        return None
    return None if VCS_DIRS.intersection(parts) else candidate


def _is_protected_existing_doc(workspace_root: Path, target_path: Path) -> bool:
    """Protect curated root docs from blind overwrite by agent output."""
    return (
        target_path.is_file()
        and target_path.parent == workspace_root
        and target_path.name in PROTECTED_ROOT_DOCS
    )


def write_files(workspace_path: str, files: list) -> Dict[str, Any]:
    """
    Write *files* (``path`` with ``content``, or ``edits``: ``file_edits.py``) under the workspace.

    One report per file: ``status`` (``written``, ``skipped_protected``,
    ``rejected``, ``rejected_edit`` or ``rejected_syntax`` with ``error``) and,
    when written, ``bytes`` and the ``sha256`` read back from disk: an observation.
    """
    results = []
    workspace_root = Path(workspace_path).expanduser()
    if not workspace_root.is_absolute() or not workspace_root.is_dir():
        # The project is not on this machine (wrong host, or the folder was
        # moved): never create it, and say so for every file.
        logger.warning("Workspace %s is not a folder on this machine", workspace_path)
        return [
            {"path": file_info.get("path", ""), "status": "workspace_missing"}
            for file_info in files
        ]
    workspace_root = workspace_root.resolve()
    for file_info in files:
        requested = file_info.get("path", "")
        target_path = _resolve_target_path(workspace_path, requested)
        overwrite_existing = bool(file_info.get("overwrite_existing") or file_info.get("safe_replace"))

        if not target_path or target_path == workspace_root:
            results.append({"path": requested, "status": "rejected"})
            logger.warning("Rejected unsafe file path: %s", requested)
            continue

        if _is_protected_existing_doc(workspace_root, target_path) and not overwrite_existing:
            results.append({"path": requested, "status": "skipped_protected", "absolute_path": str(target_path)})
            logger.warning("Skipped protected document overwrite: %s", target_path)
            continue

        content, extra = file_edits.resolve_content(target_path, file_info)
        if content is None:  # A refused edit or a file that does not parse.
            results.append({"path": requested, "absolute_path": str(target_path), **extra})
            continue
        os.makedirs(target_path.parent, exist_ok=True)
        before = file_diff.read_previous(target_path)
        with open(target_path, "w", encoding="utf-8", newline="") as f:
            f.write(content)
        on_disk = target_path.read_bytes()
        results.append({
            "path": requested,
            "status": "written",
            "absolute_path": str(target_path),
            "bytes": len(on_disk),
            "sha256": hashlib.sha256(on_disk).hexdigest(),
            **file_diff.describe_change(requested, before, on_disk),
            **extra,
        })
        logger.info(f"File written: {target_path}")
    return file_diff.cap_reply(results)


def deliver_files(workspace_path: str, files: list, git: Optional[Dict[str, Any]] = None) -> Dict[str, Any]:
    """``write_files`` wrapped in the reply shape the backend reads; a commit on the task's branch when *git* names it."""
    try:
        if git:
            return git_workspace.deliver(workspace_path, git, files, write_files)
        write_results = write_files(workspace_path, files)
        return {
            "ok": all(r["status"] == "written" for r in write_results),
            "workspace_root": workspace_path,
            "files": write_results,
        }
    except (OSError, git_workspace.GitError) as e:
        logger.error("Writing files under %s failed: %s", workspace_path, e)
        return {"ok": False, "workspace_root": workspace_path, "files": [], "error": str(e)}


def _delivery_summary(response: Dict[str, Any]) -> str:
    written = [r["path"] for r in response["files"] if r["status"] == "written"]
    others = [f"{r['path']} ({r['status']})" for r in response["files"] if r["status"] != "written"]
    summary = f"📝 Wrote {len(written)} file(s): {', '.join(written) or '—'}"
    if others:
        summary += f"; not written: {', '.join(others)}"
    if response.get("error"):
        summary += f"; error: {response['error']}"
    return summary


async def _publish_glassroom_status(nc, sprint_id, content: str) -> None:
    await nc.publish(f"glassroom.{sprint_id}", json.dumps({
        "agent": "System",
        "timestamp": datetime.now(timezone.utc).isoformat(),
        "content": content,
        "type": "status",
    }).encode())


async def handle_msg(nc, msg):
    """
    Unified NATS message handler for ``workspace.generate`` and ``workspace.execute``.

    ``workspace.generate`` flow:
    1. Write all files provided in the payload to the workspace directory.
    2. Reply with the per-file result and post a Glassroom summary.

    ``workspace.execute`` flow:
    1. Run the requested shell command inside the workspace directory.
    2. Publish the command output to the Glassroom NATS subject so the frontend
       SSE stream receives it in real-time.
    3. Optionally reply with the full result dict.

    File writes and commands run in a worker thread, so a long test run does
    not stop this daemon from answering other requests.
    """
    subject = msg.subject
    data = json.loads(msg.data.decode())
    reply_to = msg.reply

    workspace_path = data.get("workspace_root") or confirm_workspace_path()

    if subject == "workspace.generate":
        files = data.get("files", [])
        if not files:
            # Sprint kickoff stories also travel on this subject; they are for
            # the backend's agent graph, not for this daemon.
            return
        response = await asyncio.to_thread(deliver_files, workspace_path, files, data.get("git"))
        if reply_to:
            await nc.publish(reply_to, json.dumps(response).encode())

        sprint_id = data.get("sprint_id")
        if sprint_id is not None:
            await _publish_glassroom_status(nc, sprint_id, _delivery_summary(response))

    elif subject == "workspace.execute":
        cmd = data.get("command")
        result = await asyncio.to_thread(execute_shell_command, cmd, workspace_path, data.get("sandbox"), data.get("command_policy"))

        sprint_id = data.get("sprint_id")  # none: the Developer's test tool, reported to nobody
        if sprint_id is not None and not shell_exec.is_assistant_command(cmd):
            await _publish_glassroom_status(nc, sprint_id, shell_exec.summarize_command_result(cmd, result))

        if reply_to:
            await nc.publish(reply_to, json.dumps(result).encode())

def _configured_assistant_commands(data: Dict[str, Any]) -> list[str]:
    """Assistant commands to look for: the request's list, else the env default."""
    requested = data.get("commands")
    if isinstance(requested, list) and requested:
        names = [str(name).strip() for name in requested]
    else:
        names = [name.strip() for name in ASSISTANT_COMMANDS_DEFAULT.split(",")]
    # Only bare command names: a path or shell syntax is not an assistant name.
    return [name for name in names if name and os.path.basename(name) == name]


def _has_fallback_binary(name: str) -> bool:
    """Match the host fallback paths the backend's command wrapper also tries."""
    fallback = ASSISTANT_FALLBACK_PATHS.get(name)
    if not fallback:
        return False
    path = os.path.expanduser(fallback)
    return os.path.isfile(path) and os.access(path, os.X_OK)


def detect_assistants(commands: list[str]) -> list[str]:
    """Return the assistant commands found on the PATH used for command execution."""
    search_path = shell_exec.build_command_env().get("PATH")
    return [
        name
        for name in commands
        if shutil.which(name, path=search_path) or _has_fallback_binary(name)
    ]


async def handle_capabilities(nc, msg):
    """
    Reply to ``workspace.capabilities`` with the console assistants this host has.

    Reply body: ``{"assistants": [<command names found via shutil.which>]}``.
    The backend asks once when its workers start and skips console assistants
    entirely when the list is empty.
    """
    try:
        data = json.loads(msg.data.decode() or "{}")
    except (ValueError, UnicodeDecodeError):
        data = {}
    if not isinstance(data, dict):
        data = {}
    response = {"assistants": detect_assistants(_configured_assistant_commands(data))}
    logger.info(f"Capabilities: {response}")
    if msg.reply:
        await nc.publish(msg.reply, json.dumps(response).encode())


# ---------------------------------------------------------------------------
# This machine: identity, folder browsing, project inspection
# ---------------------------------------------------------------------------


def host_info() -> Dict[str, Any]:
    """Which machine answered: the backend shows it next to host paths."""
    return {
        "hostname": platform.node() or "unknown",
        "platform": sys.platform,
        "home": str(Path.home()),
        "sep": os.sep,
    }


class WorkspaceError(Exception):
    """A folder request that cannot be served; ``status`` follows HTTP."""

    def __init__(self, status: int, message: str):
        super().__init__(message)
        self.status = status
        self.message = message


# Folders never offered in the picker (dependencies, build output, VCS data).
BROWSE_SKIP_DIRS = {
    "node_modules", "__pycache__", "venv", "env", "dist", "build", "coverage",
    "target", "vendor", "$RECYCLE.BIN", "System Volume Information",
}
BROWSE_MAX_ENTRIES = 500
# Common places people clone repositories, offered as shortcuts when present.
CLONE_FOLDERS = (
    "code", "Code", "projects", "Projects", "src", "dev", "Developer", "repos",
    "git", "GitHub", "workspace", "workspaces", "Documents/GitHub",
    "Documents/GitLab", "source/repos", "IdeaProjects", "PycharmProjects",
    "WebstormProjects",
)


def _host_path(path: str) -> Path:
    """An absolute path on this machine, with ``~`` expanded."""
    candidate = Path(os.path.expanduser(path.strip()))
    if not candidate.is_absolute():
        raise WorkspaceError(400, f"Not an absolute path on this machine: {path}")
    return candidate


def _is_hidden(entry: os.DirEntry) -> bool:
    if entry.name.startswith("."):
        return True
    if IS_WINDOWS:
        try:
            attributes = entry.stat().st_file_attributes
        except OSError:
            return True
        return bool(attributes & (stat.FILE_ATTRIBUTE_HIDDEN | stat.FILE_ATTRIBUTE_SYSTEM))
    return False


def filesystem_roots() -> List[Dict[str, str]]:
    if IS_WINDOWS:
        drives = [f"{letter}:\\" for letter in string.ascii_uppercase]
        return [{"name": d, "path": d} for d in drives if os.path.isdir(d)]
    return [{"name": "/", "path": "/"}]


def clone_shortcuts() -> List[Dict[str, str]]:
    home = Path.home()
    shortcuts = [{"name": "Home", "path": str(home)}]
    seen = {str(home).lower() if IS_WINDOWS else str(home)}
    for relative in CLONE_FOLDERS:
        folder = home / relative
        key = str(folder).lower() if IS_WINDOWS else str(folder)
        if key not in seen and folder.is_dir():
            seen.add(key)
            shortcuts.append({"name": relative, "path": str(folder)})
    return shortcuts


def browse(path: Optional[str] = None) -> Dict[str, Any]:
    """The sub-folders of *path* (default: the home folder) on this machine."""
    target = _host_path(path).resolve() if path else Path.home()
    if not target.is_dir():
        raise WorkspaceError(404, f"Folder not found on {host_info()['hostname']}: {target}")
    entries = []
    truncated = False
    try:
        with os.scandir(target) as it:
            for entry in it:
                if entry.name in BROWSE_SKIP_DIRS or _is_hidden(entry):
                    continue
                try:
                    if not entry.is_dir():
                        continue
                except OSError:
                    continue
                if len(entries) >= BROWSE_MAX_ENTRIES:
                    truncated = True
                    break
                entries.append({
                    "name": entry.name,
                    "path": entry.path,
                    "is_git": os.path.exists(os.path.join(entry.path, ".git")),
                })
    except PermissionError:
        raise WorkspaceError(403, f"Permission denied: {target}")
    entries.sort(key=lambda e: e["name"].lower())
    parent = target.parent
    return {
        "host": host_info(),
        "path": str(target),
        "parent": str(parent) if parent != target else None,
        "entries": entries,
        "shortcuts": clone_shortcuts(),
        "roots": filesystem_roots(),
        "truncated": truncated,
    }


# What the backend's stack detection reads.  Paths are listed for every file;
# contents only for these build/config files.  ``.env`` is never sent.
SNAPSHOT_SKIP_DIRS = {
    ".git", ".svn", "node_modules", "__pycache__", ".venv", "venv", "env", "dist",
    "build", ".next", ".nuxt", "target", ".gradle", ".idea", ".vscode", "coverage",
    ".pytest_cache", ".mypy_cache", "vendor",
}
SNAPSHOT_KEY_FILES = {
    "package.json", "pyproject.toml", "requirements.txt", "setup.cfg", "pytest.ini",
    "pom.xml", "build.gradle", "build.gradle.kts", "Gemfile", "go.mod",
    "docker-compose.yml", "docker-compose.yaml", ".env.example", "application.yml",
    "application.properties", "database.yml", "Chart.yaml",
    "README.md", "README.rst", "README.txt", "README",
}
SNAPSHOT_MAX_FILES = 5000
SNAPSHOT_MAX_DEPTH_FOR_CONTENT = 3
SNAPSHOT_MAX_FILE_BYTES = 64 * 1024
SNAPSHOT_MAX_TOTAL_BYTES = 400 * 1024


def _wants_content(relative: str, name: str, depth: int) -> bool:
    if name in SNAPSHOT_KEY_FILES:
        return depth <= SNAPSHOT_MAX_DEPTH_FOR_CONTENT
    lowered = relative.lower()
    return name.endswith((".yaml", ".yml")) and ("k8s" in lowered or "kubernetes" in lowered)


def project_snapshot(root: Path) -> Dict[str, Any]:
    """Relative paths of the project's files plus the text of its config files."""
    files: List[str] = []
    contents: Dict[str, str] = {}
    total = 0
    truncated = False
    for dirpath, dirnames, filenames in os.walk(root):
        dirnames[:] = sorted(d for d in dirnames if d not in SNAPSHOT_SKIP_DIRS)
        rel_dir = Path(dirpath).relative_to(root)
        depth = len(rel_dir.parts)
        for name in sorted(filenames):
            relative = (rel_dir / name).as_posix()
            files.append(relative)
            if _wants_content(relative, name, depth) and total < SNAPSHOT_MAX_TOTAL_BYTES:
                try:
                    with open(os.path.join(dirpath, name), "rb") as fh:
                        raw = fh.read(SNAPSHOT_MAX_FILE_BYTES)
                except OSError:
                    raw = b""
                text = raw.decode("utf-8", errors="ignore")
                if name.startswith("README"):
                    text = text[:2000]
                contents[relative] = text
                total += len(text)
            if len(files) >= SNAPSHOT_MAX_FILES:
                truncated = True
                break
        if truncated:
            break
    return {"files": files, "contents": contents, "truncated": truncated}


def inspect_project(path: str, with_snapshot: bool = False) -> Dict[str, Any]:
    """Whether *path* is a folder on this machine, and what repository it holds."""
    candidate = _host_path(path)
    try:
        resolved = candidate.resolve()
    except OSError:
        resolved = candidate
    is_dir = resolved.is_dir()
    result: Dict[str, Any] = {
        "host": host_info(),
        "path": str(resolved),
        "name": resolved.name or str(resolved),
        "exists": resolved.exists(),
        "is_dir": is_dir,
        "git": git_info(resolved) if is_dir else None,
    }
    if with_snapshot and is_dir:
        result["snapshot"] = project_snapshot(resolved)
    return result


async def _reply(nc, msg, body: Dict[str, Any]) -> None:
    if msg.reply:
        await nc.publish(msg.reply, json.dumps(body).encode())


def _request_body(msg) -> Dict[str, Any]:
    try:
        data = json.loads(msg.data.decode() or "{}")
    except (ValueError, UnicodeDecodeError):
        return {}
    return data if isinstance(data, dict) else {}


async def handle_browse(nc, msg):
    """``workspace.browse`` → the folder listing, or ``{"error": {status, message}}``."""
    data = _request_body(msg)
    try:
        body = await asyncio.to_thread(browse, data.get("path") or None)
    except WorkspaceError as e:
        body = {"error": {"status": e.status, "message": e.message}}
    await _reply(nc, msg, body)


async def handle_inspect(nc, msg):
    """``workspace.inspect`` → folder, git and (when asked) file snapshot."""
    data = _request_body(msg)
    path = data.get("path")
    if not isinstance(path, str) or not path.strip():
        body = {"error": {"status": 400, "message": "A folder path is required."}}
    else:
        try:
            body = await asyncio.to_thread(inspect_project, path, bool(data.get("snapshot")))
        except WorkspaceError as e:
            body = {"error": {"status": e.status, "message": e.message}}
    await _reply(nc, msg, body)


async def handle_status(nc, msg, jetstream=False):
    jetstream = jetstream() if callable(jetstream) else jetstream
    await _reply(nc, msg, {"host": host_info(), "version": DAEMON_VERSION, "jetstream": bool(jetstream)})


# ---------------------------------------------------------------------------
# Deliveries queued while this daemon was offline (JetStream)
# ---------------------------------------------------------------------------


async def process_queued_delivery(nc, js, msg) -> None:
    """Write one queued delivery and report the result on ``workspace.delivered``."""
    try:
        job = json.loads(msg.data.decode())
    except (ValueError, UnicodeDecodeError):
        logger.error("Discarding malformed queued delivery")
        await msg.term()
        return
    workspace_path = job.get("workspace_root") or ""
    response = await asyncio.to_thread(delivery_journal.once, job.get("job_id"), lambda: deliver_files(workspace_path, job.get("files") or [], job.get("git")))
    result = {
        **response,
        "job_id": job.get("job_id"),
        "company_id": job.get("company_id"),
        "sprint_id": job.get("sprint_id"),
        "item_id": job.get("item_id"),
        "host": host_info(),
    }
    try:
        await js.publish(
            nats_scope.DELIVERY_RESULT_SUBJECT,
            json.dumps(result).encode(),
            headers={"Nats-Msg-Id": f"delivered-{job.get('job_id')}"},
        )
    except Exception as e:  # noqa: BLE001 - Retry; the journal replays the response, edits apply once.
        logger.warning("Could not report queued delivery %s (%s); will retry", job.get("job_id"), e)
        await msg.nak(delay=10)
        return
    await msg.ack()
    delivery_journal.forget(job.get("job_id"))
    logger.info("Queued delivery %s: %s", job.get("job_id"), _delivery_summary(response))
    if job.get("sprint_id") is not None:
        await _publish_glassroom_status(nc, job["sprint_id"], f"{_delivery_summary(response)} (queued delivery)")


async def consume_queued_deliveries(nc, js, ready=None) -> None:
    """Wait for the backend's streams, then write each queued delivery (``ready`` is set once consuming)."""
    from nats.js import api

    config = api.ConsumerConfig(
        durable_name=nats_scope.DELIVERY_CONSUMER, deliver_policy=api.DeliverPolicy.ALL, ack_policy=api.AckPolicy.EXPLICIT,
        ack_wait=120, max_deliver=10, filter_subject=nats_scope.DELIVERY_SUBJECT,
    )
    while True:
        await nats_connect.wait_for_streams(js, nats_scope.DELIVERY_STREAMS)
        try:
            psub = await js.pull_subscribe(
                nats_scope.DELIVERY_SUBJECT, durable=nats_scope.DELIVERY_CONSUMER, stream=nats_scope.DELIVERY_STREAM,
                config=config, inbox_prefix=nats_connect.PULL_INBOX_PREFIX,
            )
            break
        except Exception as e:  # noqa: BLE001 - A permission or JetStream error: try again, say why.
            logger.warning("Could not consume queued deliveries (%s); retrying", e)
            await asyncio.sleep(5)
    if ready is not None:
        ready["jetstream"] = True
    logger.info("Consuming queued deliveries from %s", nats_scope.DELIVERY_STREAM)
    while True:
        try:
            msgs = await psub.fetch(1, timeout=5)
        except (NATSTimeoutError, asyncio.TimeoutError):
            continue
        except Exception as e:  # noqa: BLE001 - Keep consuming across reconnects.
            logger.warning("Fetching queued deliveries failed (%s); retrying", e)
            await asyncio.sleep(2)
            continue
        for msg in msgs:
            await process_queued_delivery(nc, js, msg)


# ---------------------------------------------------------------------------
# Entry point
# ---------------------------------------------------------------------------


async def connect_with_retry():
    """Connect to NATS with the credentials and TLS the environment names (``nats_connect``)."""
    return await nats_connect.connect_with_retry(NATS_URL)


async def main():
    logger.info("Connecting to NATS at %s...", nats_connect.safe_url(NATS_URL))
    nc = await connect_with_retry()
    host = host_info()

    # The streams are the backend's: the consumer waits until they exist.
    state = {"jetstream": False}
    consumer = asyncio.create_task(consume_queued_deliveries(nc, nc.jetstream(), state))

    def bound(handler, *extra):
        async def callback(msg): await handler(nc, msg, *extra)  # noqa: E704 - one line per handler.
        return callback

    for subject, callback in (
        ("workspace.generate", bound(handle_msg)),
        ("workspace.execute", bound(handle_msg)),
        ("workspace.capabilities", bound(handle_capabilities)),
        ("workspace.status", bound(handle_status, lambda: state["jetstream"])),
        ("workspace.browse", bound(handle_browse)),
        ("workspace.inspect", bound(handle_inspect)),
        ("workspace.hash", bound(file_hash.handle_hash, _reply, _request_body, _resolve_target_path)),
        *((subject, bound(handler, _reply, _request_body, _resolve_target_path)) for subject, handler in (*workspace_read.HANDLERS, *workspace_edit.HANDLERS)),
        ("workspace.git.publish", bound(git_workspace.handle_publish, _reply, _request_body)),
        ("workspace.git.reviews", bound(platform_reviews.handle_reviews, _reply, _request_body)),
        ("workspace.git.integrate", bound(sprint_integration.handle_integrate, _reply, _request_body)),
    ):
        await nc.subscribe(subject, queue=QUEUE_GROUP, cb=callback)

    logger.info("Daemon ready on %s (%s), home %s.", host["hostname"], host["platform"], host["home"])
    try:
        while True:
            await asyncio.sleep(3600)
    finally:
        consumer.cancel()
        await nc.drain()


if __name__ == "__main__":
    try:
        asyncio.run(main())
    except KeyboardInterrupt:
        pass
