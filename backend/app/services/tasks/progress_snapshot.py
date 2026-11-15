"""A sprint's progress, live or sealed at its close (issue #531).

``live`` builds the series of an open sprint from its task events now
(``services/tasks/progress.build``). ``seal_at_close`` stores the series and
the retrospective's numbers as a ``SprintProgressSnapshot`` when a sprint
closes; the backend computes the snapshot's sha256 (``digest_of``) and writes
it into the company's audit chain (``AgentService.audit_interaction``, under
``sprint:<id>:progress``), never accepting a digest from anyone else. A
reopened sprint keeps its first snapshot: its next close adds one with the
next ``close_number``. ``verify`` recomputes the digest, compares it with the
audit row and walks the chain.

``claim_gap`` is the claimed-but-not-verified count over several sprints,
from each sprint's first snapshot or, without one, from its events: the
number the procedures are measured against (``services/learning/procedures``).

The verdict of a ``verified`` event is read from its payload
(``progress.VERIFICATION_KEY``, written with the event since #531); an
older event without it is given the verdict of the Verification it names, or
else the latest Verification of its run observed before the event. A
Verification row is never edited, so the answer does not change later.
"""

from __future__ import annotations

import hashlib
import hmac
import logging
from collections.abc import Iterable
from typing import Any

import audit_chain
from models import AuditLog, BacklogItem, Sprint, SprintProgressSnapshot, TaskEvent
from models.agent_run import Verification
from sqlalchemy import func
from sqlmodel import Session, select

from services.platform.agent_service import AgentService
from services.tasks import progress

logger = logging.getLogger("fabrique-backend")

SNAPSHOT_VERSION = 1
AUDIT_ACTION = "sprint_progress_sealed"
DIGEST_PREFIX = "sha256:"
# The retrospective's numbers a snapshot keeps (``services/learning/retrospective.build``):
# counts only, no text.
RETRO_FIELDS = (
    "total_tasks",
    "done_tasks",
    "blocked_tasks",
    "awaiting_person_tasks",
    "completion_rate",
    "rework_cycles",
    "qa_failures",
    "contradiction_rework",
)


def interaction_id(sprint_id: int) -> str:
    return f"sprint:{sprint_id}:progress"


# ── Reading the events ───────────────────────────────────────────────────────


def events_of(session: Session, sprint_ids: list[int]) -> dict[int, list[TaskEvent]]:
    """The task events of several sprints in one query, by sprint, in order."""
    grouped: dict[int, list[TaskEvent]] = {sid: [] for sid in sprint_ids}
    if not sprint_ids:
        return grouped
    for event in session.exec(
        select(TaskEvent)
        .where(TaskEvent.sprint_id.in_(sprint_ids))  # type: ignore[union-attr]
        .where(TaskEvent.task_id.is_not(None))  # type: ignore[union-attr]
        .order_by(TaskEvent.id)  # type: ignore[arg-type]
    ).all():
        grouped.setdefault(event.sprint_id, []).append(event)
    return grouped


def _naive(value: Any) -> Any:
    return value.replace(tzinfo=None) if value is not None else None


def _fallback_verdicts(
    session: Session, events: list[TaskEvent]
) -> dict[int, str | None]:
    """The verdict of each ``verified`` event whose payload does not carry one."""
    missing = [
        e
        for e in events
        if e.event_type == "verified"
        and (e.payload or {}).get(progress.VERIFICATION_KEY) not in progress.VERDICTS
        and (e.verification_id is not None or e.run_id is not None)
    ]
    if not missing:
        return {}
    run_ids = {e.run_id for e in missing if e.run_id is not None}
    ids = {e.verification_id for e in missing if e.verification_id is not None}
    query = select(Verification)
    if run_ids and ids:
        query = query.where(
            Verification.agent_run_id.in_(run_ids) | Verification.id.in_(ids)  # type: ignore[attr-defined]
        )
    elif run_ids:
        query = query.where(Verification.agent_run_id.in_(run_ids))  # type: ignore[attr-defined]
    else:
        query = query.where(Verification.id.in_(ids))  # type: ignore[union-attr]
    rows = list(session.exec(query.order_by(Verification.id)).all())  # type: ignore[arg-type]
    by_id = {v.id: v for v in rows}
    verdicts: dict[int, str | None] = {}
    for event in missing:
        if event.verification_id in by_id:
            verdicts[event.id] = by_id[event.verification_id].verdict
            continue
        at = _naive(event.created_at)
        before = [
            v
            for v in rows
            if v.agent_run_id == event.run_id
            and (at is None or _naive(v.observed_at) <= at)
        ]
        verdicts[event.id] = before[-1].verdict if before else None
    return verdicts


