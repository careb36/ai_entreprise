"""The usage report (issue #512, phase 5; ``docs/archive/design/TEAMS_AND_USAGE.md``).

- ``GET /v1/companies/{id}/usage``      — the report as JSON (Manager)
- ``GET /v1/companies/{id}/usage.csv``  — the same report as CSV (Manager), audited

Both take ``since`` and ``until`` (UTC dates, inclusive; at most
``usage_report.MAX_DAYS`` days; by default the last 30 days to today) and
``period`` (``day``, ``week`` or ``month``). The numbers are read back from
the records (``services/models/usage_report.py``); the CSV escapes every text
cell a spreadsheet would run as a formula (``services/models/usage_csv.py``).
"""

from __future__ import annotations

from datetime import UTC, date, datetime, timedelta

from auth_policy import current_user_if_required, require_company_role
from database import get_session
from fastapi import APIRouter, Depends, HTTPException, Query
from fastapi.responses import Response
from models import Company, User
from services.models import usage_csv, usage_report
from services.platform.agent_service import AgentService
from sqlmodel import Session

router = APIRouter(prefix="/v1", tags=["Company"])

DEFAULT_DAYS = 30


def _now() -> datetime:
    return datetime.now(UTC)


def _range(since: date | None, until: date | None) -> tuple[date, date]:
    end = until or _now().date()
    return since or (end - timedelta(days=DEFAULT_DAYS - 1)), end


def _build(session: Session, company_id: int, since, until, period: str) -> dict:
    start, end = _range(since, until)
    try:
        return usage_report.report(
            session, company_id, since=start, until=end, period=period, now=_now()
        )
    except usage_report.UsageRangeError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc


def _actor(user: User | None) -> str:
    """Who exported: ``api-token:<id>`` for a token, ``human:<email>`` for a person."""
    if user is None:
        return "human:a person"  # a local demo without authentication
    if getattr(user, "_api_token_company_id", None) is not None:
        return user.email  # the synthetic user's "api-token:<id>"
    return f"human:{user.email}"


_PERIOD = Query("week", pattern="^(day|week|month)$")


@router.get("/companies/{company_id}/usage")
async def get_usage(
    company_id: int,
    since: date | None = None,
    until: date | None = None,
    period: str = _PERIOD,
    session: Session = Depends(get_session),  # noqa: B008 - FastAPI dependency marker.
    user: User | None = Depends(current_user_if_required),  # noqa: B008 - FastAPI dependency marker.
):
    require_company_role(session, user, company_id, "Manager")
    return _build(session, company_id, since, until, period)


@router.get("/companies/{company_id}/usage.csv")
async def export_usage(
    company_id: int,
    since: date | None = None,
    until: date | None = None,
    period: str = _PERIOD,
    session: Session = Depends(get_session),  # noqa: B008 - FastAPI dependency marker.
    user: User | None = Depends(current_user_if_required),  # noqa: B008 - FastAPI dependency marker.
):
    require_company_role(session, user, company_id, "Manager")
    report = _build(session, company_id, since, until, period)
    company = session.get(Company, company_id)
    body = usage_csv.render(report, company.company_name if company else None)
    AgentService().audit_interaction(
        session=session,
        company_id=company_id,
        interaction_id=f"usage-report:{company_id}",
        agent_id=_actor(user),
        action="usage_report_exported",
        details={"company_id": company_id},
        result={
            "since": report["since"],
            "until": report["until"],
            "period": report["period"],
            "generated_at": report["generated_at"],
            "rows": sum(len(report[f"by_{d}"]) for d in usage_report.DIMENSIONS),
        },
        decision_signature=None,
        cost_tokens=0,
    )
    session.commit()
    name = (
        f"usage-{company_id}-{report['since']}-{report['until']}-{report['period']}.csv"
    )
    return Response(
        content=body,
        media_type="text/csv; charset=utf-8",
        headers={
            "Content-Disposition": f'attachment; filename="{name}"',
            "Cache-Control": "no-store",
            "X-Content-Type-Options": "nosniff",
        },
    )
