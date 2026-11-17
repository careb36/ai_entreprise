"""The self-improvement cycles of a company (``services/management/rsi_cycle.py``;
``docs/design/rsi-loop.md``, PR 10 of section 8).

- ``GET  /v1/companies/{id}/rsi/cycles``                    the loop's state and its cycles (Viewer)
- ``GET  /v1/companies/{id}/rsi/cycles/{rsi_cycle_id}``     one cycle (Viewer)
- ``POST /v1/companies/{id}/rsi/resume``                    resume after the stop (a signed-in Owner)
- ``POST /v1/companies/{id}/rsi/cycles/{rsi_cycle_id}/merge``  record the person's merge at C (a signed-in Owner)
- ``POST /v1/companies/{id}/rsi/cycles/import``             import a cycle runner's record (a signed-in Owner)

No route starts a cycle or records a decision: the runner does, under the
backend's limits. An API token is refused like a Manager on every action,
and the Manager agent never merges a self-change (``merge_self_change`` is in
``ALWAYS_ESCALATE``). Every action, and every refusal, is audited.
"""

from __future__ import annotations

from typing import Any

from auth_policy import current_user_if_required, require_company_role
from database import get_session
from fastapi import APIRouter, Depends, HTTPException
from models import RsiCycle, User
from pydantic import Field
from schemas import STRICT, RequestModel
from services.management import rsi_cycle, rsi_cycle_import, rsi_cycle_view
from sqlmodel import Session

from routers.sod import owner_person

router = APIRouter(prefix="/v1", tags=["Company"])


class RsiMergeRecord(RequestModel):
    """The merge a person made of a kept cycle's pull request."""

    model_config = STRICT

    merged_sha: str = Field(min_length=40, max_length=40)


class RsiRecordImport(RequestModel):
    """A record written by ``scripts/rsi/cycle.py`` (schema version 1)."""

    model_config = STRICT

    record: dict[str, Any]


def _http(exc: rsi_cycle.CycleRefused) -> HTTPException:
    return HTTPException(status_code=exc.status, detail=exc.detail)


def _cycle(session: Session, company_id: int, rsi_cycle_id: int) -> RsiCycle:
    row = session.get(RsiCycle, rsi_cycle_id)
    if row is None or row.company_id != company_id:
        raise HTTPException(status_code=404, detail="Cycle not found")
    return row


@router.get("/companies/{company_id}/rsi/cycles")
def list_rsi_cycles(
    company_id: int,
    session: Session = Depends(get_session),  # noqa: B008 - FastAPI dependency marker.
    user: User | None = Depends(current_user_if_required),  # noqa: B008 - FastAPI dependency marker.
):
    """The loop's switches, limits and counts, and the company's cycles, newest first."""
    require_company_role(session, user, company_id, "Viewer")
    return {
        "state": rsi_cycle_view.state(session, company_id),
        "cycles": [
            rsi_cycle_view.serialize(c)
            for c in rsi_cycle_view.list_cycles(session, company_id)
        ],
    }


@router.get("/companies/{company_id}/rsi/cycles/{rsi_cycle_id}")
def get_rsi_cycle(
    company_id: int,
    rsi_cycle_id: int,
    session: Session = Depends(get_session),  # noqa: B008 - FastAPI dependency marker.
    user: User | None = Depends(current_user_if_required),  # noqa: B008 - FastAPI dependency marker.
):
    """One cycle, with the runner's record when it was imported."""
    require_company_role(session, user, company_id, "Viewer")
    row = _cycle(session, company_id, rsi_cycle_id)
    return {**rsi_cycle_view.serialize(row), "record": row.record}


@router.post("/companies/{company_id}/rsi/resume")
def resume_rsi(
    company_id: int,
    session: Session = Depends(get_session),  # noqa: B008 - FastAPI dependency marker.
    user: User | None = Depends(current_user_if_required),  # noqa: B008 - FastAPI dependency marker.
):
    """Resume the loop after N cycles without a keep (a signed-in Owner; audited)."""
    actor = owner_person(session, user, company_id)
    try:
        resumed = rsi_cycle.resume(session, company_id, actor)
    except rsi_cycle.CycleRefused as exc:
        raise _http(exc) from exc
    return {**rsi_cycle_view.state(session, company_id), **resumed}


@router.post("/companies/{company_id}/rsi/cycles/import")
def import_rsi_record(
    company_id: int,
    payload: RsiRecordImport,
    session: Session = Depends(get_session),  # noqa: B008 - FastAPI dependency marker.
    user: User | None = Depends(current_user_if_required),  # noqa: B008 - FastAPI dependency marker.
):
    """Import a runner's record once it validates and its kept files' hashes
    match what the backend computes (a signed-in Owner; audited)."""
    actor = owner_person(session, user, company_id)
    try:
        row = rsi_cycle_import.import_record(
            session, company_id, payload.record, person=actor
        )
    except rsi_cycle.CycleRefused as exc:
        raise _http(exc) from exc
    return rsi_cycle_view.serialize(row)


@router.post("/companies/{company_id}/rsi/cycles/{rsi_cycle_id}/merge")
def record_rsi_merge(
    company_id: int,
    rsi_cycle_id: int,
    payload: RsiMergeRecord,
    session: Session = Depends(get_session),  # noqa: B008 - FastAPI dependency marker.
    user: User | None = Depends(current_user_if_required),  # noqa: B008 - FastAPI dependency marker.
):
    """Record the merge a person made, pinned to C (a signed-in Owner; audited)."""
    actor = owner_person(session, user, company_id)
    row = _cycle(session, company_id, rsi_cycle_id)
    try:
        rsi_cycle.record_person_merge(session, row, payload.merged_sha, actor)
    except rsi_cycle.CycleRefused as exc:
        raise _http(exc) from exc
    return rsi_cycle_view.serialize(row)
