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
from typing import Optional
from uuid import uuid4

from fastapi import APIRouter, HTTPException, Depends
from sqlmodel import Session, select, text

from database import get_session
from models import Sprint, BacklogItem, OutboxEvent, Company, Agent, ProjectDiscovery, BusinessUnit
from schemas import SprintStartRequest, BacklogItemUpdate, SprintPlanRequest, SelfImprovementSprintRequest
from logging_config import logger
from services.sprint_planner import plan_execution_sprint, plan_self_improvement_sprint as build_self_improvement_plan, build_self_improvement_objective

router = APIRouter(prefix="/v1", tags=["Core"])

# Allowed values for validation; rejected values return HTTP 422.
VALID_STATUSES = {"todo", "in_progress", "in_review", "done", "blocked"}
VALID_PRIORITIES = {"low", "medium", "high", "critical"}

# Scope guardrail — max backlog items per scope size.
SCOPE_LIMITS: dict[str, int] = {"S": 4, "M": 8, "L": 12}
SCOPE_HARD_MAX = 12


def _load_planning_context(session: Session, company_id: int):
    company = session.get(Company, company_id)
    if not company:
        raise HTTPException(status_code=404, detail="Company not found")

    agents = session.exec(
        select(Agent).where(Agent.company_id == company_id)
    ).all()
    agent_dicts = [
        {
            "role": agent.role,
            "name": agent.name,
            "model": agent.model,
            "seniority": agent.seniority,
            "specialty": agent.specialty,
        }
        for agent in agents
    ]

    discovery = session.exec(
        select(ProjectDiscovery)
        .where(ProjectDiscovery.company_id == company_id)
        .order_by(text("created_at DESC"))
    ).first()

    return company, agent_dicts, discovery


def _serialize_plan_response(plan: dict, scope_size: str, fallback_name: str, fallback_goal: str):
    return {
        "sprint_name": plan.get("sprint_name", fallback_name),
        "goal": plan.get("goal", fallback_goal),
        "scope_size": scope_size,
        "tasks": plan.get("tasks", []),
    }


@router.post("/plan_sprint")
async def plan_sprint(req: SprintPlanRequest, session: Session = Depends(get_session)):
    """Turn a strategic prompt into a scoped sprint backlog before sprint creation."""
    scope_size = req.scope_size.upper()
    if scope_size not in SCOPE_LIMITS:
        raise HTTPException(status_code=422, detail="Invalid scope_size. Valid values: S, M, L")
    if not req.prompt.strip():
        raise HTTPException(status_code=422, detail="Prompt is required")

    company, agent_dicts, discovery = _load_planning_context(session, req.company_id)

    plan = plan_execution_sprint(
        company_name=company.company_name,
        objective=req.prompt,
        scope_size=scope_size,
        agents=agent_dicts,
        detected_stack=discovery.detected_stack if discovery else None,
        project_path=discovery.project_path if discovery else None,
    )

    return _serialize_plan_response(
        plan,
        scope_size,
        f"Sprint - {company.company_name}",
        req.prompt.strip(),
    )


@router.post("/plan_self_improvement_sprint")
async def plan_self_improvement_sprint(req: SelfImprovementSprintRequest, session: Session = Depends(get_session)):
    """Create a bounded autonomous-improvement plan from the repository's own context."""
    scope_size = req.scope_size.upper()
    if scope_size not in SCOPE_LIMITS:
        raise HTTPException(status_code=422, detail="Invalid scope_size. Valid values: S, M, L")

    company, agent_dicts, discovery = _load_planning_context(session, req.company_id)
    objective = build_self_improvement_objective(
        company_name=company.company_name,
        detected_stack=discovery.detected_stack if discovery else None,
    )

    plan = build_self_improvement_plan(
        company_name=company.company_name,
        objective=objective,
        scope_size=scope_size,
        agents=agent_dicts,
        detected_stack=discovery.detected_stack if discovery else None,
        project_path=discovery.project_path if discovery else None,
    )

    return _serialize_plan_response(
        plan,
        scope_size,
        f"Sprint - Improve {company.company_name}",
        objective,
    )


