"""Who used what (issue #512, phase 5; ``docs/archive/design/TEAMS_AND_USAGE.md``).

The usage report sums a company's records over a UTC date range, by period
(day, week from Monday, month), by person, by team, by agent role, by producer
and by served model: tasks, runs, verified and refuted runs, rework, the
people's decisions, and the turns, tokens and cost the proxy reported.

Every number is an observation read back from the records, never a claim:

- a **task** is a ``backlog_items`` row created in the range; its rework is
  its ``NEEDS WORK:`` requests (``board.rework_request_count``); its producer
  and model are those of its latest run, as ``routing_record`` reads them;
- a **run** is an ``agent_runs`` row ingested in the range, with its producer,
  its role and the model the proxy reported serving (``AgentRun.model``); it
  is verified or refuted by its latest ``verifications`` verdict, unverified
  otherwise. What a producer says of its own run is never read;
- a **turn** is an LLM call's audit row (``run_cost.TURN_ACTIONS``) in the
  range: its served model (``details.model``), its role and the usage the
  proxy reported, priced by ``run_cost.turn_cost``. Only ``reported`` turns
  carry tokens; an estimate is counted apart (``estimated_tokens``) and never
  priced; a turn whose cost is unknown (estimated, unavailable, unpriced
  model, no input/output split) is counted in ``cost_unknown_turns`` and
  never as 0: ``cost_cents`` is the cost of the priced turns only, ``None``
  when none was priced. The proxy sees only the backend's own model calls
  (the task worker's Developer, QA review, Architect consultation), so every
  turn is the task worker's; a console producer's usage is its provider's and
  is not observed here;
- a **decision** is a person's review (``run_reviews``: ``approve`` or
  ``request_changes``) submitted in the range, by the account the session
  named (``author_user_id``), on the web or from chat.

A person is the account that started the task's sprint
(``Sprint.started_by_user_id``, from the session) for its tasks, runs and
turns, and the reviewer for a decision; anything without one (a sprint the
platform opened, one from before migration 0029, a run not about a task) is
unattributed (``None``), never guessed. A person's teams are the teams they
were in at the fact's own observed time (``team_membership_periods``, issue
#702; ``teams_at``); a fact from before the history started (migration 0052,
``team_history_start``) keeps the teams they belong to when the report is
made, and ``team_history`` says which part of the range that covers. A person
in two teams counts in both, so the team rows may sum to more than the total.

Times are UTC: a naive timestamp is read as UTC, an aware one converted.
The same records and the same ``now`` give the same report, byte for byte:
rows sorted by key (unattributed last), cents rounded to six places.
"""

from __future__ import annotations

from collections.abc import Iterable
from datetime import UTC, date, datetime, timedelta
from typing import Any

from services.models import model_prices, run_cost
from services.tasks.board import rework_request_count

PERIODS = ("day", "week", "month")
DIMENSIONS = ("period", "person", "team", "role", "producer", "model")
MAX_DAYS = 366
DECISION_EVENTS = ("approve", "request_changes")
VERIFIED, REFUTED = "verified", "refuted"
COLUMNS = (
    "tasks",
    "rework",
    "runs",
    "verified",
    "refuted",
    "unverified",
    "approvals",
    "changes_requested",
    "turns",
    "reported_turns",
    "estimated_turns",
    "unavailable_turns",
    "input_tokens",
    "output_tokens",
    "reported_tokens",
    "estimated_tokens",
    "cost_cents",
    "off_peak_turns",
    "cost_unknown_turns",
)


class UsageRangeError(ValueError):
    """A range the report refuses: ``until`` before ``since``, or too long."""


def naive_utc(moment: datetime | None) -> datetime | None:
    if moment is None:
        return None
    if moment.tzinfo is not None:
        moment = moment.astimezone(UTC).replace(tzinfo=None)
    return moment


def window(since: date, until: date) -> tuple[datetime, datetime]:
    """The half-open UTC window ``[since 00:00, until+1 00:00)``."""
    if until < since:
        raise UsageRangeError("until is before since")
    if (until - since).days + 1 > MAX_DAYS:
        raise UsageRangeError(f"a report covers at most {MAX_DAYS} days")
    start = datetime(since.year, since.month, since.day)
    return start, datetime(until.year, until.month, until.day) + timedelta(days=1)


