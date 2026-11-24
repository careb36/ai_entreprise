"""
Sprint router — Kanban board management and backlog item lifecycle.

Endpoints: ``POST /v1/start_sprint`` (a sprint, its team and its backlog
items), ``GET /v1/sprints``, ``GET /v1/sprints/{id}/board`` and ``PATCH
/v1/backlog/{item_id}``; the planning routes are ``routers/sprint_planning.py``.
New backlog items are dispatched to agents via the transactional outbox: an
``OutboxEvent`` row is inserted atomically in the same DB transaction and
delivered to NATS by the ``process_outbox`` background worker.
"""

from uuid import uuid4

from auth_policy import current_user_if_required, require_company_role
from auth_tokens import STREAM_TICKET_MINUTES, create_stream_ticket
from database import get_session
from fastapi import APIRouter, Depends, HTTPException
from models import (
    BacklogItem,
    BusinessUnit,
    Company,
    OutboxEvent,
    Sprint,
    User,
)
from schemas import (
    SCOPE_HARD_MAX,
    SCOPE_LIMITS,
    BacklogItemUpdate,
    SprintStartRequest,
)
from services.evidence import acceptance
from services.loop import board_moves, live_state, run_window_gate, sprint_archive
from services.loop.carry_over import CarryOverError, carry_over
from services.loop.run_review import ReviewRefused, segregate_board_acceptance
from services.models import run_cost
from services.planning import (
    arch_author,
    planner_fallback,
    spec_check,
    spec_planning,
    sprint_team,
)
from services.platform import sod
from services.release import integration_state
from services.tasks import dependencies, relations, sprint_board, task_filter
from services.tasks.board import (
    count_architect_advice,
    count_second_attempt,
    no_architect_advice,
    no_second_attempts,
    rework_request_count,
)
from sqlalchemy import case, func
from sqlmodel import Session, select

from routers.sprint_control import require_preconditions
from routers.sprint_planning import _refused, _specification
from routers.sprint_planning import router as planning_router

router = APIRouter(prefix="/v1", tags=["Core"])
router.include_router(planning_router)

# Allowed values for validation; rejected values return HTTP 422.
VALID_STATUSES = {"todo", "in_progress", "in_review", "done", "blocked", "discarded"}
VALID_PRIORITIES = {"low", "medium", "high", "critical"}


def _person_id(user: User | None) -> int | None:
    if user is None or getattr(user, "_api_token_company_id", None) is not None:
        return None
    return user.id