@router.post("/start_sprint")
async def start_sprint(req: SprintStartRequest, session: Session = Depends(get_session)):
    """
    Create a sprint and its initial backlog items in a single transaction.

    For each backlog item an ``OutboxEvent`` is inserted so the background
    worker can publish a ``task.assign`` NATS message once the DB transaction
    commits.  This guarantees at-least-once delivery without a distributed
    transaction between Postgres and NATS.
    """
    scope_size = req.scope_size.upper()
    if scope_size not in SCOPE_LIMITS:
        raise HTTPException(status_code=422, detail="Invalid scope_size. Valid values: S, M, L")

    item_limit = SCOPE_LIMITS[scope_size]
    if len(req.backlog_items) > SCOPE_HARD_MAX:
        raise HTTPException(
            status_code=422,
            detail=f"Too many backlog items ({len(req.backlog_items)}). Hard limit is {SCOPE_HARD_MAX}. Split into multiple sprints.",
        )
    if len(req.backlog_items) > item_limit:
        raise HTTPException(
            status_code=422,
            detail=f"Scope '{scope_size}' allows up to {item_limit} items, got {len(req.backlog_items)}. Use scope 'L' or split this sprint.",
        )

    sprint = Sprint(
        company_id=req.company_id,
        sprint_name=req.sprint_name,
        start_date=req.start_date,
        scope_size=scope_size,
        goal=req.goal,
    )

    # Resolve the company's default business unit for quota tracking.
    default_bu = session.exec(
        select(BusinessUnit).where(BusinessUnit.company_id == req.company_id)
    ).first()
    default_bu_id: Optional[int] = default_bu.id if default_bu else None
    sprint.business_unit_id = default_bu_id
    session.add(sprint)
    session.flush()  # Obtain sprint.id before processing backlog items

    item_ids = []
    for item in req.backlog_items:
        backlog_item = BacklogItem(
            sprint_id=sprint.id,
            title=item.get("title", ""),
            description=item.get("description", ""),
            priority=item.get("priority", "medium"),
            assignee=item.get("assignee", "PO"),
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
                "agent": item.get("assignee", "PO"),
                "sprint_id": sprint.id,
            },
        }
        session.add(OutboxEvent(
            aggregate_type="backlog_item",
            aggregate_id=str(backlog_item.id),
            event_type="nats_publish",
            payload=nats_payload,
        ))

    kickoff_story = "\n".join(
        [
            f"Sprint kickoff: {req.sprint_name}",
            f"Sprint ID: {sprint.id}",
            "Backlog:",
            *[
                f"- {item.get('title', '').strip()}: {item.get('description', '').strip()}"
                for item in req.backlog_items
            ],
            "Goal: PO, Scrum Master and Developer must coordinate, surface blockers, and agree the first execution plan.",
        ]
    )
    session.add(OutboxEvent(
        aggregate_type="sprint",
        aggregate_id=str(sprint.id),
        event_type="nats_publish",
        payload={
            "subject": "workspace.generate",
            "message": {
                "interaction_id": str(uuid4()),
                "company_id": req.company_id,
                "business_unit_id": default_bu_id,
                "sprint_id": sprint.id,
                "story": kickoff_story,
                "constraints": "Start the sprint conversation, coordinate roles, and keep the GlassRoom updated.",
            },
        },
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
        "scope_size": sprint.scope_size,
        "item_limit": SCOPE_LIMITS.get(sprint.scope_size, 8),
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
        "sprint_id": item.sprint_id,
        "status": item.status,
        "priority": item.priority,
        "assignee": item.assignee,
        "comments": item.comments,
    }


@router.post("/sprints/{sprint_id}/dispatch")
async def dispatch_sprint_tasks(sprint_id: int, session: Session = Depends(get_session)):
    """
    Dispatch all ``todo`` backlog items in a sprint to their assigned agents.

    Creates an ``OutboxEvent`` for each eligible item.  The background worker
    publishes these to NATS as ``task.assign`` messages and the task worker
    picks them up.  Items already dispatched (non-todo status) are skipped.
    """
    sprint = session.get(Sprint, sprint_id)
    if not sprint:
        raise HTTPException(status_code=404, detail="Sprint not found")

    items = session.exec(
        select(BacklogItem).where(
            BacklogItem.sprint_id == sprint_id,
            BacklogItem.status == "todo",
        )
    ).all()

    if not items:
        return {"dispatched": 0, "message": "No todo items to dispatch"}

    dispatched = 0
    for item in items:
        nats_payload = {
            "subject": "task.assign",
            "message": {
                "backlog_item_id": item.id,
                "interaction_id": str(uuid4()),
                "agent": item.assignee or "PO",
                "sprint_id": sprint.id,
            },
        }
        session.add(OutboxEvent(
            aggregate_type="backlog_item",
            aggregate_id=str(item.id),
            event_type="nats_publish",
            payload=nats_payload,
        ))
        dispatched += 1

    session.commit()
    logger.info("Dispatched %d tasks for sprint %d", dispatched, sprint_id)
    return {"dispatched": dispatched, "sprint_id": sprint_id}