def bucket(moment: datetime, period: str) -> str:
    """The period *moment* (UTC) falls in, as its first day: ``2026-11-09``.

    A week starts on Monday (ISO 8601); a month on its first day.
    """
    day = naive_utc(moment).date()
    if period == "week":
        day = day - timedelta(days=day.weekday())
    elif period == "month":
        day = day.replace(day=1)
    elif period != "day":
        raise UsageRangeError(f"unknown period {period!r}")
    return day.isoformat()


def task_id_of(ref: str | None) -> int | None:
    prefix, _, number = (ref or "").partition("-")
    return int(number) if prefix == "task" and number.isdigit() else None


def empty() -> dict[str, Any]:
    row: dict[str, Any] = dict.fromkeys(COLUMNS, 0)
    row["cost_cents"] = None
    return row


def turn_numbers(turn: dict[str, Any]) -> dict[str, Any]:
    """One priced turn (``run_cost.turn_cost``) as report columns."""
    out: dict[str, Any] = {"turns": 1}
    status = turn.get("status")
    if status == "estimated":
        out["estimated_turns"] = 1
        out["estimated_tokens"] = int(turn.get("estimated_tokens") or 0)
        out["cost_unknown_turns"] = 1
        return out
    if status != "reported":
        out["unavailable_turns"] = 1
        out["cost_unknown_turns"] = 1
        return out
    out["reported_turns"] = 1
    out["reported_tokens"] = int(turn.get("tokens") or 0)
    out["input_tokens"] = int(turn.get("input_tokens") or 0)
    out["output_tokens"] = int(turn.get("output_tokens") or 0)
    if "cents" in turn:
        out["cost_cents"] = float(turn["cents"])
        out["off_peak_turns"] = int(bool(turn.get("off_peak")))
    else:
        out["cost_unknown_turns"] = 1
    return out


def add(row: dict[str, Any], numbers: dict[str, Any]) -> None:
    for key, value in numbers.items():
        if key == "cost_cents":
            row[key] = (row[key] or 0.0) + value
        else:
            row[key] += value


def _finish(row: dict[str, Any]) -> dict[str, Any]:
    if row["cost_cents"] is not None:
        row["cost_cents"] = round(row["cost_cents"], 6)
    return row


def _order(key: Any) -> tuple[int, str]:
    return (1, "") if key is None else (0, str(key))


def aggregate(
    facts: Iterable[dict[str, Any]],
    *,
    period: str,
    people: dict[int, str],
    teams_of: dict[int, list[int]],
    team_names: dict[int, str],
) -> dict[str, Any]:
    """Pure: the report's rows from its facts.

    A fact is ``{"at", "person", "role", "producer", "model", "numbers"}``.
    Returns ``{"totals": row, "by_<dimension>": [rows]}``; a row is
    ``{"key", "label", **COLUMNS}``.
    """
    totals = empty()
    groups: dict[str, dict[Any, dict[str, Any]]] = {d: {} for d in DIMENSIONS}
    for fact in facts:
        numbers = fact["numbers"]
        add(totals, numbers)
        person = fact.get("person")
        if "teams" in fact:
            team_keys = fact["teams"] or [None]
        else:
            team_keys = (
                (teams_of.get(person) or [None]) if person is not None else [None]
            )
        keys = {
            "period": [bucket(fact["at"], period)],
            "person": [person],
            "team": team_keys,
            "role": [fact.get("role") or None],
            "producer": [fact.get("producer") or None],
            "model": [fact.get("model") or None],
        }
        for dimension, values in keys.items():
            for value in values:
                add(groups[dimension].setdefault(value, empty()), numbers)

    def label(dimension: str, key: Any) -> str | None:
        if key is None:
            return None
        if dimension == "person":
            return people.get(key) or f"#{key}"
        if dimension == "team":
            return team_names.get(key) or f"#{key}"
        return str(key)

    out: dict[str, Any] = {"totals": _finish(totals)}
    for dimension in DIMENSIONS:
        rows = groups[dimension]
        out[f"by_{dimension}"] = [
            {"key": key, "label": label(dimension, key), **_finish(rows[key])}
            for key in sorted(rows, key=_order)
        ]
    return out