def normalised(session: Session, events: list[TaskEvent]) -> list[dict[str, Any]]:
    """The events as ``progress.build`` reads them, with older verdicts filled in."""
    fallback = _fallback_verdicts(session, events)
    rows = []
    for event in events:
        row = progress.normalise(event)
        if row["verification"] is None and fallback.get(event.id) in progress.VERDICTS:
            row["verification"] = fallback[event.id]
        rows.append(row)
    return rows


def task_ids_of(session: Session, sprint_ids: list[int]) -> dict[int, list[int]]:
    grouped: dict[int, list[int]] = {sid: [] for sid in sprint_ids}
    if not sprint_ids:
        return grouped
    for task_id, sprint_id in session.exec(
        select(BacklogItem.id, BacklogItem.sprint_id).where(
            BacklogItem.sprint_id.in_(sprint_ids)  # type: ignore[attr-defined]
        )
    ).all():
        grouped.setdefault(sprint_id, []).append(task_id)
    return grouped


def live(session: Session, sprint_id: int, granularity: str = "day") -> dict[str, Any]:
    """The sprint's series from its events as they are now."""
    events = events_of(session, [sprint_id])[sprint_id]
    return progress.build(
        normalised(session, events),
        task_ids_of(session, [sprint_id])[sprint_id],
        sprint_id=sprint_id,
        granularity=granularity,
    )


# ── Snapshots ────────────────────────────────────────────────────────────────


def snapshot_body(
    company_id: int,
    sprint_id: int,
    close_number: int,
    series: dict[str, Any],
    retrospective: dict[str, Any],
) -> dict[str, Any]:
    """What the digest covers: the snapshot's whole content and its place."""
    return {
        "v": SNAPSHOT_VERSION,
        "company_id": company_id,
        "sprint_id": sprint_id,
        "close_number": close_number,
        "series": series,
        "retrospective": retrospective,
    }


def digest_of(body: dict[str, Any]) -> str:
    return DIGEST_PREFIX + hashlib.sha256(progress.canonical_bytes(body)).hexdigest()


def retro_numbers(metrics: dict[str, Any] | None) -> dict[str, Any]:
    metrics = metrics or {}
    return {k: metrics[k] for k in RETRO_FIELDS if isinstance(metrics.get(k), int)}


def snapshots_of(session: Session, sprint_id: int) -> list[SprintProgressSnapshot]:
    return list(
        session.exec(
            select(SprintProgressSnapshot)
            .where(SprintProgressSnapshot.sprint_id == sprint_id)
            .order_by(SprintProgressSnapshot.close_number)  # type: ignore[arg-type]
        ).all()
    )


def seal(
    session: Session, sprint: Sprint, retrospective: dict[str, Any] | None
) -> SprintProgressSnapshot:
    """Store the sprint's series and retrospective numbers as its next snapshot,
    and seal its digest in the audit chain; commits."""
    close_number = (
        session.exec(
            select(func.max(SprintProgressSnapshot.close_number)).where(
                SprintProgressSnapshot.sprint_id == sprint.id
            )
        ).one()
        or 0
    ) + 1
    series = live(session, sprint.id)
    numbers = retro_numbers(retrospective)
    snapshot = SprintProgressSnapshot(
        company_id=sprint.company_id,
        sprint_id=sprint.id,
        close_number=close_number,
        series=series,
        retrospective=numbers,
        digest=digest_of(
            snapshot_body(sprint.company_id, sprint.id, close_number, series, numbers)
        ),
    )
    session.add(snapshot)
    session.flush()
    final = series["final"]
    audit = AgentService().audit_interaction(
        session=session,
        company_id=sprint.company_id,
        interaction_id=interaction_id(sprint.id),
        agent_id="system:task_worker",
        action=AUDIT_ACTION,
        details={
            "company_id": str(sprint.company_id),
            "sprint_id": sprint.id,
            "snapshot_id": snapshot.id,
            "close_number": close_number,
        },
        result={
            "digest": snapshot.digest,
            "tasks": series["tasks"],
            "claimed": final["claimed"],
            "verified": final["verified"],
            "gap": final["gap"],
        },
        decision_signature=None,
        cost_tokens=0,
    )
    snapshot.audit_log_id = audit.id
    session.add(snapshot)
    session.commit()
    session.refresh(snapshot)
    return snapshot


