"""Enterprise analytics: a sprint's health, the orchestration KPIs and token usage.

Endpoints (split out of ``routers/enterprise.py``, which re-exports them):
- ``GET /v1/analytics/summary``              — AI Scrum Master sprint health report.
- ``GET /v1/analytics/orchestration_health`` — weekly KPI trends of the orchestration.
- ``GET /v1/analytics/gate_refutations``     — deliveries the gate before QA sent back.
- ``GET /v1/enterprise/quota_usage_history`` — a company's daily token usage.
"""

from datetime import UTC, date, datetime, timedelta

from auth_policy import (
    current_user_if_required,
    require_company_role,
)
from database import get_session
from fastapi import APIRouter, Depends, HTTPException, Query
from logging_config import logger
from models import AuditLog, Sprint, TaskEvent, User
from schemas import (
    GateRefutationsResponse,
    GateRefutationWeek,
    OrchestrationHealthResponse,
    OrchestrationHealthSummaries,
    OrchestrationHealthSummary,
    OrchestrationHealthWeek,
)
from services.loop.company_service import CompanyService
from services.platform.orchestration_metrics import (
    GATE_UNCONFIRMED,
    count_gate_refutations,
    gate_refutation_problem,
)
from services.tasks.task_events import GATE
from sqlalchemy import text
from sqlmodel import Session, select

router = APIRouter(prefix="/v1", tags=["Enterprise"])

_ANCHOR_COMMIT = "116138e"
_ANCHOR_DATE = date(2026, 5, 2)


@router.get("/analytics/summary", tags=["Enterprise"])
async def get_project_health(
    sprint_id: int,
    session: Session = Depends(get_session),  # noqa: B008 - FastAPI dependency marker.
    user: User | None = Depends(current_user_if_required),  # noqa: B008 - FastAPI dependency marker.
):
    """
    AI Scrum Master: generate a sprint health report from audit and backlog data.
    """
    if user is not None:
        sprint = session.get(Sprint, sprint_id)
        if not sprint:
            raise HTTPException(status_code=404, detail="Sprint not found")
        require_company_role(session, user, sprint.company_id, "Viewer")
    service = CompanyService(session)
    return service.get_project_health(sprint_id)