def teams_at(
    person: int | None,
    at: datetime,
    *,
    current: dict[int, list[int]],
    periods: dict[int, list[tuple[int, datetime, datetime | None]]],
    started: datetime | None,
) -> list[int]:
    """Pure: the teams *person* was in at *at*, sorted.

    A period ``(team_id, start, end)`` holds from its start (inclusive) to its
    end (exclusive; ``None``: still open). Before *started*, or with no
    history at all (``None``), today's teams (*current*) are the answer.
    """
    if person is None:
        return []
    moment = naive_utc(at)
    if started is None or moment < started:
        return sorted(current.get(person, []))
    return sorted(
        {
            team_id
            for team_id, start, end in periods.get(person, [])
            if naive_utc(start) <= moment and (end is None or moment < naive_utc(end))
        }
    )


def history_note(
    started: datetime | None, start: datetime, end: datetime, since: date
) -> dict[str, Any]:
    """Pure: when the history started and the part of the range ``[start,
    end)`` that keeps today's teams (``None`` when the history covers it)."""

    def stamp(moment: datetime) -> str:
        return moment.replace(microsecond=0).isoformat() + "Z"

    started = naive_utc(started)
    before = end if started is None else min(started, end)
    return {
        "started_at": stamp(started) if started else None,
        "current_teams_cover": (
            {"since": since.isoformat(), "before": stamp(before)}
            if before > start
            else None
        ),
    }


def _within(moment: datetime | None, start: datetime, end: datetime) -> bool:
    moment = naive_utc(moment)
    return moment is not None and start <= moment < end


