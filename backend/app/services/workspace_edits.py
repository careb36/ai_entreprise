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

from services import developer_tool_replies

logger = logging.getLogger("fabrique-task-worker")

EDIT_TIMEOUT_SECONDS = 30.0
EDIT_FIELDS = ("path", "start_line", "end_line", "new_text", "search", "replace")


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
    from services.task_worker import ALLOWED_ARTIFACT_EXTENSIONS

    return set(ALLOWED_ARTIFACT_EXTENSIONS)


def edit(box: Any, arguments: dict[str, Any], extra: dict[str, Any]) -> str:
    """One edit through the daemon; *extra* gets the observation's fields."""
    path = str(arguments.get("path") or "")
    suffix = (
        "." + path.rsplit(".", 1)[-1].lower() if "." in path.rsplit("/", 1)[-1] else ""
    )
    if box.edit_suffixes is not None and suffix not in box.edit_suffixes:
        raise RuntimeError(
            f"a delivery does not write {suffix or 'such'} files: {path}"
        )
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
    return developer_tool_replies.edit_text(reply)


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
        if isinstance(f, dict) and (f.get("changed") or f.get("error"))
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
        {"path": f["path"], "content": f["content"], "source": "workspace_edit"}
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
    conflicts = [
        {
            "path": f.get("path"),
            "error": f["error"],
            "conflict": bool(f.get("conflict")),
        }
        for f in edits
        if isinstance(f, dict) and f.get("error")
    ]
    if not (calls or files or conflicts):
        return evidence
    evidence = evidence if isinstance(evidence, dict) else {}
    for key, value in (
        ("tool_calls", calls),
        ("tool_edit_files", files),
        ("tool_edit_conflicts", conflicts),
    ):
        if value:
            evidence[key] = value
    return evidence