def seal_at_close(
    engine: Any, sprint_id: int, retrospective: dict[str, Any] | None
) -> SprintProgressSnapshot | None:
    """``seal`` in a session of its own, once the sprint is closed; best-effort,
    a failure never undoes the close."""
    try:
        with Session(engine) as session:
            sprint = session.get(Sprint, sprint_id)
            if sprint is None or sprint.status != "completed":
                return None
            return seal(session, sprint, retrospective)
    except Exception as exc:  # noqa: BLE001 - The snapshot is a record; the close stands.
        logger.warning("Progress snapshot failed for sprint=%s: %s", sprint_id, exc)
        return None


def verify(
    session: Session,
    snapshot: SprintProgressSnapshot,
    chain_status: str | None = None,
) -> dict[str, Any]:
    """Whether the stored snapshot still matches its digest, the audit row
    that sealed it (chained in the company's audit chain), and whether that
    chain is intact; *chain_status*, when given, is that walk done once for
    several snapshots of one company."""
    recomputed = digest_of(
        snapshot_body(
            snapshot.company_id,
            snapshot.sprint_id,
            snapshot.close_number,
            snapshot.series or {},
            snapshot.retrospective or {},
        )
    )
    digest_ok = hmac.compare_digest(recomputed, snapshot.digest or "")
    row = (
        session.get(AuditLog, snapshot.audit_log_id)
        if snapshot.audit_log_id is not None
        else None
    )
    if row is None:
        audit = "missing"
    else:
        sealed = (row.result or {}) if isinstance(row.result, dict) else {}
        details = (row.details or {}) if isinstance(row.details, dict) else {}
        audit = (
            "match"
            if row.company_id == snapshot.company_id
            and row.chain_seq is not None
            and row.action == AUDIT_ACTION
            and details.get("snapshot_id") == snapshot.id
            and hmac.compare_digest(str(sealed.get("digest") or ""), snapshot.digest)
            else "mismatch"
        )
    if chain_status is None:
        chain_status = audit_chain.verify_chain(session, snapshot.company_id)["status"]
    return {
        "digest": "match" if digest_ok else "mismatch",
        "audit": audit,
        "chain": chain_status,
        "intact": digest_ok and audit == "match" and chain_status == "intact",
    }


def as_dict(
    snapshot: SprintProgressSnapshot, verification: dict[str, Any] | None = None
) -> dict[str, Any]:
    out = {
        "id": snapshot.id,
        "sprint_id": snapshot.sprint_id,
        "close_number": snapshot.close_number,
        "digest": snapshot.digest,
        "audit_log_id": snapshot.audit_log_id,
        "created_at": snapshot.created_at.isoformat() if snapshot.created_at else None,
        "retrospective": snapshot.retrospective or {},
        "final": (snapshot.series or {}).get("final"),
    }
    if verification is not None:
        out["verification"] = verification
    return out


# ── The number procedures are measured against ───────────────────────────────


def claim_gap(session: Session, sprint_ids: Iterable[int]) -> dict[str, int]:
    """Tasks claimed delivered and not verified, summed over *sprint_ids*:
    each sprint's first snapshot when it has one, else its events now."""
    ids = list(dict.fromkeys(sprint_ids))
    if not ids:
        return {"tasks": 0, "claimed": 0, "verified": 0, "gap": 0}
    first: dict[int, dict[str, Any]] = {}
    for snap in session.exec(
        select(SprintProgressSnapshot)
        .where(SprintProgressSnapshot.sprint_id.in_(ids))  # type: ignore[attr-defined]
        .order_by(SprintProgressSnapshot.close_number)  # type: ignore[arg-type]
    ).all():
        first.setdefault(snap.sprint_id, snap.series or {})
    rest = [sid for sid in ids if sid not in first]
    events = events_of(session, rest)
    tasks = task_ids_of(session, rest)
    for sid in rest:
        first[sid] = progress.build(
            normalised(session, events[sid]), tasks[sid], sprint_id=sid
        )
    totals = {"tasks": 0, "claimed": 0, "verified": 0, "gap": 0}
    for series in first.values():
        final = series.get("final") or {}
        totals["tasks"] += int(series.get("tasks") or 0)
        for key in ("claimed", "verified", "gap"):
            totals[key] += int(final.get(key) or 0)
    return totals