@router.post("/start_sprint")
async def start_sprint(
    req: SprintStartRequest,
    session: Session = Depends(get_session),  # noqa: B008 - FastAPI dependency marker.
    user: User | None = Depends(current_user_if_required),  # noqa: B008 - FastAPI dependency marker.
):
    """
    Create a sprint and its initial backlog items in a single transaction.

    For each backlog item an ``OutboxEvent`` is inserted so the background
    worker can publish a ``task.assign`` NATS message once the DB transaction
    commits.  This guarantees at-least-once delivery without a distributed
    transaction between Postgres and NATS.
    """
    require_company_role(session, user, req.company_id, "Manager")
    require_preconditions(session, req.company_id)
    scope_size = req.scope_size.upper()
    if scope_size not in SCOPE_LIMITS:
        raise HTTPException(
            status_code=422, detail="Invalid scope_size. Valid values: S, M, L"
        )

    item_limit = SCOPE_LIMITS[scope_size]
    carry_over_ids = list(dict.fromkeys(req.carry_over_ids))
    backlog_items = [item.model_dump() for item in req.backlog_items]
    item_count = len(backlog_items) + len(carry_over_ids)
    if item_count > SCOPE_HARD_MAX:
        raise HTTPException(
            status_code=422,
            detail=f"Too many backlog items ({item_count}). Hard limit is {SCOPE_HARD_MAX}. Split into multiple sprints.",
        )
    if item_count > item_limit:
        raise HTTPException(
            status_code=422,
            detail=f"Scope '{scope_size}' allows up to {item_limit} items, got {item_count}. Use scope 'L' or split this sprint.",
        )

    # The sprint's team: hired roles only, and every task assigned within it.
    hired = sprint_team.hired_roles(session, req.company_id)
    team, team_source = sprint_team.decide(
        sprint_team.selection_of(session.get(Company, req.company_id)),
        _refused(sprint_team.check_team, req.team, hired),
        _refused(sprint_team.check_team, req.suggested_team or None, hired),
    )
    _refused(
        sprint_team.check_assignees,
        team,
        [i.get("assignee", "PO") for i in backlog_items],
    )
    known: set[str] | None = None
    if req.specification_id is not None:
        spec = _specification(session, req.company_id, req.specification_id)
        known = set(spec_check.criterion_ids(spec_planning.spec_content(spec)))
        # The specification names the version of the company's architecture
        # its sprint was planned against (issue #329, F3).
        holds = arch_author.approved_for(session, spec.id)
        if holds is not None and spec.architecture_id != holds.id:
            spec.architecture_id = holds.id
            session.add(spec)

    sprint = Sprint(
        company_id=req.company_id,
        sprint_name=req.sprint_name,
        start_date=req.start_date,
        scope_size=scope_size,
        goal=req.goal,
        objective=req.objective,
        specification_id=req.specification_id,
        team=team,
        team_source=team_source,
        # From the session, never the body; an API token starts it unattributed.
        started_by_user_id=_person_id(user),
    )

    # Resolve the company's default business unit for quota tracking.
    default_bu = session.exec(
        select(BusinessUnit).where(BusinessUnit.company_id == req.company_id)
    ).first()
    default_bu_id: int | None = default_bu.id if default_bu else None
    sprint.business_unit_id = default_bu_id
    session.add(sprint)
    session.flush()  # Obtain sprint.id before processing backlog items
    # Outside the run window (issue #736) work waits, unless urgent (audited).
    held = run_window_gate.start_new(
        session, sprint, req.urgent, sod.actor_of(session, user)
    )

    # A plan made without the model: the sprint's event, from the backend's record.
    planner_fallback.carry_to_sprint(session, sprint, req.plan_id)
    positions = _refused(dependencies.check_positions, backlog_items)
    item_ids = []
    rows: list[BacklogItem] = []
    for item in backlog_items:
        backlog_item = BacklogItem(
            sprint_id=sprint.id,
            title=item.get("title", ""),
            description=item.get("description", ""),
            priority=item.get("priority", "medium"),
            assignee=item.get("assignee", "PO"),
            covers=_refused(spec_planning.checked_covers, item, known),
            kind=acceptance.ACCEPTANCE_KIND
            if item.get("kind") == acceptance.ACCEPTANCE_KIND
            else None,
        )
        session.add(backlog_item)
        session.flush()
        item_ids.append(backlog_item.id)
        rows.append(backlog_item)
    for row, deps in zip(rows, positions, strict=True):
        if deps:
            # It waits for those tasks; the worker queues it once they are done.
            row.depends_on = [rows[position].id for position in deps]
            dependencies.note(
                session, row, dependencies.waiting_note([rows[p] for p in deps])
            )
            session.add(row)
            continue
        if held:
            run_window_gate.defer_task(session, row, sprint, held)
            continue
        # Outbox entry — delivered to NATS by the process_outbox background worker.
        session.add(
            OutboxEvent(
                aggregate_type="backlog_item",
                aggregate_id=str(row.id),
                event_type="nats_publish",
                payload={
                    "subject": "task.assign",
                    "message": {
                        "backlog_item_id": row.id,
                        "interaction_id": str(uuid4()),
                        "agent": row.assignee or "PO",
                        "sprint_id": sprint.id,
                    },
                },
            )
        )

    # Two tasks of the plan that likely repeat each other: a proposed
    # duplicate relation for a person, both still run (issue #766).
    relations.propose_likely_duplicates(session, rows)
    if carry_over_ids:
        try:
            carried = carry_over(
                session,
                req.company_id,
                carry_over_ids,
                sprint,
                user.email if user is not None else "a person",
                "Manager",
            )
            sprint_team.check_assignees(team, [item.assignee for item in carried])
        except (CarryOverError, sprint_team.TeamRefused) as exc:
            session.rollback()
            raise HTTPException(status_code=exc.status, detail=exc.detail) from exc
        item_ids.extend(item.id for item in carried)
        story_items = [
            *backlog_items,
            *(
                {"title": item.title, "description": item.description or ""}
                for item in carried
            ),
        ]
    else:
        story_items = list(backlog_items)

    kickoff_story = "\n".join(
        [
            f"Sprint kickoff: {req.sprint_name}",
            f"Sprint ID: {sprint.id}",
            "Backlog:",
            *[
                f"- {item.get('title', '').strip()}: {item.get('description', '').strip()}"
                for item in story_items
            ],
            "Goal: PO, Scrum Master and Developer must coordinate, surface blockers, and agree the first execution plan.",
        ]
    )
    session.add(
        OutboxEvent(
            aggregate_type="sprint",
            aggregate_id=str(sprint.id),
            event_type="nats_publish",
            status=run_window_gate.HELD_OUTBOX if held else "pending",
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
        )
    )

    session.commit()
    return {
        "sprint_id": sprint.id,
        "backlog_item_ids": item_ids,
        "sprint_url": f"/v1/sprints/{sprint.id}/board",
        "urgent": sprint.urgent,
        "run_window": run_window_gate.view(session, sprint),
    }


