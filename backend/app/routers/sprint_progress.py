"""
Sprint progress router — claimed done versus verified, from the task events (issue #531).

Endpoints:
- ``GET /v1/sprints/{sprint_id}/progress``        — an open sprint's series
  built from its events now; a closed sprint's sealed snapshot, with every
  snapshot of the sprint and whether each still matches its digest, the
  audit row that sealed it and an intact audit chain (Viewer).
- ``GET /v1/specifications/{spec_id}/progress``   — per requirement of a
  specification, across the sprints planned from it, the tasks covering it
  and how many were claimed, done and verified, or that none covers it
  (Viewer).

Both read only; the series is built by ``services/tasks/progress.build`` and
the snapshots are written when a sprint closes (``progress_snapshot.seal_at_close``).
"""

from typing import Literal

import audit_chain
from auth_policy import current_user_if_required, require_company_role
from database import get_session
from fastapi import APIRouter, Depends, HTTPException, Query
from models import BacklogItem, Specification, Sprint, User
from services.tasks import progress, progress_snapshot
from sqlmodel import Session, select

router = APIRouter(prefix="/v1", tags=["Core"])

CLOSED = "completed"


@router.get("/sprints/{sprint_id}/progress")
async def sprint_progress(
    sprint_id: int,
    granularity: Literal["day", "event"] = Query("day"),
    session: Session = Depends(get_session),  # noqa: B008 - FastAPI dependency marker.
    user: User | None = Depends(current_user_if_required),  # noqa: B008 - FastAPI dependency marker.
):
    """The live series of an open sprint, the latest snapshot of a closed one."""
    sprint = session.get(Sprint, sprint_id)
    if sprint is None:
        raise HTTPException(status_code=404, detail="Sprint not found")
    require_company_role(session, user, sprint.company_id, "Viewer")
    snapshots = progress_snapshot.snapshots_of(session, sprint.id)
    # The company's audit chain is walked once for every snapshot.
    chain = (
        audit_chain.verify_chain(session, sprint.company_id)["status"]
        if snapshots
        else None
    )
    listed = [
        progress_snapshot.as_dict(s, progress_snapshot.verify(session, s, chain))
        for s in snapshots
    ]
    if sprint.status == CLOSED and snapshots:
        latest = snapshots[-1]
        return {
            "sprint_id": sprint.id,
            "status": sprint.status,
            "source": "snapshot",
            "series": latest.series,
            "retrospective": latest.retrospective or {},
            "snapshot": listed[-1],
            "snapshots": listed,
        }
    return {
        "sprint_id": sprint.id,
        "status": sprint.status,
        "source": "live",
        "series": progress_snapshot.live(session, sprint.id, granularity),
        "retrospective": None,
        "snapshot": None,
        "snapshots": listed,
    }


@router.get("/specifications/{spec_id}/progress")
async def specification_progress(
    spec_id: int,
    session: Session = Depends(get_session),  # noqa: B008 - FastAPI dependency marker.
    user: User | None = Depends(current_user_if_required),  # noqa: B008 - FastAPI dependency marker.
):
    """Per requirement: tasks covering it, claimed, done and verified."""
    spec = session.get(Specification, spec_id)
    if spec is None:
        raise HTTPException(status_code=404, detail="Specification not found")
    require_company_role(session, user, spec.company_id, "Viewer")
    sprint_ids = list(
        session.exec(
            select(Sprint.id)
            .where(Sprint.specification_id == spec.id)
            .where(Sprint.company_id == spec.company_id)
            .order_by(Sprint.id)  # type: ignore[arg-type]
        ).all()
    )
    events = progress_snapshot.events_of(session, sprint_ids)
    tasks = progress_snapshot.task_ids_of(session, sprint_ids)
    by_task: list[dict] = []
    for sid in sprint_ids:
        series = progress.build(
            progress_snapshot.normalised(session, events[sid]),
            tasks[sid],
            sprint_id=sid,
        )
        by_task.extend(series["by_task"])
    covers = {
        item.id: item.covers if isinstance(item.covers, list) else []
        for item in session.exec(
            select(BacklogItem).where(BacklogItem.sprint_id.in_(sprint_ids))  # type: ignore[attr-defined]
        ).all()
    }
    requirement_ids = [
        str(r["id"])
        for r in spec.requirements or []
        if isinstance(r, dict) and r.get("id")
    ]
    return {
        "specification_id": spec.id,
        "sprint_ids": sprint_ids,
        **progress.coverage(requirement_ids, covers, by_task),
    }