def report(
    session: Any,
    company_id: int,
    *,
    since: date,
    until: date,
    period: str,
    now: datetime,
) -> dict[str, Any]:
    """The company's usage between *since* and *until* (UTC dates, inclusive)."""
    from models import (
        AgentRun,
        AuditLog,
        BacklogItem,
        RunReview,
        Sprint,
        Team,
        User,
        Verification,
    )
    from models.agent_run import TASK_WORKER_PRODUCER
    from sqlmodel import select

    from services.platform import teams

    if period not in PERIODS:
        raise UsageRangeError(f"unknown period {period!r}")
    start, end = window(since, until)
    # Read a day wider and filter exactly here: rows written with an aware
    # timestamp compare as text on SQLite.
    lo, hi = start - timedelta(days=1), end + timedelta(days=1)

    tasks = [
        item
        for item in session.exec(
            select(BacklogItem)
            .join(Sprint, Sprint.id == BacklogItem.sprint_id)
            .where(
                Sprint.company_id == company_id,
                BacklogItem.created_at >= lo,
                BacklogItem.created_at < hi,
            )
            .order_by(BacklogItem.id)
        ).all()
        if _within(item.created_at, start, end)
    ]
    runs_in = [
        run
        for run in session.exec(
            select(AgentRun)
            .where(
                AgentRun.company_id == company_id,
                AgentRun.ingested_at >= lo,
                AgentRun.ingested_at < hi,
            )
            .order_by(AgentRun.id)
        ).all()
        if _within(run.ingested_at, start, end)
    ]
    turn_rows = [
        row
        for row in session.exec(
            select(AuditLog)
            .where(
                AuditLog.company_id == company_id,
                AuditLog.action.in_(run_cost.TURN_ACTIONS),
                AuditLog.created_at >= lo,
                AuditLog.created_at < hi,
            )
            .order_by(AuditLog.id)
        ).all()
        if _within(row.created_at, start, end)
    ]
    reviews = [
        review
        for review in session.exec(
            select(RunReview)
            .where(
                RunReview.company_id == company_id,
                RunReview.event.in_(DECISION_EVENTS),
                RunReview.created_at >= lo,
                RunReview.created_at < hi,
            )
            .order_by(RunReview.id)
        ).all()
        if _within(review.created_at, start, end)
    ]

    # Who started each task's sprint, for every task the facts name.
    task_ids = {item.id for item in tasks}
    task_ids |= {task_id_of(r.external_ref) for r in runs_in}
    task_ids |= {task_id_of(r.interaction_id) for r in turn_rows}
    task_ids.discard(None)
    starter: dict[int, int | None] = {}
    if task_ids:
        for item_id, user_id in session.exec(
            select(BacklogItem.id, Sprint.started_by_user_id)
            .join(Sprint, Sprint.id == BacklogItem.sprint_id)
            .where(Sprint.company_id == company_id, BacklogItem.id.in_(task_ids))
        ).all():
            starter[item_id] = user_id

    # Each task's latest run (any producer), for its producer and model; and
    # the runs the decisions are about.
    latest: dict[int, Any] = {}
    refs = [run_cost.task_ref(i) for i in sorted(t.id for t in tasks)]
    if refs:
        for run in session.exec(
            select(AgentRun)
            .where(AgentRun.company_id == company_id, AgentRun.external_ref.in_(refs))
            .order_by(AgentRun.id)
        ).all():
            latest[task_id_of(run.external_ref)] = run
    reviewed_ids = sorted({r.agent_run_id for r in reviews})
    reviewed = {
        run.id: run
        for run in (
            session.exec(
                select(AgentRun).where(
                    AgentRun.company_id == company_id, AgentRun.id.in_(reviewed_ids)
                )
            ).all()
            if reviewed_ids
            else []
        )
    }

    verdicts: dict[int, str] = {}
    run_ids = [r.id for r in runs_in]
    if run_ids:
        for verification in session.exec(
            select(Verification)
            .where(
                Verification.company_id == company_id,
                Verification.agent_run_id.in_(run_ids),
            )
            .order_by(Verification.observed_at, Verification.id)
        ).all():
            verdicts[verification.agent_run_id] = verification.verdict

    facts: list[dict[str, Any]] = []
    for item in tasks:
        run = latest.get(item.id)
        facts.append(
            {
                "at": item.created_at,
                "person": starter.get(item.id),
                "role": item.assignee,
                "producer": run.producer if run else None,
                "model": run.model if run else None,
                "numbers": {"tasks": 1, "rework": rework_request_count(item.comments)},
            }
        )
    for run in runs_in:
        verdict = verdicts.get(run.id)
        facts.append(
            {
                "at": run.ingested_at,
                "person": starter.get(task_id_of(run.external_ref)),
                "role": run.agent_role,
                "producer": run.producer,
                "model": run.model,
                "numbers": {
                    "runs": 1,
                    "verified": int(verdict == VERIFIED),
                    "refuted": int(verdict == REFUTED),
                    "unverified": int(verdict not in (VERIFIED, REFUTED)),
                },
            }
        )
    connections = run_cost._connections(session, company_id)
    calendars = model_prices.off_peak_calendars()
    for row in turn_rows:
        details = row.details if isinstance(row.details, dict) else {}
        turn = run_cost.turn_cost(row, connections, calendars=calendars)
        facts.append(
            {
                "at": row.created_at,
                "person": starter.get(task_id_of(row.interaction_id)),
                "role": details.get("agent_role") or details.get("role"),
                "producer": TASK_WORKER_PRODUCER,
                "model": turn.get("model"),
                "numbers": turn_numbers(turn),
            }
        )
    for review in reviews:
        run = reviewed.get(review.agent_run_id)
        approve = review.event == "approve"
        facts.append(
            {
                "at": review.created_at,
                "person": review.author_user_id,
                "role": run.agent_role if run else None,
                "producer": run.producer if run else None,
                "model": run.model if run else None,
                "numbers": {
                    "approvals": int(approve),
                    "changes_requested": int(not approve),
                },
            }
        )

    teams_of = teams.teams_of(session, company_id)
    person_ids = sorted({f["person"] for f in facts if f["person"] is not None})
    started = naive_utc(teams.history_started_at(session))
    periods: dict[int, list[tuple[int, datetime, datetime | None]]] = {}
    old_names: dict[int, str] = {}
    for row in teams.membership_periods(session, company_id, person_ids):
        periods.setdefault(row.user_id, []).append(
            (row.team_id, row.started_at, row.ended_at)
        )
        old_names[row.team_id] = row.team_name
    for fact in facts:
        fact["teams"] = teams_at(
            fact["person"],
            fact["at"],
            current=teams_of,
            periods=periods,
            started=started,
        )
    people = {
        user.id: (user.full_name or "").strip() or f"#{user.id}"
        for user in (
            session.exec(select(User).where(User.id.in_(person_ids))).all()
            if person_ids
            else []
        )
    }
    team_names = old_names | {
        team.id: team.name
        for team in session.exec(
            select(Team).where(Team.company_id == company_id)
        ).all()
    }
    return {
        "company_id": company_id,
        "since": since.isoformat(),
        "until": until.isoformat(),
        "period": period,
        "timezone": "UTC",
        "generated_at": naive_utc(now).replace(microsecond=0).isoformat() + "Z",
        "columns": list(COLUMNS),
        "team_history": history_note(started, start, end, since),
        **aggregate(
            facts,
            period=period,
            people=people,
            teams_of=teams_of,
            team_names=team_names,
        ),
    }