@router.get(
    "/analytics/orchestration_health",
    response_model=OrchestrationHealthResponse,
    tags=["Enterprise"],
)
async def get_orchestration_health(
    company_id: int = Query(
        ..., description="Company whose orchestration history will be analysed"
    ),
    weeks: int = Query(8, ge=1, le=52, description="Trailing weeks to include"),
    session: Session = Depends(get_session),  # noqa: B008 - FastAPI dependency marker.
    user: User | None = Depends(current_user_if_required),  # noqa: B008 - FastAPI dependency marker.
):
    """
    Return weekly KPI trends that measure the Batch-1 orchestration rollout.
    """
    require_company_role(session, user, company_id, "Viewer")
    cutoff = datetime.now(UTC).replace(tzinfo=None) - timedelta(weeks=weeks)
    params = {"company_id": company_id, "cutoff": cutoff}

    qa_sql = text("""
        SELECT
            to_char(date_trunc('week', a.created_at), 'IYYY-"W"IW') AS week,
            COALESCE(
                COUNT(*) FILTER (WHERE a.details->>'result_type' = 'NEEDS_WORK') * 1.0
                / NULLIF(COUNT(*), 0),
                0
            ) AS qa_needs_work_rate,
            COUNT(*) AS task_count
        FROM audit_log a
        WHERE a.details->>'role' = 'QA'
          AND a.action = 'qa_review'
          AND NULLIF(a.details->>'company_id', '')::int = :company_id
          AND a.created_at >= :cutoff
        GROUP BY 1
        ORDER BY 1
    """)

    qa_fallback_sql = text("""
        WITH review_comments AS (
            SELECT
                to_char(date_trunc('week', bi.updated_at), 'IYYY-"W"IW') AS week,
                UPPER(COALESCE(c->>'text', '')) AS review_text
            FROM backlog_items bi
            JOIN sprints s ON s.id = bi.sprint_id
            CROSS JOIN LATERAL jsonb_array_elements(
                CASE
                    WHEN bi.comments IS NULL THEN '[]'::jsonb
                    WHEN jsonb_typeof(bi.comments::jsonb) = 'array' THEN bi.comments::jsonb
                    ELSE '[]'::jsonb
                END
            ) AS c
            WHERE s.company_id = :company_id
              AND bi.updated_at >= :cutoff
              AND (
                  UPPER(COALESCE(c->>'text', '')) LIKE 'APPROVED:%'
                  OR UPPER(COALESCE(c->>'text', '')) LIKE 'NEEDS WORK:%'
              )
        )
        SELECT
            week,
            COALESCE(
                COUNT(*) FILTER (WHERE review_text LIKE 'NEEDS WORK:%') * 1.0
                / NULLIF(COUNT(*), 0),
                0
            ) AS qa_needs_work_rate,
            COUNT(*) AS task_count
        FROM review_comments
        GROUP BY 1
        ORDER BY 1
    """)

    reported = "COALESCE(a.details->>'tokens', CASE WHEN a.cost_tokens > 0 THEN 'reported' ELSE 'unavailable' END) = 'reported'"
    tokens_sql = text(
        """
        SELECT
            to_char(date_trunc('week', a.created_at), 'IYYY-"W"IW') AS week,
            -- Turns whose usage the proxy did not report are counted apart, not averaged as zero.
            COALESCE(AVG(a.cost_tokens) FILTER (WHERE {reported}), 0) AS avg_developer_tokens,
            COUNT(*) FILTER (WHERE {reported}) AS developer_turn_count,
            COUNT(*) FILTER (WHERE NOT ({reported})) AS developer_turns_without_tokens
        FROM audit_log a
        WHERE a.details->>'role' = 'Developer'
          AND NULLIF(a.details->>'company_id', '')::int = :company_id
          AND a.created_at >= :cutoff
        GROUP BY 1
        ORDER BY 1
    """.replace("{reported}", reported)
    )

    consult_sql = text("""
        SELECT
            to_char(date_trunc('week', bi.updated_at), 'IYYY-"W"IW') AS week,
            COUNT(*) AS architect_consult_count
        FROM backlog_items bi
        JOIN sprints s ON s.id = bi.sprint_id
        CROSS JOIN LATERAL jsonb_array_elements(
            CASE
                WHEN bi.comments IS NULL THEN '[]'::jsonb
                WHEN jsonb_typeof(bi.comments::jsonb) = 'array' THEN bi.comments::jsonb
                ELSE '[]'::jsonb
            END
        ) AS c
        WHERE s.company_id = :company_id
          AND bi.updated_at >= :cutoff
          AND c->>'author' = 'Architect'
          AND UPPER(COALESCE(c->>'text', '')) LIKE 'ARCHITECT_CONSULTED:%'
        GROUP BY 1
        ORDER BY 1
    """)

    try:
        qa_rows = session.execute(qa_sql, params).mappings().all()
        if not qa_rows:
            qa_rows = session.execute(qa_fallback_sql, params).mappings().all()
        token_rows = session.execute(tokens_sql, params).mappings().all()
        consult_rows = session.execute(consult_sql, params).mappings().all()
    except Exception as exc:  # noqa: BLE001 - Translate analytics failures to a sanitized API error.
        logger.error("Orchestration health query failed: %s", exc)
        raise HTTPException(status_code=500, detail="Analytics query failed.")

    qa_by_week = {row["week"]: row for row in qa_rows}
    tokens_by_week = {row["week"]: row for row in token_rows}
    consult_by_week = {row["week"]: row for row in consult_rows}
    developer_turn_counts = {
        row["week"]: int(row.get("developer_turn_count", 0)) for row in token_rows
    }
    all_weeks = sorted(set(qa_by_week) | set(tokens_by_week) | set(consult_by_week))
    anchor_week = _ANCHOR_DATE.strftime("%G-W%V")

    week_rows: list[OrchestrationHealthWeek] = []
    for week_label in all_weeks:
        qa_row = qa_by_week.get(week_label, {})
        token_row = tokens_by_week.get(week_label, {})
        consult_row = consult_by_week.get(week_label, {})
        week_rows.append(
            OrchestrationHealthWeek(
                week=week_label,
                qa_needs_work_rate=float(qa_row.get("qa_needs_work_rate", 0.0)),
                avg_developer_tokens=float(token_row.get("avg_developer_tokens", 0.0)),
                architect_consult_count=int(
                    consult_row.get("architect_consult_count", 0)
                ),
                task_count=int(qa_row.get("task_count", 0)),
                developer_turns_without_tokens=int(
                    token_row.get("developer_turns_without_tokens", 0) or 0
                ),
                post_batch1=week_label >= anchor_week,
            )
        )

    def _weighted_qa_rate(items: list[OrchestrationHealthWeek]) -> float:
        total_reviews = sum(item.task_count for item in items)
        if not total_reviews:
            return 0.0
        weighted_sum = sum(item.qa_needs_work_rate * item.task_count for item in items)
        return weighted_sum / total_reviews

    def _weighted_token_average(items: list[OrchestrationHealthWeek]) -> float:
        total_turns = sum(developer_turn_counts.get(item.week, 0) for item in items)
        if not total_turns:
            return 0.0
        weighted_sum = sum(
            item.avg_developer_tokens * developer_turn_counts.get(item.week, 0)
            for item in items
        )
        return weighted_sum / total_turns

    pre_batch1 = [row for row in week_rows if not row.post_batch1]
    post_batch1 = [row for row in week_rows if row.post_batch1]

    return OrchestrationHealthResponse(
        anchor_commit=_ANCHOR_COMMIT,
        anchor_date=_ANCHOR_DATE.isoformat(),
        weeks=week_rows,
        summary=OrchestrationHealthSummaries(
            pre_batch1=OrchestrationHealthSummary(
                qa_needs_work_rate=_weighted_qa_rate(pre_batch1),
                avg_developer_tokens=_weighted_token_average(pre_batch1),
            ),
            post_batch1=OrchestrationHealthSummary(
                qa_needs_work_rate=_weighted_qa_rate(post_batch1),
                avg_developer_tokens=_weighted_token_average(post_batch1),
            ),
        ),
    )


