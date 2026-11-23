"""The edit tool's backend side: where it may write, each edit, and what it delivers.

``workspace__edit`` (SWE-agent's edit with a check before applying, MIT) asks
the daemon to change a line range or one SEARCH/REPLACE block of an existing
file; the daemon applies it only if the file still parses
(``vscode-daemon/workspace_edit.py``). Each applied edit is an observation on
the run's evidence: the path, the sha256 before and after, the checker.

The edits are a working copy for the task's tool loop. When the loop ends
(``WorkspaceToolbox.close`` → ``finish``) the daemon answers with each edited
file's final text and puts the original back; the task worker then delivers
those texts with the answer's files (``with_tool_edits``) through
``workspace.generate``, so the daemon's diff, the Git commit, the delivery
tests and the Verifier see the change as any delivery's. An edit that never
reaches a delivery never stays on disk.

Where: only the task's own worktree, or a project without the Git workflow
(where deliveries write anyway); never the person's checkout of a company
that delivers on branches (``allowed``).
"""

from __future__ import annotations

import logging
from typing import Any

from services.contracts.manifest import ALLOWED_ARTIFACT_EXTENSIONS
from services.tools import developer_tool_replies, edit_lint
from services.workspace import daemon_liveness

logger = logging.getLogger("fabrique-task-worker")

EDIT_TIMEOUT_SECONDS = 30.0
EDIT_FIELDS = ("path", "start_line", "end_line", "new_text", "search", "replace")
MOVE_FIELDS = ("path", "start_line", "end_line", "to_path", "to_line")


def allowed(bridge: dict[str, Any], company_id: int, root: str, engine: Any) -> bool:
    """Whether the edit tool may write under *root*."""
    try:
        if root != bridge["resolve_root"](company_id):
            return True  # The task's own worktree.
        if engine is None:
            return False
        from models.company import Company
        from sqlmodel import Session

        with Session(engine) as session:
            company = session.get(Company, company_id)
            return company is not None and not company.git_workflow
    except Exception as exc:  # noqa: BLE001 - Unknown: no edits.
        logger.warning("Could not tell where edits may go: %s", exc)
        return False


def delivery_suffixes() -> set[str]:
    """The file types a delivery writes: an edit to another would be dropped."""
    return set(ALLOWED_ARTIFACT_EXTENSIONS)


def _deliverable(box: Any, path: str) -> None:
    """Refuse a file a delivery would not write: the change would be dropped."""
    suffix = (
        "." + path.rsplit(".", 1)[-1].lower() if "." in path.rsplit("/", 1)[-1] else ""
    )
    if box.edit_suffixes is not None and suffix not in box.edit_suffixes:
        raise RuntimeError(
            f"a delivery does not write {suffix or 'such'} files: {path}"
        )


def can_move(can_edit: bool) -> bool:
    """Whether to offer move and delete: where edits may go, and only when every
    daemon announces both operations, so an older one never misreads them."""
    if not can_edit:
        return False
    return bool(
        daemon_liveness.announces("workspace.edit", "move")
        and daemon_liveness.announces("workspace.edit", "delete")
        and daemon_liveness.announces("workspace.generate", "delete")
    )


def structure(
    box: Any, tool: str, arguments: dict[str, Any], extra: dict[str, Any]
) -> str:
    """A move of lines or the deletion of an emptied file through the daemon
    (``vscode-daemon/workspace_move.py``); *extra* gets the daemon's observation:
    the moved bytes' sha256 and both files' hashes, or the deleted file's."""
    fields = MOVE_FIELDS if tool == "move" else ("path",)
    request = {k: arguments[k] for k in fields if k in arguments}
    for key in ("path", "to_path"):
        if key in request:
            _deliverable(box, str(request[key] or ""))
    box.edits_made = True
    reply = box.requester(
        "workspace.edit",
        {"workspace_root": box.workspace_root, "session": box.edit_session, "op": tool}
        | request,
        EDIT_TIMEOUT_SECONDS,
    )
    keys = (
        ("path", "to_path", "moved_lines", "moved_sha256", "source", "target")
        if tool == "move"
        else ("path", "deleted", "sha256_before")
    )
    extra.update({key: reply.get(key) for key in keys})
    if tool == "delete":
        return (
            f"Deleted {reply.get('path')}; the deletion is delivered when you finish."
        )
    target = reply.get("target") or {}
    return (
        f"Moved {reply.get('moved_lines')} lines from {reply.get('path')} to "
        f"{reply.get('to_path')}{' (a new file)' if target.get('created') else ''}; "
        f"{reply.get('path')} has {(reply.get('source') or {}).get('lines')} lines now. "
        f"Lines {reply.get('first_line')}–{reply.get('last_line')} of {reply.get('to_path')}:\n"
        + str(reply.get("text") or "")
    )


def deletions(edited: Any) -> list[dict[str, Any]]:
    """The files the session deleted, as delivery entries. ``sha256_expected``
    is the original's sha256 the daemon itself reported at finish, never the
    model's: the delivery deletes the file only while it still holds those bytes."""
    return [
        {"path": f["path"], "delete": True, "sha256_expected": f["sha256_original"]}
        for f in edited or []
        if isinstance(f, dict)
        and f.get("deleted")
        and not f.get("error")
        and isinstance(f.get("path"), str)
        and isinstance(f.get("sha256_original"), str)
    ]


