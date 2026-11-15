"""Which recorded runs wrote which files (issue #438).

A read of recorded observations, never a verdict: ``workspace_writes`` on a
verification is the daemon's per-file report of what it wrote (the sha256 it
read back from disk), so "this run wrote ``src/store.py``" comes from the
daemon, never from the agent's claims. The verdict shown beside it is the one
the Verifier already recorded for the run.

``runs_touching`` gives the latest task-worker runs of a company that wrote
any of the given paths (the Developer's prompt names them beside the related
code, ``services/tools/code_graph.py``); ``written_by_task`` the files a
task's own earlier runs wrote (the rework seeds, ``services/loop/worker/related.py``).

Both scan a bounded number of the company's latest runs (``SCAN_RUNS``) and
read the evidence in Python rather than with JSON operators in SQL, so the
same code runs on SQLite (the tests) and Postgres.
"""

from __future__ import annotations

from collections.abc import Iterable
from typing import Any

from models.agent_run import TASK_WORKER_PRODUCER, AgentRun, Verification
from sqlmodel import Session, select

SCAN_RUNS = 200
MAX_TASK_FILES = 50
_TASK_REF = "task-"


def _reports(
    session: Session, runs: list[AgentRun]
) -> dict[int, tuple[str, list[str]]]:
    """Per run: the latest verdict and the paths its latest daemon report says
    were written. A run with no report of what was written is left out."""
    ids = [run.id for run in runs]
    if not ids:
        return {}
    rows = session.exec(
        select(Verification)
        .where(Verification.agent_run_id.in_(ids))
        .order_by(Verification.id.desc())
    ).all()
    verdicts: dict[int, str] = {}
    written: dict[int, list[str]] = {}
    for row in rows:
        verdicts.setdefault(row.agent_run_id, row.verdict)
        writes = (row.evidence or {}).get("workspace_writes")
        if row.agent_run_id in written or not isinstance(writes, list):
            continue
        written[row.agent_run_id] = [
            w["path"]
            for w in writes
            if isinstance(w, dict)
            and w.get("status") == "written"
            and isinstance(w.get("path"), str)
        ]
    return {run_id: (verdicts[run_id], paths) for run_id, paths in written.items()}


def _latest_task_runs(
    session: Session, company_id: int, external_ref: str | None = None
) -> list[AgentRun]:
    query = select(AgentRun).where(
        AgentRun.company_id == company_id,
        AgentRun.producer == TASK_WORKER_PRODUCER,
    )
    if external_ref is not None:
        query = query.where(AgentRun.external_ref == external_ref)
    runs = session.exec(query.order_by(AgentRun.id.desc()).limit(SCAN_RUNS)).all()
    return [run for run in runs if (run.external_ref or "").startswith(_TASK_REF)]


def runs_touching(
    session: Session,
    company_id: int | None,
    paths: Iterable[str],
    limit: int = 5,
) -> list[dict[str, Any]]:
    """The latest task-worker runs of *company_id* the daemon reports wrote any
    of *paths*: ``{run_id, backlog_item_id, title, verdict, written,
    finished_at}``, newest first, at most *limit*. ``written`` is the given
    paths the run wrote."""
    wanted = {p for p in paths if isinstance(p, str) and p}
    if company_id is None or not wanted or limit <= 0:
        return []
    runs = _latest_task_runs(session, company_id)
    reports = _reports(session, runs)
    out: list[dict[str, Any]] = []
    for run in runs:
        verdict, written = reports.get(run.id, ("", []))
        hit = sorted(wanted.intersection(written))
        if not hit:
            continue
        subject = run.subject or {}
        out.append(
            {
                "run_id": run.id,
                "backlog_item_id": subject.get("backlog_item_id"),
                "title": subject.get("title"),
                "verdict": verdict,
                "written": hit,
                "finished_at": run.finished_at,
            }
        )
        if len(out) >= limit:
            break
    return out


def verified_writers(
    session: Session, company_id: int | None, paths: Iterable[str]
) -> dict[str, dict[str, Any] | None]:
    """Per path, the latest task-worker run whose latest verdict is
    ``verified`` and whose daemon report says it wrote the path:
    ``{run_id, backlog_item_id}``, or None when no such run is on record.

    What the sprint board shows beside a note that names a file (issue #439):
    an observation, kept apart from the note's claim."""
    wanted = [p for p in dict.fromkeys(paths) if isinstance(p, str) and p]
    found: dict[str, dict[str, Any] | None] = {p: None for p in wanted}
    if company_id is None or not wanted:
        return found
    runs = _latest_task_runs(session, company_id)
    reports = _reports(session, runs)
    for run in runs:
        verdict, written = reports.get(run.id, ("", []))
        if verdict != "verified":
            continue
        for path in written:
            if path in found and found[path] is None:
                found[path] = {
                    "run_id": run.id,
                    "backlog_item_id": (run.subject or {}).get("backlog_item_id"),
                }
        if all(found.values()):
            break
    return found


def written_by_task(session: Session, company_id: int, item_id: int) -> list[str]:
    """The files the daemon reports this task's earlier runs wrote, the latest
    run's first, at most ``MAX_TASK_FILES``."""
    runs = _latest_task_runs(session, company_id, f"{_TASK_REF}{item_id}")
    reports = _reports(session, runs)
    paths: list[str] = []
    for run in runs:
        for path in reports.get(run.id, ("", []))[1]:
            if path not in paths:
                paths.append(path)
    return paths[:MAX_TASK_FILES]
