"""
Fabrique workspace daemon.

Runs natively on the developer's machine (macOS, Linux or Windows) — the one
where the project was cloned — and is the only Fabrique component that touches
that machine's files.  The backend reaches it over NATS:

- workspace.generate:     write files (per-file sha256 read back and the diff, file_diff.py; a snapshot to undo it);
                          with a `git` spec, a commit on the task's branch from the base as its remote has it (git_workspace.py, git_base.py)
- workspace.execute:      run a shell command in the project and return its output
- workspace.capabilities: the console assistant CLIs found on PATH
- workspace.status:       which machine this is, the protocol version it speaks; workspace.heartbeat: published every <=25 s (daemon_status.py)
- workspace.browse / inspect: the folder picker, and a project folder's git remote/branch and file snapshot (host_folders.py)
- workspace.hash:         sha256 of named files as they are on disk (file_hash.py); workspace.undo: undo a delivery (delivery_snapshot.py)
- workspace.list / read / search / find / map, workspace.edit / edit.finish: the Developer's tools (workspace_read.py, repo_map.py, workspace_edit.py); workspace.signatures: named Python definitions' signatures for the Verifier (signatures.py); workspace.related / graph: the files around a task's seeds, and each changed file's parse for the stored graph (code_graph.py); workspace.console.prepare / collect: a console producer's worktree, then what it changed (console_run.py)
- workspace.git.publish / merge / diff / base_status / sync_base: push a branch and open its request (gh, glab), merge it locally (local_merge.py), read what it brings (git_diff.py), the base against its remote (git_base.py), or the local base fast-forwarded to it (git_sync.py)
- workspace.git.reviews:  the comments people left on that request (platform_reviews.py)
- workspace.git.read_at:  files at the default branch's head commit, never the checkout's: a company's standards and guards (git_read_at.py); workspace.self_host.checkout: the self-hosting snapshot under this machine's root (self_host_checkout.py); workspace.self_host.git / land / push: land and mirror that source (self_host_git.py)
- workspace.app.check:    start the application in the sandbox, probe it, stop it (app_run.py); workspace.sandbox.reset: reset a project's dependency cache volume (sandbox_cache.py)
- workspace.git.integrate: a sprint's task branches merged in a worktree (sprint_integration.py)
- workspace.deliver:      (JetStream) files queued while offline; result on workspace.delivered

The wire, versioned, with a schema per subject: protocol/PROTOCOL.md.
Start it with scripts/start-daemon.sh (macOS/Linux) or .ps1 (Windows); it reconnects to NATS on its own.
"""

import asyncio
import hashlib
import json
import logging
import os
import shutil
import subprocess  # noqa: F401 - tests patch daemon.subprocess.run
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, Optional

import app_run
import git_read_at, self_host_checkout, self_host_git  # noqa: E401 - one line: this file may only shrink.
import daemon_status
import delivery_journal
from daemon_status import DAEMON_VERSION, host_info  # noqa: F401 - re-exported for callers of daemon.py
from delivery import deliver as _deliver
import file_diff
import file_edits
import file_hash
import git_base, git_sync, git_workspace  # noqa: E401 - one line: this file may only shrink.
from git_workspace import git_info, remote_web_url  # noqa: F401 - re-exported for callers of daemon.py
import host_folders
from host_folders import WorkspaceError, browse, filesystem_roots, inspect_project, project_snapshot  # noqa: F401 - re-exported
import nats_connect
import nats_scope
import run_logs
import git_diff, local_merge, platform_reviews, sprint_integration  # noqa: E401 - one line: this file may only shrink.
import shell_exec
import code_graph, console_run, delivery_snapshot, repo_map, sandbox_cache, signatures, stack_image, workspace_edit, workspace_move, workspace_read  # noqa: E401 - one line: this file may only shrink.
from shell_exec import COMMAND_TIMEOUT_SECONDS, execute_shell_command  # noqa: F401 - re-exported
_build_shell_command = shell_exec.build_shell_command  # Old private name; the logic is in shell_exec.
from nats.errors import TimeoutError as NATSTimeoutError

logging.basicConfig(level=logging.INFO, format='%(asctime)s - %(name)s - %(levelname)s - %(message)s')
logger = logging.getLogger("fabrique-daemon")

NATS_URL = os.getenv("NATS_URL", "nats://localhost:4222")
WORKSPACE_ROOT_DEFAULT = os.getenv("WORKSPACE_ROOT", os.path.expanduser("~/factory-workspaces"))
PROTECTED_ROOT_DOCS = set()
# Console assistant commands workspace.capabilities reports when the request names none.
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
    return target_path.is_file() and target_path.parent == workspace_root and target_path.name in PROTECTED_ROOT_DOCS