@router.get("/sprints")
async def list_sprints(
    company_id: int,
    include_archived: bool = False,
    session: Session = Depends(get_session),  # noqa: B008 - FastAPI dependency marker.
    user: User | None = Depends(current_user_if_required),  # noqa: B008 - FastAPI dependency marker.
):
    """
    Return the company's sprints, newest first, with backlog progress.

    ``total`` and ``done`` are counted from the sprint's backlog items so the
    list never needs one board request per sprint. ``second_attempts`` counts
    the tasks carried over from an earlier sprint: how many there are, how
    many got done, and the same two numbers for those that came with the
    ScrumMaster's brief, so a person sees whether another attempt, and the
    brief, pay off. ``architect_advice`` splits the tasks into those the
    Architect briefed and those it did not, with how many got done and how
    many rework requests each group drew, so whether that advice helps is a
    number too (the console assistants' tally, ``console_advice``, went with
    them, issue #750). ``contradiction_rework`` counts the rework requests that say
    they contradict another task of the same sprint
    (``sprint_board.contradiction_rework``, issue #439). ``cost`` is the
    sprint's tokens and US cents (``run_cost``). ``integration_state`` is
    where the sprint's latest integration stands, read from what the backend
    recorded (``integration_state``): a ``completed`` sprint whose state is
    ``integrating`` or ``failed`` is not finished.
    Archived sprints are left out unless ``include_archived``.
    """
    require_company_role(session, user, company_id, "Viewer")
    query = select(Sprint).where(Sprint.company_id == company_id)
    if not include_archived:
        query = query.where(sprint_archive.listed())
    sprints = session.exec(query.order_by(Sprint.id.desc())).all()
    sprint_ids = [s.id for s in sprints]
    counts: dict[int, tuple[int, int, int]] = {}
    rework: dict[int, int] = {}
    attempts: dict[int, dict[str, int]] = {}
    briefs: dict[int, dict[str, dict[str, int]]] = {}
    contradictions: dict[int, int] = {}
    if sprint_ids:
        rows = session.exec(
            select(
                BacklogItem.sprint_id,
                func.count(BacklogItem.id),
                func.sum(case((BacklogItem.status == "done", 1), else_=0)),
                func.sum(case((BacklogItem.status == "blocked", 1), else_=0)),
            )
            .where(BacklogItem.sprint_id.in_(sprint_ids))
            .group_by(BacklogItem.sprint_id)
        ).all()
        counts = {
            sid: (int(total), int(done or 0), int(blocked or 0))
            for sid, total, done, blocked in rows
        }
        # Rework requests live in the comments, so they are counted in Python;
        # together with the blocked share they are the numbers the procedures
        # are judged by, shown per sprint so a person sees the trend.
        tasks: dict[int, list[tuple]] = {}
        for item_id, title, sid, status, comments in session.exec(
            select(
                BacklogItem.id,
                BacklogItem.title,
                BacklogItem.sprint_id,
                BacklogItem.status,
                BacklogItem.comments,
            ).where(BacklogItem.sprint_id.in_(sprint_ids))
        ).all():
            tasks.setdefault(sid, []).append((item_id, title, comments))
            rework[sid] = rework.get(sid, 0) + rework_request_count(comments)
            count_second_attempt(
                attempts.setdefault(sid, no_second_attempts()), status, comments
            )
            count_architect_advice(
                briefs.setdefault(sid, no_architect_advice()), status, comments
            )
        notes = sprint_board.notes_by_sprint(session, sprint_ids)
        contradictions = {
            sid: sprint_board.contradiction_rework(items, notes.get(sid, []))
            for sid, items in tasks.items()
        }
    costs = run_cost.sprint_costs(session, company_id, sprint_ids)
    integrations = integration_state.latest_statuses(session, sprint_ids)
    return [
        {
            "id": s.id,
            "sprint_name": s.sprint_name,
            "status": s.status,
            **integration_state.fields(integrations.get(s.id)),
            "pause_reason": s.pause_reason,
            "goal": s.goal,
            "start_date": s.start_date,
            "scope_size": s.scope_size,
            "specification_id": s.specification_id,
            "duration_days": s.duration_days,
            "team": sprint_team.team_of(s),
            "team_source": sprint_team.source_of(s),
            "archived_at": s.archived_at.isoformat() if s.archived_at else None,
            "archived_by": s.archived_by,
            "total": counts.get(s.id, (0, 0, 0))[0],
            "done": counts.get(s.id, (0, 0, 0))[1],
            "blocked": counts.get(s.id, (0, 0, 0))[2],
            "rework": rework.get(s.id, 0),
            "contradiction_rework": contradictions.get(s.id, 0),
            "second_attempts": attempts.get(s.id, no_second_attempts()),
            "architect_advice": briefs.get(s.id, no_architect_advice()),
            "cost": costs.get(s.id) or run_cost.summarize([]),
        }
        for s in sprints
    ]


