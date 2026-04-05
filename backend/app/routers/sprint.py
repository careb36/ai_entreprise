"""
Sprint router — Kanban board management and backlog item lifecycle.

Endpoints:
- ``POST /v1/start_sprint``          — create a sprint with initial backlog items.
- ``GET  /v1/sprints/{id}/board``    — retrieve sprint metadata and Kanban columns.
- ``PATCH /v1/backlog/{item_id}``    — update status, priority, assignee, or add a comment.

New backlog items are dispatched to agents via the transactional outbox:
an ``OutboxEvent`` row is inserted atomically in the same DB transaction and
delivered to NATS by the ``process_outbox`` background worker.
"""

import json
from datetime import datetime
from uuid import uuid4

from fastapi import APIRouter, HTTPException, Depends
from sqlmodel import Session, select, text

from database import get_session
from models import Sprint, BacklogItem, OutboxEvent
from schemas import SprintStartRequest, BacklogItemUpdate
from logging_config import logger

router = APIRouter(prefix="/v1", tags=["Core"])

# Allowed values for validation; rejected values return HTTP 422.
VALID_STATUSES = {"todo", "in_progress", "in_review", "done", "blocked"}
VALID_PRIORITIES = {"low", "medium", "high", "critical"}


@router.post("/start_sprint")
async def start_sprint(req: SprintStartRequest, session: Session = Depends(get_session)):
    """
    Create a sprint and its initial backlog items in a single transaction.

    For each backlog item an ``OutboxEvent`` is inserted so the background
    worker can publish a ``task.assign`` NATS message once the DB transaction
    commits.  This guarantees at-least-once delivery without a distributed
    transaction between Postgres and NATS.
    """
    sprint = Sprint(
        company_id=req.company_id,
        sprint_name=req.sprint_name,
        start_date=req.start_date,
        duration_days=req.duration_days,
    )
    session.add(sprint)
    session.flush()  # Obtain sprint.id before processing backlog items

    item_ids = []
    for item in req.backlog_items:
        backlog_item = BacklogItem(
            sprint_id=sprint.id,
            title=item.get("title", ""),
            description=item.get("description", ""),
        )
        session.add(backlog_item)
        session.flush()
        item_ids.append(backlog_item.id)

        # Outbox entry — delivered to NATS by the process_outbox background worker.
        nats_payload = {
            "subject": "task.assign",
            "message": {
                "backlog_item_id": backlog_item.id,
                "interaction_id": str(uuid4()),
                "agent": "PO",
                "sprint_id": sprint.id,
            },
        }
        session.add(OutboxEvent(
            aggregate_type="backlog_item",
            aggregate_id=str(backlog_item.id),
            event_type="nats_publish",
            payload=json.dumps(nats_payload),
        ))

    session.commit()
    return {
        "sprint_id": sprint.id,
        "backlog_item_ids": item_ids,
        "sprint_url": f"/v1/sprints/{sprint.id}/board",
    }


@router.get("/sprints/{sprint_id}/board")
async def get_sprint_board(sprint_id: int, session: Session = Depends(get_session)):
    """
    Return sprint metadata and all backlog items grouped by Kanban column.

    Items whose ``status`` value is not in ``VALID_STATUSES`` are placed in
    the ``"todo"`` column to avoid breaking the frontend rendering.
    """
    sprint = session.get(Sprint, sprint_id)
    if not sprint:
        raise HTTPException(status_code=404, detail="Sprint not found")

    sprint_data = {
        "id": sprint.id,
        "sprint_name": sprint.sprint_name,
        "start_date": sprint.start_date,
        "duration_days": sprint.duration_days,
        "status": sprint.status,
        "goal": sprint.goal,
    }

    items_db = session.exec(
        select(BacklogItem).where(BacklogItem.sprint_id == sprint_id)
    ).all()

    items = [
        {
            "id": i.id,
            "title": i.title,
            "description": i.description,
            "status": i.status,
            "priority": i.priority,
            "assignee": i.assignee,
            "comments": i.comments or [],
            "created_at": str(i.created_at),
            "updated_at": str(i.updated_at),
        }
        for i in items_db
    ]

    # Pre-populate all columns so the frontend always receives a complete dict.
    columns = {s: [] for s in ["todo", "in_progress", "in_review", "done", "blocked"]}
    for item in items:
        col = item["status"] if item["status"] in columns else "todo"
        columns[col].append(item)

    return {"sprint": sprint_data, "columns": columns, "total": len(items)}


@router.patch("/backlog/{item_id}")
async def update_backlog_item(item_id: int, req: BacklogItemUpdate, session: Session = Depends(get_session)):
    """
    Partially update a backlog item.

    Supports updating ``status``, ``priority``, ``assignee``, or appending a
    new comment.  All fields are optional; only provided fields are changed.
    Invalid ``status`` or ``priority`` values return HTTP 422.
    """
    if req.status and req.status not in VALID_STATUSES:
        raise HTTPException(status_code=422, detail=f"Invalid status. Valid: {VALID_STATUSES}")
    if req.priority and req.priority not in VALID_PRIORITIES:
        raise HTTPException(status_code=422, detail=f"Invalid priority. Valid: {VALID_PRIORITIES}")

    item = session.get(BacklogItem, item_id)
    if not item:
        raise HTTPException(status_code=404, detail="Backlog item not found")

    if req.status:
        item.status = req.status
    if req.priority:
        item.priority = req.priority
    if req.assignee is not None:
        item.assignee = req.assignee
    if req.comment:
        # Append the new comment to the existing JSON list.
        comments = list(item.comments or [])
        comments.append({
            "author": "user",
            "text": req.comment,
            "created_at": str(datetime.utcnow()),
        })
        item.comments = comments
    item.updated_at = datetime.utcnow()

    session.add(item)
    session.commit()
    session.refresh(item)
    return {
        "id": item.id,
        "status": item.status,
        "priority": item.priority,
        "assignee": item.assignee,
        "comments": item.comments,
    }