def edit(box: Any, arguments: dict[str, Any], extra: dict[str, Any]) -> str:
    """One edit through the daemon; *extra* gets the observation's fields."""
    path = str(arguments.get("path") or "")
    _deliverable(box, path)
    request = {k: arguments[k] for k in EDIT_FIELDS if k in arguments}
    box.edits_made = True
    reply = box.requester(
        "workspace.edit",
        {"workspace_root": box.workspace_root, "session": box.edit_session} | request,
        EDIT_TIMEOUT_SECONDS,
    )
    extra.update(
        {
            key: reply.get(key)
            for key in (
                "path",
                "sha256_before",
                "sha256_after",
                "bytes",
                "lines",
                "checked",
            )
        }
    )
    # The linter's errors in the same turn (``services/edit_lint.py``), ahead
    # of the edited window so the result's cut never drops them.
    lint = edit_lint.after_edit(box, str(reply.get("path") or path), reply, extra)
    return (lint.strip() + "\n\n" if lint else "") + developer_tool_replies.edit_text(
        reply
    )


def finish(box: Any) -> None:
    """The edited files' final texts onto ``box.edited_files``, the originals
    back on disk. A daemon that cannot answer leaves the edits on disk,
    undelivered: ``box.finish_error`` and the log say so."""
    if not box.edits_made:
        return
    box.edits_made = False
    try:
        reply = box.requester(
            "workspace.edit.finish",
            {"workspace_root": box.workspace_root, "session": box.edit_session},
            EDIT_TIMEOUT_SECONDS,
        )
    except Exception as exc:  # noqa: BLE001 - Reported, never raised from close().
        box.finish_error = f"{type(exc).__name__}: {exc}"
        logger.warning("Could not finish the workspace edits: %s", exc)
        return
    # The files to deliver, and the ones the daemon left alone (someone saved
    # them meanwhile, or their path now leads elsewhere): kept to report.
    box.edited_files = [
        f
        for f in reply.get("files") or []
        if isinstance(f, dict)
        and (f.get("changed") or f.get("error") or f.get("deleted"))
    ]
    for f in box.edited_files:
        if f.get("error"):
            logger.warning("Edit of %s not delivered: %s", f.get("path"), f["error"])


def delivered(edited: Any) -> list[dict[str, Any]]:
    """The edited files a delivery writes: changed, with the daemon's text."""
    return [
        f
        for f in edited or []
        if isinstance(f, dict)
        and f.get("changed")
        and not f.get("error")
        and isinstance(f.get("path"), str)
        and isinstance(f.get("content"), str)
    ]


def with_tool_edits(targets: list[dict[str, Any]], edited: Any) -> list[dict[str, Any]]:
    """The delivery's files plus the ones the edit tool changed, with the
    text the daemon read back when the loop finished: an observation, not the
    model's claim. A file the answer delivers itself keeps its own block."""
    have = {t["path"] for t in targets}
    extra = [
        {
            "path": f["path"],
            "content": f["content"],
            "source": f.get("source") or "workspace_edit",
        }
        for f in delivered(edited)
        if f["path"] not in have
    ]
    return list(targets) + extra


def observe_tools(evidence: Any, agent: dict[str, Any]) -> Any:
    """The tool loop's observations onto the run's evidence, apart from the
    claims: the calls (``tool_calls``), the edited files' texts as the daemon
    read them back (``tool_edit_files``, scanned by the standards check and
    read back by the acceptance guard) and the edits it left alone
    (``tool_edit_conflicts``). Updates *evidence* in place, so a later
    re-verification of the same delivery keeps them."""
    calls, edits = agent.get("tool_calls"), agent.get("tool_edits") or []
    files = [
        {"path": f["path"], "content": f["content"], "sha256": f.get("sha256")}
        for f in delivered(edits)
    ]
    deleted = [
        {"path": d["path"], "sha256_original": d["sha256_expected"]}
        for d in deletions(edits)
    ]
    conflicts = [
        {
            "path": f.get("path"),
            "error": f["error"],
            "conflict": bool(f.get("conflict")),
        }
        for f in edits
        if isinstance(f, dict) and f.get("error")
    ]
    console = agent.get("console_run")  # A console producer's run (console_producer).
    if not (
        calls
        or files
        or deleted
        or conflicts
        or console
        or agent.get("console_skipped")
    ):
        return evidence
    evidence = evidence if isinstance(evidence, dict) else {}
    if console:
        evidence["console_run"] = console
    if agent.get("console_skipped"):  # Why the model produced it instead.
        evidence["console_producer_skipped"] = agent["console_skipped"]
    for key, value in (
        ("tool_calls", calls),
        ("tool_edit_files", files),
        ("tool_edit_deletions", deleted),
        ("tool_edit_conflicts", conflicts),
    ):
        if value:
            evidence[key] = value
    return evidence