@router.post("/sprints/{sprint_id}/stream_ticket")
async def glassroom_stream_ticket(
    sprint_id: int,
    session: Session = Depends(get_session),  # noqa: B008 - FastAPI dependency marker.
    user: User | None = Depends(current_user_if_required),  # noqa: B008 - FastAPI dependency marker.
):
    """A short-lived ticket to open this sprint's Glassroom stream (Viewer).

    The browser's EventSource cannot send an Authorization header, so the
    stream takes this ticket in its query string. Without a login (a local
    demo) the stream needs none and ``ticket`` is null.
    """
    sprint = session.get(Sprint, sprint_id)
    if not sprint:
        raise HTTPException(status_code=404, detail="Sprint not found")
    require_company_role(session, user, sprint.company_id, "Viewer")
    if user is None:
        return {"ticket": None, "expires_in": 0}
    return {
        "ticket": create_stream_ticket(user_id=user.id, sprint_id=sprint.id),
        "expires_in": STREAM_TICKET_MINUTES * 60,
    }


@router.get("/sprints/{sprint_id}/board")
async def get_sprint_board(
    sprint_id: int,
    session: Session = Depends(get_session),  # noqa: B008 - FastAPI dependency marker.
    user: User | None = Depends(current_user_if_required),  # noqa: B008 - FastAPI dependency marker.
):
    """
    Return sprint metadata and all backlog items grouped by Kanban column.

    Items whose ``status`` value is not in ``VALID_STATUSES`` are placed in
    the ``"todo"`` column to avoid breaking the frontend rendering.
    """
    sprint = session.get(Sprint, sprint_id)
    if not sprint:
        raise HTTPException(status_code=404, detail="Sprint not found")

    require_company_role(session, user, sprint.company_id, "Viewer")
    sprint_data = {
        "id": sprint.id,
        "company_id": sprint.company_id,
        "sprint_name": sprint.sprint_name,
        "start_date": sprint.start_date,
        "scope_size": sprint.scope_size,
        "item_limit": SCOPE_LIMITS.get(sprint.scope_size, 8),
        "status": sprint.status,
        **integration_state.of_sprint(session, sprint.id),
        "pause_reason": sprint.pause_reason,
        "pause_kind": live_state.pause_kind(sprint.status, sprint.pause_reason),
        "urgent": sprint.urgent,
        "run_window": run_window_gate.view(session, sprint),
        "goal": sprint.goal,
        "objective": sprint.objective,
        "planner_fallback": planner_fallback.of_sprint(session, sprint),
        "specification_id": sprint.specification_id,
        "archived_at": sprint.archived_at.isoformat() if sprint.archived_at else None,
        "team": sprint_team.team_of(sprint),
        "team_source": sprint_team.source_of(sprint),
    }

    items_db = session.exec(
        select(BacklogItem).where(BacklogItem.sprint_id == sprint_id)
    ).all()
    by_id = {i.id: i for i in items_db}
    # What was observed, apart from the row (and its claims) by design.
    observed = task_filter.board_observations(
        session, sprint.company_id, list(items_db)
    )

    items = [
        {
            "id": i.id,
            "title": i.title,
            "description": i.description,
            "status": i.status,
            "priority": i.priority,
            "assignee": i.assignee,
            "comments": i.comments or [],
            "covers": spec_planning.covers_of(i),
            "kind": i.kind,
            "depends_on": dependencies.depends_on_of(i),
            "waiting_for": dependencies.waiting_for(by_id, i),
            "created_at": str(i.created_at),
            "updated_at": str(i.updated_at),
            "observed": observed[i.id]["observed"],
            "reported": observed[i.id]["reported"],
        }
        for i in items_db
    ]

    # Pre-populate all columns so the frontend always receives a complete dict.
    columns = {
        s: []
        for s in ["todo", "in_progress", "in_review", "done", "blocked", "discarded"]
    }
    for item in items:
        col = item["status"] if item["status"] in columns else "todo"
        columns[col].append(item)

    return {"sprint": sprint_data, "columns": columns, "total": len(items)}


