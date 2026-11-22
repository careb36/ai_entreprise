"""A refuted delivery does not stay in a folder without Git.

With the Git workflow each task delivers on its own branch, and a branch the
Verifier refuted is never merged. Without it the workspace daemon writes into
the person's folder before the Verifier judges, so a refuted delivery used to
stay there: the other tasks' tests ran against it, and what the folder held
at the end was the last attempt of a task that ended blocked (bench, specs 05
and 06 of 2026-11-22).

The worker now undoes a refuted delivery at once, from the snapshot the daemon
took before writing (``vscode-daemon/delivery_snapshot.py``, the same
``workspace.undo`` a Manager asks for, ``services/release/delivery_undo.py``).
The daemon puts a file back only while it still holds the bytes the delivery
wrote; when another task changed one since, it refuses the whole undo as a
conflict, nothing is touched and the task's comment says so. The daemon's
answer is audited as ``run_delivery_undone`` like a Manager's undo, which is
how ``undone`` (and the release context's ``snapshot_of``) tell it was undone,
and the next attempt is told it starts from the files as they were.

Taken in spirit from GitHub Copilot coding agent and OpenAI Codex cloud, whose
runs never touch the person's tree until they pass: here the tree is shared,
so the failed run is taken back out instead.
"""

from __future__ import annotations

from typing import Any

from models.agent_run import TASK_WORKER_PRODUCER, AgentRun, Verification
from models.audit import AuditLog
from sqlmodel import Session, select

from services.platform.agent_service import AgentService
from services.platform.orchestration_metrics import VERDICT_REFUTED
from services.workspace.workspace_daemon import (
    DaemonError,
    DaemonUnavailable,
    daemon_request,
)

# ``services/release/delivery_undo.UNDO_ACTION``; loop may not import release.
UNDO_ACTION = "run_delivery_undone"
UNDO_TIMEOUT_SECONDS = 60.0
WORKER = "system:task-worker"
UNDONE_PREFIX = "Refuted delivery undone"
# What the next attempt is told instead of ``qa_review.EDIT_INSTRUCTION``: its
# earlier attempt is no longer in the folder, so it delivers in full.
UNDONE_INSTRUCTION = (
    "Your last attempt was refuted and undone: the files are as they were "
    "before it. Deliver every file this task needs, in full, each as a path= block."
)
KEPT_PREFIX = "The refuted delivery stays in the folder"


def undo_spec(evidence: dict[str, Any] | None) -> dict[str, Any] | None:
    """The ``workspace.undo`` request for a delivery, or ``None`` when there is
    nothing to undo here: a Git branch, or no snapshot the daemon reported."""
    evidence = evidence or {}
    snapshot = evidence.get("snapshot")
    root = evidence.get("workspace_root")
    if evidence.get("git") or not isinstance(snapshot, dict) or not root:
        return None
    if not snapshot.get("id"):
        return None
    return {"workspace_root": root, "snapshot_id": snapshot["id"]}


def _interaction(item_id: int) -> str:
    # The run's ``external_ref`` is ``task-<id>`` (record._record_task_run).
    return f"agent-run:task-{item_id}"


def record(
    session: Session,
    company_id: int,
    item_id: int,
    run_id: int | None,
    reply: dict[str, Any],
) -> dict[str, Any]:
    """Audit what the daemon undid; the caller has checked the reply."""
    result = {
        "snapshot": reply.get("snapshot"),
        "restored": list(reply.get("restored") or []),
        "removed": list(reply.get("removed") or []),
        "reason": "refuted",
    }
    AgentService().audit_interaction(
        session=session,
        company_id=company_id,
        interaction_id=_interaction(item_id),
        agent_id=WORKER,
        action=UNDO_ACTION,
        details={"company_id": str(company_id), "agent_run_id": run_id},
        result=result,
        decision_signature=None,
        cost_tokens=0,
    )
    return result


def _comment(result: dict[str, Any]) -> str:
    restored, removed = len(result["restored"]), len(result["removed"])
    return (
        f"{UNDONE_PREFIX}: {restored} file(s) put back and {removed} removed; "
        "the folder is as it was before this attempt."
    )


async def undo_refuted(
    nats_client: Any,
    engine: Any,
    *,
    company_id: int,
    item_id: int,
    run_id: int | None,
    verdict: str | None,
    evidence: dict[str, Any] | None,
) -> str | None:
    """Undo a refuted delivery made without Git; the sentence for the task's
    comment, or ``None`` when nothing applied (not refuted, or no snapshot)."""
    if verdict != VERDICT_REFUTED:
        return None
    spec = undo_spec(evidence)
    if spec is None:
        return None
    try:
        reply = await daemon_request(
            nats_client, "workspace.undo", spec, timeout=UNDO_TIMEOUT_SECONDS
        )
    except (DaemonUnavailable, DaemonError) as exc:
        return f"{KEPT_PREFIX}: it could not be undone ({exc})."
    if (
        not reply.get("ok")
        or not isinstance(reply.get("restored"), list)
        or not isinstance(reply.get("removed"), list)
    ):
        return f"{KEPT_PREFIX}: the workspace daemon sent an unreadable answer."
    with Session(engine) as session:
        result = record(session, company_id, item_id, run_id, reply)
    return _comment(result)


def undone(session: Session, company_id: int, item_id: int) -> bool:
    """Whether the task's latest delivery was undone (by the worker or a Manager)."""
    run = session.exec(
        select(AgentRun)
        .where(
            AgentRun.company_id == company_id,
            AgentRun.external_ref == f"task-{item_id}",
            AgentRun.producer == TASK_WORKER_PRODUCER,
        )
        .order_by(AgentRun.version.desc())
    ).first()
    if run is None:
        return False
    verification = session.exec(
        select(Verification)
        .where(Verification.agent_run_id == run.id)
        .order_by(Verification.id.desc())
    ).first()
    evidence = (verification.evidence or {}) if verification else {}
    return undone_snapshot(session, company_id, evidence)


def undone_snapshot(
    session: Session, company_id: int, evidence: dict[str, Any] | None
) -> bool:
    """Whether the delivery this evidence records was undone (by the worker or
    a Manager): an audited undo names its snapshot."""
    snapshot = (evidence or {}).get("snapshot")
    if not isinstance(snapshot, dict) or not snapshot.get("id"):
        return False
    return any(
        isinstance(row.result, dict) and row.result.get("snapshot") == snapshot["id"]
        for row in session.exec(
            select(AuditLog).where(
                AuditLog.company_id == company_id,
                AuditLog.action == UNDO_ACTION,
            )
        ).all()
    )