def write_files(workspace_path: str, files: list) -> Dict[str, Any]:
    """
    Write *files* (``path`` with ``content``, or ``edits``: ``file_edits.py``) under the workspace.

    One report per file: ``status`` (``written``, ``deleted`` for a ``delete``
    entry, ``rejected_delete``, ``skipped_protected``,
    ``rejected``, ``rejected_edit`` or ``rejected_syntax`` with ``error``) and,
    when written, ``bytes`` and the ``sha256`` read back from disk: an observation.
    """
    results = []
    workspace_root = Path(workspace_path).expanduser()
    if not workspace_root.is_absolute() or not workspace_root.is_dir():
        # The project is not on this machine (wrong host, or the folder was
        # moved): never create it, and say so for every file.
        logger.warning("Workspace %s is not a folder on this machine", workspace_path)
        return [{"path": file_info.get("path", ""), "status": "workspace_missing"} for file_info in files]
    workspace_root = workspace_root.resolve()
    for file_info in files:
        requested = file_info.get("path", "")
        target_path = _resolve_target_path(workspace_path, requested)
        overwrite_existing = bool(file_info.get("overwrite_existing") or file_info.get("safe_replace"))

        if not target_path or target_path == workspace_root:
            results.append({"path": requested, "status": "rejected"})
            logger.warning("Rejected unsafe file path: %s", requested)
            continue

        if file_info.get("delete") is True:  # workspace_move.py: an emptied file.
            if _is_protected_existing_doc(workspace_root, target_path):
                results.append({"path": requested, "status": "skipped_protected", "absolute_path": str(target_path)})
            else:
                results.append(workspace_move.deliver_delete(workspace_root, target_path, file_info, requested))
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
    """``write_files`` in the reply shape the backend reads: a commit on the task's branch when *git* names it, else (also for a folder that is not a repository, ``git_workflow_skipped``) with a snapshot to undo it, and the project's paths for the Verifier."""
    return _deliver(workspace_path, files, git, write_files, _resolve_target_path)


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
        full_log = run_logs.wanted(data)  # The whole output to the object store (run_logs.py).
        result = await asyncio.to_thread(sprint_integration.while_active, workspace_path, execute_shell_command, cmd, workspace_path, data.get("sandbox"), data.get("command_policy"), data.get("timeout_seconds"), full_log is not None)
        await run_logs.upload(nc, full_log, result)

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


async def _reply(nc, msg, body: Dict[str, Any]) -> None:
    if msg.reply:
        await nc.publish(msg.reply, json.dumps(body).encode())


def _request_body(msg) -> Dict[str, Any]:
    try:
        data = json.loads(msg.data.decode() or "{}")
    except (ValueError, UnicodeDecodeError):
        return {}
    return data if isinstance(data, dict) else {}


async def handle_status(nc, msg, jetstream=False):
    """``workspace.status`` → host, version, protocol version and range, daemon id (``daemon_status.py``)."""
    jetstream = jetstream() if callable(jetstream) else jetstream
    await _reply(nc, msg, await asyncio.to_thread(daemon_status.status_body, jetstream))


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


def subscriptions(nc, jetstream=lambda: False):
    """(subject, callback) for every request subject this daemon serves (``protocol/PROTOCOL.md``)."""
    def bound(handler, *extra):
        async def callback(msg): await handler(nc, msg, *extra)  # noqa: E704 - one line per handler.
        return callback

    return [
        ("workspace.generate", bound(handle_msg)),
        ("workspace.execute", bound(handle_msg)),
        ("workspace.capabilities", bound(handle_capabilities)),
        ("workspace.status", bound(handle_status, jetstream)),
        ("workspace.hash", bound(file_hash.handle_hash, _reply, _request_body, _resolve_target_path)),
        ("workspace.undo", bound(delivery_snapshot.handle_undo, _reply, _request_body, _resolve_target_path)),
        *((subject, bound(handler, _reply, _request_body, _resolve_target_path)) for subject, handler in (*workspace_read.HANDLERS, *workspace_edit.HANDLERS, *repo_map.HANDLERS, *signatures.HANDLERS, *code_graph.HANDLERS, *console_run.HANDLERS, *sandbox_cache.HANDLERS, *stack_image.HANDLERS)),
        *((subject, bound(handler, _reply, _request_body)) for subject, handler in (*host_folders.HANDLERS, *git_sync.HANDLERS, *git_read_at.HANDLERS, *self_host_checkout.HANDLERS, *self_host_git.HANDLERS, ("workspace.git.publish", git_workspace.handle_publish), ("workspace.git.merge", local_merge.handle_merge_local), ("workspace.git.diff", git_diff.handle_diff), ("workspace.git.base_status", git_base.handle_status))),
        ("workspace.git.reviews", bound(platform_reviews.handle_reviews, _reply, _request_body)),
        ("workspace.app.check", bound(app_run.handle_app_check, _reply, _request_body)),
        ("workspace.git.integrate", bound(sprint_integration.handle_integrate, _reply, _request_body)),
    ]


async def main():
    logger.info("Connecting to NATS at %s...", nats_connect.safe_url(NATS_URL))
    nc = await connect_with_retry()
    host = host_info()

    # The streams are the backend's: the consumer waits until they exist.
    state = {"jetstream": False}
    consumer = asyncio.create_task(consume_queued_deliveries(nc, nc.jetstream(), state))
    for subject, callback in subscriptions(nc, lambda: state["jetstream"]):
        await nc.subscribe(subject, queue=QUEUE_GROUP, cb=callback)
    heartbeat = asyncio.create_task(daemon_status.heartbeat_loop(nc))

    logger.info("Daemon ready on %s (%s), home %s, protocol %d.", host["hostname"], host["platform"], host["home"], daemon_status.PROTOCOL_VERSION)
    try:
        while True:
            await asyncio.sleep(3600)
    finally:
        consumer.cancel()
        heartbeat.cancel()
        await nc.drain()


if __name__ == "__main__":
    try:
        asyncio.run(main())
    except KeyboardInterrupt:
        pass
