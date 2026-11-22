"""The folder's latest test run before a delivery, for the Verifier.

Without the Git workflow the tasks share one folder; before a delivery's tests
run, the worker records on its evidence which tests failed in the latest run
the daemon made in that folder for another task (``test_baseline``), so that
``services/evidence/test_baseline.py`` does not blame this delivery for them.

The run counts only while what it ran against is still in the folder: a run
whose delivery was undone (``refuted_undo.undone_snapshot``) is skipped, and
so is a run of this same task (its earlier attempt is its own work). It is
taken just before the tests run, so a delivery undone later still counts. The
baseline is the platform's own record of a daemon observation, never what an
agent reported. With Git each task's branch starts from verified work and no
baseline is taken.
"""

from __future__ import annotations

from typing import Any

from models.agent_run import TASK_WORKER_PRODUCER, AgentRun, Verification
from sqlmodel import Session, select

from services.loop import refuted_undo

# How many of the company's latest verifications are looked through.
LOOKBACK = 200


def _baseline(
    session: Session, company_id: int, item_id: int, root: str
) -> dict[str, Any] | None:
    rows = session.exec(
        select(Verification, AgentRun)
        .join(AgentRun, AgentRun.id == Verification.agent_run_id)
        .where(
            Verification.company_id == company_id,
            AgentRun.producer == TASK_WORKER_PRODUCER,
            AgentRun.external_ref != f"task-{item_id}",
        )
        .order_by(Verification.id.desc())
        .limit(LOOKBACK)
    ).all()
    for verification, run in rows:
        evidence = verification.evidence or {}
        test_run = evidence.get("test_run") or {}
        exit_code = test_run.get("exit_code")
        if evidence.get("git") or evidence.get("workspace_root") != root:
            continue
        if isinstance(exit_code, bool) or not isinstance(exit_code, int):
            continue
        if refuted_undo.undone_snapshot(session, company_id, evidence):
            continue
        return {
            "run_id": run.id,
            "failing_tests": list(test_run.get("failing_tests") or []),
            "failing_count": test_run.get("failing_count"),
        }
    return None


def observe(
    engine: Any, company_id: int, item_id: int, evidence: dict[str, Any]
) -> None:
    """Put ``test_baseline`` on *evidence* (a delivery without Git only)."""
    root = evidence.get("workspace_root")
    if evidence.get("git") or not root:
        return
    with Session(engine) as session:
        baseline = _baseline(session, company_id, item_id, root)
    if baseline is not None:
        evidence["test_baseline"] = baseline
