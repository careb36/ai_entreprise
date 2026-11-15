"""
Task relations router — typed links between tasks across sprints (issue #533).

Endpoints:
- ``GET  /v1/tasks/{task_id}/relations``          — the task's relations, each
  named from its side (Viewer).
- ``POST /v1/tasks/{task_id}/relations``          — a person relates the task to
  another of the company, proposed or, with ``confirm``, confirmed (Manager).
- ``POST /v1/task_relations/{id}/confirm``        — a person confirms a proposed
  relation (Manager).
- ``POST /v1/task_relations/{id}/reject``         — a person rejects a proposed
  relation (Manager).
- ``POST /v1/task_relations/{id}/remove``         — a person removes a relation
  (Manager).
- ``GET  /v1/sprints/{sprint_id}/relations``      — the relations of a sprint's
  tasks and their numbers, ``duplicates_delivered`` among them (Viewer).

Every write is a ``relation`` event on both tasks (``services/tasks/relations``)
and an audit row under ``task-<id>``. Agents do not propose relations here yet.
"""

from typing import Any

from auth_policy import current_user_if_required, require_company_role
from database import get_session
from fastapi import APIRouter, Depends, HTTPException
from models import BacklogItem, Sprint, TaskRelation, User
from pydantic import Field
from schemas import RequestModel
from services.platform.agent_service import AgentService
from services.tasks import dependencies, relations
from sqlmodel import Session

router = APIRouter(prefix="/v1", tags=["Agents"])


class RelationCreateRequest(RequestModel):
    related_task_id: int
    relation: str = Field(min_length=1, max_length=40)
    # A person may write it confirmed in one step.
    confirm: bool = False


class RelationReasonRequest(RequestModel):
    reason: str | None = Field(default=None, max_length=relations.REASON_LIMIT)


def _actor(user: User | None) -> str:
    return user.email if user is not None else "local demo"


def _task(session: Session, task_id: int) -> tuple[BacklogItem, Sprint]:
    item = session.get(BacklogItem, task_id)
    sprint = session.get(Sprint, item.sprint_id) if item is not None else None
    if item is None or sprint is None:
        raise HTTPException(status_code=404, detail="Task not found")
    return item, sprint


def _relation(session: Session, relation_id: int) -> TaskRelation:
    relation = session.get(TaskRelation, relation_id)
    if relation is None:
        raise HTTPException(status_code=404, detail="Relation not found")
    return relation


def _audit(
    session: Session,
    company_id: int,
    task_id: int,
    actor: str,
    action: str,
    result: dict[str, Any],
) -> None:
    """One audit row for a relation write; commits the session."""
    AgentService().audit_interaction(
        session=session,
        company_id=company_id,
        interaction_id=f"task-{task_id}",
        agent_id=f"human:{actor}",
        action=action,
        details={"company_id": str(company_id), "backlog_item_id": task_id},
        result=result,
        decision_signature=None,
        cost_tokens=0,
    )


def _summary(relation: TaskRelation) -> dict[str, Any]:
    return {
        "relation_id": relation.id,
        "task_id": relation.task_id,
        "related_task_id": relation.related_task_id,
        "kind": relation.kind,
        "state": relation.state,
    }


@router.get("/tasks/{task_id}/relations")
async def list_task_relations(
    task_id: int,
    session: Session = Depends(get_session),  # noqa: B008 - FastAPI dependency marker.
    user: User | None = Depends(current_user_if_required),  # noqa: B008 - FastAPI dependency marker.
):
    """The task's relations, oldest first, each named from this task's side."""
    item, sprint = _task(session, task_id)
    require_company_role(session, user, sprint.company_id, "Viewer")
    rows = [
        r
        for r in relations.for_task(session, item.id)
        if r.company_id == sprint.company_id
    ]
    return {
        "task_id": item.id,
        "relations": [relations.as_dict(session, r, item.id) for r in rows],
        "names": list(relations.NAMES),
    }


