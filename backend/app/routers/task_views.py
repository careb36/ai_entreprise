"""
Task views router — a company's tasks filtered on the evidence, and saved views (issue #535).

Endpoints:
- ``GET    /v1/tasks?company_id=&filter=<json>&view_id=&order_by=&group_by=&limit=&offset=``
  — the tasks that pass the filter, evaluated on the server from the task
  projections, with their count (Viewer). A field outside the allowlist is a
  422 whose ``detail.field`` names it; no task is evaluated.
- ``GET    /v1/companies/{company_id}/views`` — the views this person lists:
  the company's and their own private ones (Viewer).
- ``POST   /v1/companies/{company_id}/views`` — a new view; sharing it with the
  company or locking it takes a Manager (Viewer).
- ``GET / PUT / DELETE /v1/views/{view_id}`` — read, change, remove; a locked
  view is changed only by a Manager, a private one only by its author.
- ``GET / PUT /v1/views/{view_id}/preferences`` — this person's display
  preferences for a view, kept apart from the view (Viewer).

Writes are audited under ``company:<id>:views``.
"""

from typing import Any

from auth_policy import current_user_if_required, require_company_role
from database import get_session
from fastapi import APIRouter, Depends, HTTPException, Query, Response
from models import SavedView, User
from services.contracts import task_filters
from services.platform.agent_service import AgentService
from services.tasks import saved_views, task_filter
from sqlmodel import Session

router = APIRouter(prefix="/v1", tags=["Core"])


def _user_id(user: User | None) -> int | None:
    return getattr(user, "id", None) if user is not None else None


def _actor(user: User | None) -> str:
    return user.email if user is not None else "local demo"


def _is_manager(session: Session, user: User | None, company_id: int) -> bool:
    try:
        require_company_role(session, user, company_id, "Manager")
    except HTTPException:
        return False
    return True


def _refused(exc: task_filters.FilterRefused) -> HTTPException:
    return HTTPException(status_code=422, detail=exc.as_detail())


def _view_error(exc: saved_views.ViewError) -> HTTPException:
    return HTTPException(status_code=exc.status, detail=str(exc))


def _audit(
    session: Session, company_id: int, user: User | None, action: str, result: dict
) -> None:
    AgentService().audit_interaction(
        session=session,
        company_id=company_id,
        interaction_id=f"company:{company_id}:views",
        agent_id=f"human:{_actor(user)}",
        action=action,
        details={"company_id": str(company_id)},
        result=result,
        decision_signature=None,
        cost_tokens=0,
    )


def _combine(
    view_tree: dict[str, Any] | None, request_tree: dict[str, Any] | None
) -> dict[str, Any] | None:
    """A view's filter and the request's, both to pass."""
    if view_tree is None or request_tree is None:
        return view_tree or request_tree
    left = view_tree.get("and", [view_tree])
    right = request_tree.get("and", [request_tree])
    return {"and": [*left, *right]}


@router.get("/tasks")
async def filter_tasks(
    company_id: int,
    filter: str | None = Query(default=None, max_length=task_filters.MAX_RAW_BYTES),
    view_id: int | None = None,
    order_by: str | None = None,
    group_by: str | None = None,
    limit: int = Query(
        default=task_filter.DEFAULT_LIMIT, ge=0, le=task_filter.MAX_LIMIT
    ),
    offset: int = Query(default=0, ge=0),
    session: Session = Depends(get_session),  # noqa: B008 - FastAPI dependency marker.
    user: User | None = Depends(current_user_if_required),  # noqa: B008 - FastAPI dependency marker.
):
    """The company's tasks that pass the filter (and the view's, with ``view_id``)."""
    require_company_role(session, user, company_id, "Viewer")
    view_tree = None
    if view_id is not None:
        try:
            view = saved_views.readable(session, view_id, _user_id(user))
        except saved_views.ViewError as exc:
            raise _view_error(exc) from exc
        if view.company_id != company_id:
            raise HTTPException(status_code=404, detail="View not found")
        try:
            view_tree = saved_views.stored_filter(view)
        except task_filters.FilterRefused as exc:
            raise _refused(exc) from exc
        order_by = order_by or view.order_by
        group_by = group_by or view.group_by
    try:
        tree = _combine(view_tree, task_filters.load(filter))
        order = task_filters.parse_order(order_by)
        if group_by not in (None, *task_filters.GROUP_FIELDS):
            raise task_filters.FilterRefused(
                f"{group_by[:60]!r} cannot group tasks", "group_by"
            )
    except task_filters.FilterRefused as exc:
        raise _refused(exc) from exc
    return task_filter.query(
        session,
        company_id,
        tree,
        order_by=order,
        group_by=group_by,
        limit=limit,
        offset=offset,
    )


