"""The record tools' scope and readers: what ``board__task`` and
``board__evidence`` read, for which turns (issue #766, family E).

``services/tools/board_tools.py`` holds the tools; it may not read the tasks
context, so the readers are here, where both the task events and the runs'
verifications may be read. ``offer`` puts the tools beside a turn's toolbox:

- the QA review of a delivery (``qa_review_reads``), the Architect's brief
  (``architect_brief``) and an execution turn on a rework (``execute``), each
  scoped to the turn's company and its own task plus the tasks it depends on
  (``task_scope``, read from the database, never from the call's arguments).
  They go beside the turn's own tools (within its rounds) and never turn a
  plain call into a tool loop: a turn offered no tools gets none.

The readers split every record into ``observations`` (what the platform wrote
or computed: the events' kinds, actors, statuses and times, the system's and
the gates' own text, the Verifier's checks, the files' sha256 the backend sent
or the daemon read back, the tools offered, the loop's round count) and
``claims`` (what an agent or a person wrote: comments, verdicts, rework
requests, the plan's words, the producer's model, role, claims and
deliverable). Both are bounded here; ``board_tools`` redacts and cuts the reply.
"""

from __future__ import annotations

from typing import Any

from config import settings
from logging_config import setup_logger
from models import AgentRun, BacklogItem, Sprint, Verification
from models.agent_run import TASK_WORKER_PRODUCER
from sqlmodel import Session, select

from services.contracts.task_dependencies import depends_on_of
from services.tasks import task_events
from services.tools import board_tools

logger = setup_logger("fabrique-task-worker")

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


# --- Scope and offer --------------------------------------------------------------


def task_scope(engine: Any, company_id: int, item_id: int) -> board_tools.Scope | None:
    """The task and the tasks it depends on, when it is a task of the company."""
    with Session(engine) as session:
        item = _company_item(session, company_id, item_id)
        if item is None:
            return None
        ids = {int(item_id), *depends_on_of(item)}
    return board_tools.Scope(company_id, frozenset(ids))


def toolbox_for_task(
    company_id: int | None, item_id: int | None, *, rounds: int, engine: Any
) -> board_tools.BoardToolbox | None:
    """The record tools scoped to the task, or ``None`` when they are off,
    there are no rounds or the task is not the company's."""
    if not settings.BOARD_TOOLS_ENABLED or rounds <= 0:
        return None
    if company_id is None or item_id is None:
        return None
    try:
        scope = task_scope(engine, company_id, item_id)
    except Exception as exc:  # noqa: BLE001 - Without a scope, no record tools.
        logger.warning("No record tools for item=%s: %s", item_id, exc)
        return None
    if scope is None:
        return None
    return board_tools.BoardToolbox(
        scope,
        lambda c, t, a: read_task(engine, c, t, a),
        lambda c, t, a: read_evidence(engine, c, t, a),
        rounds=rounds,
    )


def offer(
    toolbox: Any,
    company_id: int | None,
    item_id: int | None,
    *,
    rounds: int,
    engine: Any,
) -> Any:
    """*toolbox* with the record tools of the task beside it (``board_tools.attach``).
    They go beside a turn's tools and never make a plain call a tool loop:
    without a toolbox (no daemon, no recorded delivery) there is none."""
    if toolbox is None:
        return None
    board = toolbox_for_task(company_id, item_id, rounds=rounds, engine=engine)
    return board_tools.attach(toolbox, board)


def on_rework(
    toolbox: Any,
    company_id: int | None,
    item: dict[str, Any],
    limits: dict[str, Any],
    engine: Any,
) -> Any:
    """An execution turn on a rework: its toolbox with the task's record tools,
    within the model's rounds and result budget (``model_profile``); none
    for a turn without tools, as in ``offer``."""
    if toolbox is None:
        return None
    board = toolbox_for_task(
        company_id,
        item.get("id"),
        rounds=int(limits.get("tool_rounds") or 0),
        engine=engine,
    )
    if board is not None and limits.get("result_max_chars"):
        board.result_max_chars = int(limits["result_max_chars"])
    return board_tools.attach(toolbox, board)
