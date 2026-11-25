"""A rework that delivers the same bytes again is not sent back again.

When the Verifier refutes a delivery for its code (``delivery_gate.
CODE_PROBLEMS``: the tests failed, the build broke), the task goes back with
the observation. An agent that then delivers exactly the files it delivered
before, byte for byte, gets exactly the same verdict: every further attempt
spends a model call and a test run to learn nothing, until the rework limit
blocks the task anyway.

``repeated`` compares the task's latest delivery with the one before it on
the backend's own record of what it sent: ``evidence["expected"]``, the sha256
of each file's content computed by the backend (``manifest.expected_hashes``),
never a hash the agent reported, together with the files the daemon read back
after writing them (``evidence["workspace_writes"]``, each with the sha256 the
daemon computed over the bytes on disk). An edit (search and replace, a moved
block) has no expected content, so without the read-back a rework that only
edited a file looked like the attempt before it (sprint 11, task 46: the edit
of ``contexts.json`` was not counted and the task was blocked as a repeat).
When both hold the same files with the same
digests, the worker blocks the task for a person at once
(``worker/review._send_back_from_delivery_gate``) and says which run it
repeats. A delivery the daemon never confirmed is not a code problem: sending
it again is how it gets written, and it is never cut short here.

The idea is Paperclip's rewake throttle (MIT:
``server/src/services/issue-rewake-throttle.ts``), which stops waking an agent
after runs that showed no progress on the platform's own record, never on the
agent's word.
"""

from __future__ import annotations

from typing import Any

from models.agent_run import TASK_WORKER_PRODUCER, AgentRun, Verification
from sqlmodel import Session, select


def _expected(session: Session, run: AgentRun) -> dict[str, str]:
    """What the run delivered: the backend's expected digests, then the
    daemon's read-back of each file it wrote (edits included)."""
    verification = session.exec(
        select(Verification)
        .where(Verification.agent_run_id == run.id)
        .order_by(Verification.id.desc())
    ).first()
    evidence = (verification.evidence or {}) if verification else {}
    expected = evidence.get("expected")
    delivered = {
        str(path): str(digest)
        for path, digest in (expected if isinstance(expected, dict) else {}).items()
        if isinstance(digest, str) and digest
    }
    writes = evidence.get("workspace_writes")
    for write in writes if isinstance(writes, list) else []:
        if not isinstance(write, dict) or write.get("status") != "written":
            continue
        path, digest = write.get("path"), write.get("sha256")
        if isinstance(path, str) and isinstance(digest, str) and digest:
            delivered[path] = digest
    return delivered


def repeated(session: Session, company_id: int, item_id: int) -> dict[str, Any] | None:
    """The run the task's latest delivery repeats byte for byte, if it does:
    ``{"run_id", "same_as", "files"}``; ``None`` otherwise."""
    runs = session.exec(
        select(AgentRun)
        .where(
            AgentRun.company_id == company_id,
            AgentRun.external_ref == f"task-{item_id}",
            AgentRun.producer == TASK_WORKER_PRODUCER,
        )
        .order_by(AgentRun.version.desc(), AgentRun.id.desc())
        .limit(2)
    ).all()
    if len(runs) < 2:
        return None
    latest, previous = runs
    now, before = _expected(session, latest), _expected(session, previous)
    if not now or now != before:
        return None
    return {"run_id": latest.id, "same_as": previous.id, "files": len(now)}


def blocked_text(problem: str, same: dict[str, Any], observation: str) -> str:
    return (
        f"{problem.capitalize()} again with the same delivery: run #{same['run_id']} "
        f"sent the same {same['files']} file(s), byte for byte, as run "
        f"#{same['same_as']}, which was refuted — blocked for manual triage "
        f"instead of another attempt: {observation}"
    )