_GATE_REFUTATION_DAYS = 56


@router.get(
    "/analytics/gate_refutations",
    response_model=GateRefutationsResponse,
    tags=["Enterprise"],
)
async def get_gate_refutations(
    company_id: int = Query(..., description="Company whose task events are read"),
    sprint_id: int | None = Query(None, description="Only this sprint's tasks"),
    since: date | None = Query(None, description="First day, inclusive"),  # noqa: B008 - FastAPI query marker.
    until: date | None = Query(None, description="Last day, inclusive (today)"),  # noqa: B008 - FastAPI query marker.
    session: Session = Depends(get_session),  # noqa: B008 - FastAPI dependency marker.
    user: User | None = Depends(current_user_if_required),  # noqa: B008 - FastAPI dependency marker.
):
    """
    Deliveries the gate before QA sent back, per problem and ISO week (issue #549).

    Read from the task-event chain (the Verifier's ``gate`` events), never
    from what an agent said; ``count_gate_refutations`` says what is left
    out. ``since`` defaults to eight weeks before ``until``.
    """
    require_company_role(session, user, company_id, "Viewer")
    until = until or datetime.now(UTC).date()
    since = since or until - timedelta(days=_GATE_REFUTATION_DAYS)
    if since > until:
        raise HTTPException(status_code=400, detail="since is after until")
    if sprint_id is not None:
        sprint = session.get(Sprint, sprint_id)
        if sprint is None or sprint.company_id != company_id:
            raise HTTPException(status_code=404, detail="Sprint not found")
    query = select(TaskEvent).where(
        TaskEvent.company_id == company_id,
        TaskEvent.actor == GATE,
        TaskEvent.created_at >= datetime.combine(since, datetime.min.time()),
        TaskEvent.created_at
        < datetime.combine(until + timedelta(days=1), datetime.min.time()),
    )
    if sprint_id is not None:
        query = query.where(TaskEvent.sprint_id == sprint_id)
    weekly: dict[str, list[dict]] = {}
    payloads = []
    for event in session.exec(query.order_by(TaskEvent.id)).all():
        payload = event.payload if isinstance(event.payload, dict) else {}
        if gate_refutation_problem(payload) is None:
            continue
        payloads.append(payload)
        year, week, _ = event.created_at.isocalendar()
        weekly.setdefault(f"{year}-W{week:02d}", []).append(payload)
    by_problem = count_gate_refutations(payloads)
    unconfirmed = by_problem.get(GATE_UNCONFIRMED, 0)
    return GateRefutationsResponse(
        company_id=company_id,
        sprint_id=sprint_id,
        since=since.isoformat(),
        until=until.isoformat(),
        by_problem=by_problem,
        refuted=sum(by_problem.values()) - unconfirmed,
        unconfirmed=unconfirmed,
        weeks=[
            GateRefutationWeek(week=week, by_problem=count_gate_refutations(rows))
            for week, rows in sorted(weekly.items())
        ],
    )


@router.get("/enterprise/quota_usage_history", tags=["Enterprise"])
async def quota_usage_history(
    company_id: int,
    days: int = Query(7, ge=1, le=90),
    session: Session = Depends(get_session),  # noqa: B008 - FastAPI dependency marker.
    user: User | None = Depends(current_user_if_required),  # noqa: B008 - FastAPI dependency marker.
):
    """Return daily token usage for a company over the last *days* days (Viewer)."""
    require_company_role(session, user, company_id, "Viewer")
    from datetime import timedelta

    from sqlmodel import func

    cutoff = datetime.now(UTC).replace(tzinfo=None) - timedelta(days=days)
    rows = session.exec(
        select(
            func.date(AuditLog.created_at).label("day"),
            func.coalesce(func.sum(AuditLog.cost_tokens), 0),
        )
        .where(AuditLog.company_id == company_id)
        .where(AuditLog.created_at >= cutoff)
        .group_by(func.date(AuditLog.created_at))
        .order_by(func.date(AuditLog.created_at))
    ).all()
    usage = {str(r[0]): int(r[1] or 0) for r in rows}

    result = []
    for i in range(days):
        d = (
            datetime.now(UTC).replace(tzinfo=None) - timedelta(days=days - 1 - i)
        ).date()
        result.append({"date": d.isoformat(), "tokens": usage.get(str(d), 0)})
    return {"days": result, "total": sum(u["tokens"] for u in result)}
