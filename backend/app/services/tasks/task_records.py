"""A task's record for the record tools (issue #766, family E).

What ``board__task`` and ``board__evidence`` (``services/tools/board_tools.py``)
read, moved here from ``services/loop/worker/board_record.py`` so that every
context whose turns get the record tools may read them: the loop (the QA
review, the Architect's brief, a rework, the ScrumMaster's triage) and
planning (the PO's sprint plan). This module reads; the tools context
composes the tools and the caller sets the scope (``task_ids``,
``sprint_task_ids``), never the call's arguments.

The readers split every record into ``observations`` (what the platform wrote
or computed: the events' kinds, actors, statuses and times, the system's and
the gates' own text, the Verifier's checks, the files' sha256 the backend sent
or the daemon read back, the tools offered, the loop's round count) and
``claims`` (what an agent or a person wrote: comments, verdicts, rework
requests, the plan's words, the producer's model, role, claims and
deliverable). Both are bounded here; ``board_tools`` redacts and cuts the
reply. A task that is not the company's reads as ``None``.
"""

from __future__ import annotations

from typing import Any

from models import AgentRun, BacklogItem, Sprint, Verification
from models.agent_run import TASK_WORKER_PRODUCER
from sqlmodel import Session, select

from services.contracts.task_dependencies import depends_on_of
from services.tasks import task_events

MAX_EVENTS = 60
MAX_CHECKS = 40
MAX_FILES = 40
TEXT_CHARS = 600
ARTIFACT_CHARS = 1500
PLATFORM_ACTORS = (task_events.SYSTEM, task_events.GATE)


def _cut(value: Any, limit: int = TEXT_CHARS) -> Any:
    """*value* bounded: strings cut, lists shortened, dicts walked."""
    if isinstance(value, str):
        return value if len(value) <= limit else value[:limit] + " [cut]"
    if isinstance(value, list):
        return [_cut(v, limit) for v in value[:MAX_FILES]] + (
            [f"[{len(value) - MAX_FILES} more]"] if len(value) > MAX_FILES else []
        )
    if isinstance(value, dict):
        return {str(k): _cut(v, limit) for k, v in value.items()}
    return value


def _when(value: Any) -> str | None:
    return value.isoformat() if hasattr(value, "isoformat") else value


def _company_item(session: Session, company_id: int, task_id: int) -> Any:
    """The task, when it is a task of *company_id*; ``None`` otherwise."""
    item = session.get(BacklogItem, task_id)
    if item is None:
        return None
    sprint = session.get(Sprint, item.sprint_id)
    if sprint is None or sprint.company_id != company_id:
        return None
    return item


# --- board__task ----------------------------------------------------------------


def task_record(item: Any, events: list[Any]) -> dict[str, Any]:
    """A task's history from its row and events, claims apart."""
    kinds = [str(e.event_type) for e in events]
    payloads = [e.payload or {} for e in events]
    observations: list[dict[str, Any]] = [
        {
            "task": item.id,
            "sprint": item.sprint_id,
            "status": item.status,
            "assignee": item.assignee,
            "priority": item.priority,
            "depends_on": depends_on_of(item),
        },
        {
            "attempts": kinds.count("claimed"),
            "deliveries": kinds.count("delivered"),
            "rework_requests": sum(
                1 for p in payloads if p.get("verdict") == "needs_work"
            ),
            "events": len(events),
        },
    ]
    claims: list[dict[str, Any]] = [
        {
            "source": "the plan",
            "title": _cut(item.title),
            "description": _cut(item.description or ""),
        }
    ]
    for event in events[-MAX_EVENTS:]:
        payload = event.payload or {}
        moved = {
            "seq": event.seq,
            "at": _when(event.created_at),
            "kind": event.event_type,
            "actor": event.actor,
            "status_after": event.task_status,
        }
        moved.update(
            {
                k: getattr(event, k)
                for k in ("run_id", "verification_id")
                if getattr(event, k)
            }
        )
        text = str(payload.get("text") or "").strip()
        if event.actor in PLATFORM_ACTORS:
            if text:
                moved["text"] = _cut(text)
            if payload.get("verdict"):
                moved["verdict"] = payload["verdict"]
        observations.append(moved)
        if text and event.actor not in PLATFORM_ACTORS:
            claim = {
                "seq": event.seq,
                "author": payload.get("author") or event.actor,
                "by": "a person" if event.actor == task_events.PERSON else "an agent",
                "kind": event.event_type,
                "text": _cut(text),
            }
            if payload.get("verdict"):
                claim["verdict"] = payload["verdict"]
            if payload.get("verdict") == "needs_work":
                claim["rework_request"] = True
            claims.append(claim)
    record: dict[str, Any] = {"observations": observations, "claims": claims}
    if len(events) > MAX_EVENTS:
        record["omitted"] = (
            f"The first {len(events) - MAX_EVENTS} event(s) are left out; "
            f"the last {MAX_EVENTS} are shown."
        )
    return record


def read_task(
    engine: Any, company_id: int, task_id: int, _arguments: dict[str, Any]
) -> dict[str, Any] | None:
    with Session(engine) as session:
        item = _company_item(session, company_id, task_id)
        if item is None:
            return None
        return task_record(item, task_events.for_task(session, task_id))


