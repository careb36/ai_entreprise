"""
Intake router — the requests that reach a company, until a person decides (issue #532).

Endpoints:
- ``GET  /v1/companies/{company_id}/intake``         — the items, newest first,
  by status (Viewer).
- ``POST /v1/companies/{company_id}/intake``         — send a request (Viewer).
  The source is the backend's: ``webhook`` for an API-token caller, ``brief``
  for a person signed in; never a field of the body.
- ``GET  /v1/companies/{company_id}/intake-record``  — the queue's numbers:
  the share closed with what it led to recorded, the median time to a
  decision, the Support agent's agreement with people per category (Viewer).
- ``GET  /v1/intake/{intake_id}``                    — one item with its history
  from the audit log (Viewer).
- ``POST /v1/intake/{intake_id}/accept|decline|duplicate|snooze|reopen`` — a
  person's decision (Manager).

Tickets arrive from ``POST /v1/support/report_bug`` with a ``company_id``,
chat requests from ``routers/chat_interactions.py`` (``request <text>``) and
the client's brief from ``POST /v1/specifications/draft``. Every move is
audited under ``intake:<company>:<item>`` (``services/planning/intake_queue.py``).
"""

from __future__ import annotations

from datetime import datetime
from typing import Literal

from auth_policy import current_user_if_required, require_company_role
from database import get_session
from fastapi import APIRouter, Depends, HTTPException, Query
from models import Company, User
from pydantic import Field
from schemas import RequestModel
from services.contracts import intake_rules as intake
from services.planning import intake_queue, intake_records
from services.planning.intake_queue import IntakeRefused
from sqlmodel import Session

router = APIRouter(prefix="/v1", tags=["Company"])


class IntakeCreate(RequestModel):
    title: str = Field(min_length=1, max_length=intake.TITLE_LIMIT)
    body: str = Field(default="", max_length=intake.BODY_LIMIT)


class Decision(RequestModel):
    category: str | None = Field(default=None, max_length=20)
    reason: str | None = Field(default=None, max_length=intake.REASON_LIMIT)


class AcceptRequest(Decision):
    target: Literal["task", "specification"]
    sprint_id: int | None = None
    assignee: str | None = Field(default=None, max_length=60)
    priority: str | None = Field(default=None, max_length=20)
    specification_id: int | None = None


class DeclineRequest(Decision):
    reason: str = Field(min_length=1, max_length=intake.REASON_LIMIT)


class DuplicateRequest(Decision):
    of_item_id: int | None = None
    of_task_id: int | None = None


class SnoozeRequest(Decision):
    until: datetime


def _actor(user: User | None) -> str:
    return user.email if user is not None else "local demo"


def _refused(exc: IntakeRefused) -> HTTPException:
    return HTTPException(status_code=exc.status, detail=exc.detail)


def _company(session: Session, company_id: int) -> None:
    if session.get(Company, company_id) is None:
        raise HTTPException(status_code=404, detail="Company not found")


@router.get("/companies/{company_id}/intake")
async def list_intake(
    company_id: int,
    status: str | None = Query(None),
    limit: int = Query(100, ge=1, le=500),
    session: Session = Depends(get_session),  # noqa: B008 - FastAPI dependency marker.
    user: User | None = Depends(current_user_if_required),  # noqa: B008 - FastAPI dependency marker.
):
    """The company's requests, newest first; ``status`` narrows the list."""
    _company(session, company_id)
    require_company_role(session, user, company_id, "Viewer")
    if status is not None and status not in intake.STATUSES:
        raise HTTPException(
            status_code=422, detail=f"Unknown status; use {', '.join(intake.STATUSES)}"
        )
    items = intake_records.items_of(session, company_id, status, limit)
    return {
        "items": [intake_records.serialize(i) for i in items],
        "pending": intake_records.pending_count(session, company_id),
        "statuses": list(intake.STATUSES),
        "categories": list(intake.CATEGORIES),
    }


@router.post("/companies/{company_id}/intake", status_code=201)
async def create_intake(
    company_id: int,
    req: IntakeCreate,
    session: Session = Depends(get_session),  # noqa: B008 - FastAPI dependency marker.
    user: User | None = Depends(current_user_if_required),  # noqa: B008 - FastAPI dependency marker.
):
    """A request from a company member or an integration holding an API token."""
    _company(session, company_id)
    require_company_role(session, user, company_id, "Viewer")
    # An API token's caller is a synthetic user with a negative id.
    via_token = getattr(user, "_api_token_company_id", None) is not None
    token_id = -user.id if via_token and user is not None and user.id else None
    try:
        item = intake_queue.receive(
            session,
            company_id,
            "webhook" if via_token else "brief",
            req.title,
            req.body,
            submitted_by=_actor(user),
            source_ref=f"api_token:{token_id}" if token_id else None,
        )
    except IntakeRefused as exc:
        raise _refused(exc) from exc
    session.commit()
    session.refresh(item)
    return intake_records.serialize(item)