@router.post("/tasks/{task_id}/relations", status_code=201)
async def add_task_relation(
    task_id: int,
    req: RelationCreateRequest,
    session: Session = Depends(get_session),  # noqa: B008 - FastAPI dependency marker.
    user: User | None = Depends(current_user_if_required),  # noqa: B008 - FastAPI dependency marker.
):
    """A person relates two tasks of the company; audited as ``task_relation_added``."""
    item, sprint = _task(session, task_id)
    require_company_role(session, user, sprint.company_id, "Manager")
    other = session.get(BacklogItem, req.related_task_id)
    if other is None:
        raise HTTPException(status_code=404, detail="Related task not found")
    actor = _actor(user)
    try:
        relation = relations.propose(
            session,
            item,
            other,
            req.relation,
            by=actor,
            role=relations.PERSON,
            confirmed_by=actor if req.confirm else None,
        )
    except relations.RelationError as exc:
        session.rollback()
        raise HTTPException(status_code=exc.status, detail=str(exc)) from exc
    result = _summary(relation)
    _audit(session, sprint.company_id, item.id, actor, "task_relation_added", result)
    session.refresh(relation)
    return relations.as_dict(session, relation, item.id)


def _person_change(
    relation_id: int,
    session: Session,
    user: User | None,
    action: str,
    change: Any,
) -> dict[str, Any]:
    relation = _relation(session, relation_id)
    require_company_role(session, user, relation.company_id, "Manager")
    actor = _actor(user)
    result = _summary(relation)
    try:
        change(relation, actor)
    except relations.RelationError as exc:
        session.rollback()
        raise HTTPException(status_code=exc.status, detail=str(exc)) from exc
    result["state"] = relation.state
    _audit(session, relation.company_id, result["task_id"], actor, action, result)
    return result


@router.post("/task_relations/{relation_id}/confirm")
async def confirm_task_relation(
    relation_id: int,
    session: Session = Depends(get_session),  # noqa: B008 - FastAPI dependency marker.
    user: User | None = Depends(current_user_if_required),  # noqa: B008 - FastAPI dependency marker.
):
    """A person confirms a proposed relation; audited as ``task_relation_confirmed``."""
    _person_change(
        relation_id,
        session,
        user,
        "task_relation_confirmed",
        lambda relation, actor: relations.confirm(session, relation, actor),
    )
    relation = _relation(session, relation_id)
    return relations.as_dict(session, relation, relation.task_id)


@router.post("/task_relations/{relation_id}/reject")
async def reject_task_relation(
    relation_id: int,
    req: RelationReasonRequest,
    session: Session = Depends(get_session),  # noqa: B008 - FastAPI dependency marker.
    user: User | None = Depends(current_user_if_required),  # noqa: B008 - FastAPI dependency marker.
):
    """A person rejects a proposed relation; audited as ``task_relation_rejected``."""
    return _person_change(
        relation_id,
        session,
        user,
        "task_relation_rejected",
        lambda relation, actor: relations.reject(session, relation, actor, req.reason),
    ) | {"removed": True}


@router.post("/task_relations/{relation_id}/remove")
async def remove_task_relation(
    relation_id: int,
    req: RelationReasonRequest,
    session: Session = Depends(get_session),  # noqa: B008 - FastAPI dependency marker.
    user: User | None = Depends(current_user_if_required),  # noqa: B008 - FastAPI dependency marker.
):
    """A person removes a relation; audited as ``task_relation_removed``."""
    return _person_change(
        relation_id,
        session,
        user,
        "task_relation_removed",
        lambda relation, actor: relations.remove(session, relation, actor, req.reason),
    ) | {"removed": True}


@router.get("/sprints/{sprint_id}/relations")
async def sprint_relations(
    sprint_id: int,
    session: Session = Depends(get_session),  # noqa: B008 - FastAPI dependency marker.
    user: User | None = Depends(current_user_if_required),  # noqa: B008 - FastAPI dependency marker.
):
    """The relations of the sprint's tasks and their numbers."""
    sprint = session.get(Sprint, sprint_id)
    if sprint is None:
        raise HTTPException(status_code=404, detail="Sprint not found")
    require_company_role(session, user, sprint.company_id, "Viewer")
    items = dependencies.sprint_items(session, sprint.id)
    rows = [
        r
        for r in relations.for_tasks(session, list(items))
        if r.company_id == sprint.company_id
    ]
    return {
        "sprint_id": sprint.id,
        "relations": [
            relations.as_dict(
                session, r, r.task_id if r.task_id in items else r.related_task_id
            )
            for r in rows
        ],
        "metrics": relations.sprint_metrics(session, sprint.id),
    }