# --- board__evidence --------------------------------------------------------------


def _files(evidence: dict[str, Any]) -> list[dict[str, Any]]:
    """The delivered files with the sha256 the backend sent and the daemon
    read back, and the tools' edits and deletions as the daemon reported them."""
    expected = evidence.get("expected") or {}
    files = [
        {
            "file": w.get("path"),
            "status": w.get("status"),
            "sha256_on_disk": w.get("sha256"),
            "sha256_sent": expected.get(w.get("path")),
        }
        for w in evidence.get("workspace_writes") or []
        if isinstance(w, dict)
    ]
    files += [
        {"file": f.get("path"), "status": "edited by a tool", "sha256": f.get("sha256")}
        for f in evidence.get("tool_edit_files") or []
        if isinstance(f, dict)
    ]
    files += [
        {"file": d.get("path"), "status": "deleted by a tool"}
        for d in evidence.get("tool_edit_deletions") or []
        if isinstance(d, dict)
    ]
    return files[:MAX_FILES]


def run_record(run: Any, verification: Any, attempts: list[int]) -> dict[str, Any]:
    """One run's evidence: the Verifier's observations, then the producer's claims."""
    observations: list[dict[str, Any]] = [
        {
            "attempt": run.version,
            "attempts_recorded": attempts,
            "producer": run.producer,
            "artifact_sha256": run.artifact_digest,
            "artifact_bytes": run.artifact_size,
            "ingested_at": _when(run.ingested_at),
        }
    ]
    evidence: dict[str, Any] = {}
    if verification is None:
        observations.append({"verification": "none recorded for this run"})
    else:
        evidence = verification.evidence or {}
        observations.append(
            {
                "verdict": verification.verdict,
                "observed_at": _when(verification.observed_at),
            }
        )
        for check in (verification.checks or [])[:MAX_CHECKS]:
            if isinstance(check, dict):
                observations.append(
                    {
                        "check": check.get("name"),
                        "outcome": check.get("outcome"),
                        "observation": _cut(str(check.get("observation") or "")),
                    }
                )
        observations += _files(evidence)
        if evidence.get("tools_offered"):
            observations.append({"tools_offered": _cut(evidence["tools_offered"])})
        loop = {
            k: evidence[k]
            for k in ("rounds_used", "rounds_max", "rounds_exhausted")
            if k in evidence
        }
        calls = [c for c in evidence.get("tool_calls") or [] if isinstance(c, dict)]
        if calls or loop:
            loop["tool_calls"] = len(calls)
            loop["tool_errors"] = sum(1 for c in calls if c.get("is_error"))
            observations.append(loop)
    claims: list[dict[str, Any]] = [
        {
            "source": "the producer's report",
            "model": run.model,
            "agent_role": run.agent_role,
            "claims": _cut(run.claims or {}),
            "deliverable_excerpt": _cut(run.artifact_text or "", ARTIFACT_CHARS),
        }
    ]
    return {"observations": observations, "claims": claims}


def read_evidence(
    engine: Any, company_id: int, task_id: int, arguments: dict[str, Any]
) -> dict[str, Any] | None:
    with Session(engine) as session:
        if _company_item(session, company_id, task_id) is None:
            return None
        runs = session.exec(
            select(AgentRun)
            .where(
                AgentRun.company_id == company_id,
                AgentRun.external_ref == f"task-{task_id}",
                AgentRun.producer == TASK_WORKER_PRODUCER,
            )
            .order_by(AgentRun.version)  # type: ignore[arg-type]
        ).all()
        attempts = [r.version for r in runs]
        if not runs:
            return {
                "observations": [{"runs": 0, "note": "no run recorded for this task"}]
            }
        wanted = arguments.get("attempt")
        run = runs[-1]
        if wanted not in (None, ""):
            run = next((r for r in runs if str(r.version) == str(wanted)), None)
            if run is None:
                return {
                    "observations": [
                        {"note": f"no attempt {wanted}", "attempts_recorded": attempts}
                    ]
                }
        verification = session.exec(
            select(Verification)
            .where(Verification.agent_run_id == run.id)
            .order_by(Verification.id.desc())  # type: ignore[union-attr]
        ).first()
        return run_record(run, verification, attempts)


# --- Scopes -----------------------------------------------------------------------


def task_ids(engine: Any, company_id: int, item_id: int) -> frozenset[int] | None:
    """The task and the tasks it depends on, when it is a task of the company."""
    with Session(engine) as session:
        item = _company_item(session, company_id, item_id)
        if item is None:
            return None
        return frozenset({int(item_id), *depends_on_of(item)})


def sprint_task_ids(
    engine: Any, company_id: int, item_id: int
) -> frozenset[int] | None:
    """Every task of the sprint *item_id* belongs to, when it is the company's."""
    with Session(engine) as session:
        item = _company_item(session, company_id, item_id)
        if item is None:
            return None
        ids = session.exec(
            select(BacklogItem.id).where(BacklogItem.sprint_id == item.sprint_id)
        ).all()
        return frozenset(int(i) for i in ids if i is not None)