@router.patch("/backlog/{item_id}")
async def update_backlog_item(
    item_id: int,
    req: BacklogItemUpdate,
    session: Session = Depends(get_session),  # noqa: B008 - FastAPI dependency marker.
    user: User | None = Depends(current_user_if_required),  # noqa: B008 - FastAPI dependency marker.
):
    """
    Partially update a backlog item.

    Supports updating ``status``, ``priority``, ``assignee``, or appending a
    new comment.  All fields are optional; only provided fields are changed.
    A comment is a person's note (author ``user``, with who wrote it from the
    signed-in session: ``board_moves.person_fields``), never a deliverable; one
    starting with ``board.SEND_BACK_MARKER`` restarts the rework budgets. A
    move to ``todo`` starts the task (``services/loop/board_moves.py``).
    """
    if req.status and req.status not in VALID_STATUSES:
        raise HTTPException(
            status_code=422, detail=f"Invalid status. Valid: {VALID_STATUSES}"
        )
    if req.priority and req.priority not in VALID_PRIORITIES:
        raise HTTPException(
            status_code=422, detail=f"Invalid priority. Valid: {VALID_PRIORITIES}"
        )

    item = session.get(BacklogItem, item_id)
    if not item:
        raise HTTPException(status_code=404, detail="Backlog item not found")
    sprint = session.get(Sprint, item.sprint_id)
    if user is not None:
        if not sprint:
            raise HTTPException(status_code=404, detail="Sprint not found")
        require_company_role(session, user, sprint.company_id, "Manager")
    try:  # A move to To do starts the task (services/loop/board_moves.py).
        starts, comment = board_moves.prepare(sprint, item, req.status, req.comment)
    except board_moves.BoardMoveError as exc:
        raise HTTPException(status_code=exc.status, detail=exc.detail) from exc
    if req.status == "done" and sprint is not None:
        # Marking a held delivery done accepts it: segregation of duties (#543).
        try:
            segregate_board_acceptance(
                session, item, sprint, sod.actor_of(session, user)
            )
        except ReviewRefused as refused:
            raise HTTPException(
                status_code=refused.status, detail=refused.detail
            ) from refused

    if req.priority:
        item.priority = req.priority
    # Within the sprint's team (services/planning/sprint_team.py).
    if req.assignee is not None:
        _refused(
            sprint_team.check_assignees, sprint_team.team_of(sprint), [req.assignee]
        )
        item.assignee = req.assignee
    # A person's transition: its event first, the comment its rendering; a
    # send-back from chat writes it through the same function.
    _event, started = board_moves.record_person_change(
        session, item, sprint, user, status=req.status, comment=comment, starts=starts
    )
    session.commit()
    session.refresh(item)
    return {
        "id": item.id,
        "sprint_id": item.sprint_id,
        "status": item.status,
        "priority": item.priority,
        "assignee": item.assignee,
        "comments": item.comments,
        **started,
    }
