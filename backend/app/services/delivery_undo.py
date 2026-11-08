"""Undoing a task's delivery, for a company without the Git workflow.

Taken from opencode's snapshots (https://opencode.ai/v2/docs/snapshots/).
Before a delivery lands in the person's folder, the workspace daemon keeps
the state of the files it is about to change in a separate Git object store
of its own, never in the project's ``.git`` (``vscode-daemon/delivery_snapshot.py``).
Its answer names the snapshot, with per file whether it existed and the
sha256 before and after: an observation the daemon made, kept on the run's
evidence as ``snapshot`` (``git_workflow.record_reply``).

A Manager undoes the delivery (``POST /v1/agent_runs/{id}/undo``): the daemon
puts back each file the delivery changed, and removes the ones it created,
only when every one of them still holds the bytes the delivery wrote; a file
changed since is a conflict and nothing is touched. A delivery on a Git
branch is undone by discarding the branch instead. The daemon's answer is
audited (``run_delivery_undone``); a second undo finds no snapshot and is
refused. Undoing touches files only: the task's status stays as it is, and a
Manager who wants the work done again sends the task back with a review.
"""

from __future__ import annotations

from typing import Any

from models.agent_run import AgentRun
from models.audit import AuditLog
from sqlmodel import Session, select

from services.agent_service import AgentService
from services.review_changes import latest_workspace_report

UNDO_ACTION = "run_delivery_undone"


class UndoRefused(Exception):
    def __init__(self, status: int, detail: str):
        super().__init__(detail)
        self.status = status
        self.detail = detail


def _interaction(run: AgentRun) -> str:
    return f"agent-run:{run.external_ref}"


def snapshot_of(session: Session, run: AgentRun) -> dict[str, Any] | None:
    """The snapshot the daemon reported for the run's latest observed delivery, with whether it was undone."""
    report = latest_workspace_report(session, run)
    evidence = (report.evidence or {}) if report is not None else {}
    snapshot = evidence.get("snapshot")
    if evidence.get("git") or not isinstance(snapshot, dict) or not snapshot.get("id"):
        return None
    undone = next(
        (
            row
            for row in session.exec(
                select(AuditLog).where(
                    AuditLog.company_id == run.company_id,
                    AuditLog.action == UNDO_ACTION,
                    AuditLog.interaction_id == _interaction(run),
                )
            ).all()
            if isinstance(row.result, dict)
            and row.result.get("snapshot") == snapshot["id"]
        ),
        None,
    )
    return {
        "id": snapshot["id"],
        "files": [f for f in snapshot.get("files") or [] if isinstance(f, dict)],
        "undone_at": undone.created_at if undone is not None else None,
    }


def undo_spec(session: Session, run: AgentRun) -> dict[str, Any]:
    """The ``workspace.undo`` request for *run*, or why its delivery cannot be undone."""
    report = latest_workspace_report(session, run)
    evidence = (report.evidence or {}) if report is not None else {}
    if evidence.get("git"):
        raise UndoRefused(
            409, "This delivery was made on a Git branch: discard the branch instead"
        )
    snapshot = evidence.get("snapshot")
    root = evidence.get("workspace_root")
    if not isinstance(snapshot, dict) or not snapshot.get("id") or not root:
        reason = (
            evidence.get("snapshot_error") or "the workspace daemon kept no snapshot"
        )
        raise UndoRefused(409, f"This delivery cannot be undone: {reason}")
    return {"workspace_root": root, "snapshot_id": snapshot["id"]}


def record_undo(
    session: Session,
    run: AgentRun,
    reply: dict[str, Any],
    *,
    undone_by: str,
) -> dict[str, Any]:
    """Audit what the daemon answered and return it; an answer missing its facts is refused."""
    restored, removed = reply.get("restored"), reply.get("removed")
    if (
        not reply.get("ok")
        or not isinstance(restored, list)
        or not isinstance(removed, list)
    ):
        raise UndoRefused(502, "The workspace daemon sent an unreadable answer")
    result = {
        "snapshot": reply.get("snapshot"),
        "restored": restored,
        "removed": removed,
    }
    audit_log = AgentService().audit_interaction(
        session=session,
        company_id=run.company_id,
        interaction_id=_interaction(run),
        agent_id=f"human:{undone_by}",
        action=UNDO_ACTION,
        details={"company_id": str(run.company_id), "agent_run_id": run.id},
        result=result,
        decision_signature=None,
        cost_tokens=0,
    )
    return {
        "run_id": run.id,
        **result,
        "undone_by": undone_by,
        "audit_log_id": audit_log.id,
    }