@router.get("/companies/{company_id}/views")
async def list_views(
    company_id: int,
    session: Session = Depends(get_session),  # noqa: B008 - FastAPI dependency marker.
    user: User | None = Depends(current_user_if_required),  # noqa: B008 - FastAPI dependency marker.
):
    """The company's views and this person's private ones, with the allowlists."""
    require_company_role(session, user, company_id, "Viewer")
    uid, manager = _user_id(user), _is_manager(session, user, company_id)
    return {
        "views": [
            saved_views.as_dict(v, uid, manager)
            for v in saved_views.views_of(session, company_id, uid)
        ],
        "fields": {
            name: list(ops) for name, (_kind, ops) in task_filters.FILTER_FIELDS.items()
        },
        "group_fields": list(task_filters.GROUP_FIELDS),
        "order_fields": list(task_filters.ORDER_FIELDS),
        "columns": list(task_filters.COLUMNS),
        "can_share": manager,
    }


@router.post("/companies/{company_id}/views", status_code=201)
async def create_view(
    company_id: int,
    body: dict[str, Any],
    session: Session = Depends(get_session),  # noqa: B008 - FastAPI dependency marker.
    user: User | None = Depends(current_user_if_required),  # noqa: B008 - FastAPI dependency marker.
):
    """A new view; ``company`` access or ``locked`` take a Manager."""
    require_company_role(session, user, company_id, "Viewer")
    uid, manager = _user_id(user), _is_manager(session, user, company_id)
    try:
        view = saved_views.create(
            session,
            company_id,
            body,
            user_id=uid,
            actor=_actor(user),
            is_manager=manager,
        )
    except task_filters.FilterRefused as exc:
        raise _refused(exc) from exc
    except saved_views.ViewError as exc:
        raise _view_error(exc) from exc
    _audit(session, company_id, user, "view_created", {"view_id": view.id})
    session.refresh(view)
    return saved_views.as_dict(view, uid, manager)


def _readable(session: Session, view_id: int, user: User | None) -> SavedView:
    view = session.get(SavedView, view_id)
    if view is None:
        raise HTTPException(status_code=404, detail="View not found")
    require_company_role(session, user, view.company_id, "Viewer")
    try:
        return saved_views.readable(session, view_id, _user_id(user))
    except saved_views.ViewError as exc:
        raise _view_error(exc) from exc


@router.get("/views/{view_id}")
async def get_view(
    view_id: int,
    session: Session = Depends(get_session),  # noqa: B008 - FastAPI dependency marker.
    user: User | None = Depends(current_user_if_required),  # noqa: B008 - FastAPI dependency marker.
):
    view = _readable(session, view_id, user)
    manager = _is_manager(session, user, view.company_id)
    return saved_views.as_dict(view, _user_id(user), manager)


@router.put("/views/{view_id}")
async def update_view(
    view_id: int,
    body: dict[str, Any],
    session: Session = Depends(get_session),  # noqa: B008 - FastAPI dependency marker.
    user: User | None = Depends(current_user_if_required),  # noqa: B008 - FastAPI dependency marker.
):
    """Change a view: its author, or a Manager; a locked one only a Manager."""
    view = _readable(session, view_id, user)
    uid, manager = _user_id(user), _is_manager(session, user, view.company_id)
    try:
        saved_views.update(
            session, view, body, user_id=uid, actor=_actor(user), is_manager=manager
        )
    except task_filters.FilterRefused as exc:
        session.rollback()
        raise _refused(exc) from exc
    except saved_views.ViewError as exc:
        session.rollback()
        raise _view_error(exc) from exc
    _audit(session, view.company_id, user, "view_updated", {"view_id": view.id})
    session.refresh(view)
    return saved_views.as_dict(view, uid, manager)


@router.delete("/views/{view_id}", status_code=204)
async def delete_view(
    view_id: int,
    session: Session = Depends(get_session),  # noqa: B008 - FastAPI dependency marker.
    user: User | None = Depends(current_user_if_required),  # noqa: B008 - FastAPI dependency marker.
):
    view = _readable(session, view_id, user)
    company_id = view.company_id
    try:
        saved_views.delete(
            session,
            view,
            user_id=_user_id(user),
            is_manager=_is_manager(session, user, company_id),
        )
    except saved_views.ViewError as exc:
        raise _view_error(exc) from exc
    _audit(session, company_id, user, "view_deleted", {"view_id": view_id})
    return Response(status_code=204)


@router.get("/views/{view_id}/preferences")
async def get_preferences(
    view_id: int,
    session: Session = Depends(get_session),  # noqa: B008 - FastAPI dependency marker.
    user: User | None = Depends(current_user_if_required),  # noqa: B008 - FastAPI dependency marker.
):
    view = _readable(session, view_id, user)
    return saved_views.preferences(session, view, _user_id(user))


@router.put("/views/{view_id}/preferences")
async def put_preferences(
    view_id: int,
    body: dict[str, Any],
    session: Session = Depends(get_session),  # noqa: B008 - FastAPI dependency marker.
    user: User | None = Depends(current_user_if_required),  # noqa: B008 - FastAPI dependency marker.
):
    """This person's display preferences; the view itself is not changed."""
    view = _readable(session, view_id, user)
    try:
        return saved_views.set_preferences(session, view, _user_id(user), body)
    except saved_views.ViewError as exc:
        raise _view_error(exc) from exc