@router.get("/companies/{company_id}/intake-record")
async def intake_record(
    company_id: int,
    session: Session = Depends(get_session),  # noqa: B008 - FastAPI dependency marker.
    user: User | None = Depends(current_user_if_required),  # noqa: B008 - FastAPI dependency marker.
):
    """The queue's numbers, counted from its items (never reported by anyone)."""
    _company(session, company_id)
    require_company_role(session, user, company_id, "Viewer")
    return intake_records.measures(session, company_id)


def _item_for(session: Session, user: User | None, item_id: int, role: str):
    try:
        item = (
            intake_queue.load_for_decision(session, item_id)
            if role == "Manager"
            else session.get(intake_queue.IntakeItem, item_id)
        )
    except IntakeRefused as exc:
        raise _refused(exc) from exc
    if item is None:
        raise HTTPException(status_code=404, detail="Intake item not found")
    require_company_role(session, user, item.company_id, role)
    return item


@router.get("/intake/{intake_id}")
async def get_intake(
    intake_id: int,
    session: Session = Depends(get_session),  # noqa: B008 - FastAPI dependency marker.
    user: User | None = Depends(current_user_if_required),  # noqa: B008 - FastAPI dependency marker.
):
    item = _item_for(session, user, intake_id, "Viewer")
    return intake_records.serialize(item) | {
        "history": intake_records.history(session, item)
    }


def _decided(session: Session, item) -> dict:
    session.commit()
    session.refresh(item)
    return intake_records.serialize(item) | {
        "history": intake_records.history(session, item)
    }


@router.post("/intake/{intake_id}/accept")
async def accept_intake(
    intake_id: int,
    req: AcceptRequest,
    session: Session = Depends(get_session),  # noqa: B008 - FastAPI dependency marker.
    user: User | None = Depends(current_user_if_required),  # noqa: B008 - FastAPI dependency marker.
):
    """Accept as a task in an open sprint, or into a specification revision."""
    item = _item_for(session, user, intake_id, "Manager")
    try:
        intake_queue.accept(
            session,
            item,
            _actor(user),
            target=req.target,
            category=req.category,
            reason=req.reason,
            sprint_id=req.sprint_id,
            assignee=req.assignee,
            priority=req.priority,
            specification_id=req.specification_id,
        )
    except IntakeRefused as exc:
        session.rollback()
        raise _refused(exc) from exc
    return _decided(session, item)


@router.post("/intake/{intake_id}/decline")
async def decline_intake(
    intake_id: int,
    req: DeclineRequest,
    session: Session = Depends(get_session),  # noqa: B008 - FastAPI dependency marker.
    user: User | None = Depends(current_user_if_required),  # noqa: B008 - FastAPI dependency marker.
):
    item = _item_for(session, user, intake_id, "Manager")
    try:
        intake_queue.decline(session, item, _actor(user), req.reason, req.category)
    except IntakeRefused as exc:
        session.rollback()
        raise _refused(exc) from exc
    return _decided(session, item)


@router.post("/intake/{intake_id}/duplicate")
async def duplicate_intake(
    intake_id: int,
    req: DuplicateRequest,
    session: Session = Depends(get_session),  # noqa: B008 - FastAPI dependency marker.
    user: User | None = Depends(current_user_if_required),  # noqa: B008 - FastAPI dependency marker.
):
    item = _item_for(session, user, intake_id, "Manager")
    try:
        intake_queue.mark_duplicate(
            session,
            item,
            _actor(user),
            of_item_id=req.of_item_id,
            of_task_id=req.of_task_id,
            category=req.category,
            reason=req.reason,
        )
    except IntakeRefused as exc:
        session.rollback()
        raise _refused(exc) from exc
    return _decided(session, item)


@router.post("/intake/{intake_id}/snooze")
async def snooze_intake(
    intake_id: int,
    req: SnoozeRequest,
    session: Session = Depends(get_session),  # noqa: B008 - FastAPI dependency marker.
    user: User | None = Depends(current_user_if_required),  # noqa: B008 - FastAPI dependency marker.
):
    item = _item_for(session, user, intake_id, "Manager")
    try:
        intake_queue.snooze(
            session, item, _actor(user), req.until, req.category, req.reason
        )
    except IntakeRefused as exc:
        session.rollback()
        raise _refused(exc) from exc
    return _decided(session, item)


@router.post("/intake/{intake_id}/reopen")
async def reopen_intake(
    intake_id: int,
    session: Session = Depends(get_session),  # noqa: B008 - FastAPI dependency marker.
    user: User | None = Depends(current_user_if_required),  # noqa: B008 - FastAPI dependency marker.
):
    """A snoozed, declined or duplicate request back to pending."""
    item = _item_for(session, user, intake_id, "Manager")
    try:
        intake_queue.reopen(session, item, _actor(user))
    except IntakeRefused as exc:
        session.rollback()
        raise _refused(exc) from exc
    return _decided(session, item)
