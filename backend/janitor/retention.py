"""
Retention: what the janitor deletes, and what it never deletes (issue #328).

Each setting is a number of days; ``0`` keeps everything of that kind.

- ``RETENTION_OUTBOX_DAYS`` (7): delivered outbox events (``status =
  'completed'``). Failed and pending events stay: someone has to look at them.
- ``RETENTION_AGENT_RUNS_DAYS`` (0): superseded versions of a run, older than
  the cutoff, with their verifications. The latest version of each
  ``(company, external_ref)`` is the evidence of record and is never deleted,
  nor is a version a person reviewed, approved, commented on or published:
  those are decisions, not working copies.
- ``RETENTION_KPI_TURNS_DAYS`` (0): the KPI turn rows of ``audit_log``
  (``execute_task``, ``qa_review``, ``architect_consult``) **outside** a hash
  chain (rows without a company, or written before the chain existed). A row
  of a company's chain is never deleted here: ``GET /v1/audit/chain`` would
  show the gap. The count of chained rows kept is reported.
- ``RETENTION_GLASSROOM_DAYS`` (0): stored task events (``task_events``) of
  sprints that are completed or cancelled. The live Glassroom stream is kept
  in memory (the last 50 events per sprint) and replays the board, which is
  never deleted.

There is no audit retention. Shrinking ``audit_log`` means archiving a
company's whole chain with its rows (``docs/OPERATIONS.md``), never deleting
from it.

The statements are plain SQL over a DB-API connection: psycopg2 in the
janitor's container, ``sqlite3`` in the tests.
"""

from __future__ import annotations

import os
from datetime import UTC, datetime, timedelta
from typing import Any

KPI_TURN_ACTIONS = ("execute_task", "qa_review", "architect_consult")
CLOSED_SPRINT_STATUSES = ("completed", "cancelled")
AGENT_RUN_BATCH = 500

DEFAULTS = {
    "RETENTION_OUTBOX_DAYS": 7,
    "RETENTION_AGENT_RUNS_DAYS": 0,
    "RETENTION_KPI_TURNS_DAYS": 0,
    "RETENTION_GLASSROOM_DAYS": 0,
}

# Tables whose rows point at an audit row: a row they point at is kept.
_POINT_AT_AUDIT_ROWS = (
    "agent_runs",
    "verifications",
    "agent_run_approvals",
    "run_reviews",
    "run_publications",
)

# Tables whose rows record a person's decision on a run: a run with any is kept.
_DECISIONS_ON_A_RUN = (
    "run_reviews",
    "review_comments",
    "run_publications",
    "agent_run_approvals",
)


def settings_from_env(env: dict[str, str] | None = None) -> dict[str, int]:
    """The retention days from the environment; a value that is not a whole number keeps the default."""
    env = os.environ if env is None else env
    days: dict[str, int] = {}
    for name, default in DEFAULTS.items():
        try:
            days[name] = max(0, int(env.get(name, default)))
        except (TypeError, ValueError):
            days[name] = default
    return days


def _is_sqlite(conn: Any) -> bool:
    return type(conn).__module__.startswith("sqlite3")


def _cutoff(conn: Any, days: int, now: datetime) -> Any:
    """The cutoff as the driver compares it: naive UTC (the janitor's session is in UTC)."""
    moment = (now - timedelta(days=days)).astimezone(UTC).replace(tzinfo=None)
    # SQLite stores SQLAlchemy datetimes as text in this format.
    return moment.strftime("%Y-%m-%d %H:%M:%S.%f") if _is_sqlite(conn) else moment


def _sql(conn: Any, statement: str) -> str:
    return statement.replace("%s", "?") if _is_sqlite(conn) else statement


def _in_list(values: tuple[str, ...]) -> str:
    return ", ".join(f"'{value}'" for value in values)


def _execute(conn: Any, statement: str, params: tuple = ()) -> Any:
    cursor = conn.cursor()
    cursor.execute(_sql(conn, statement), params)
    return cursor


def prune_outbox(conn: Any, days: int, now: datetime) -> int:
    if days <= 0:
        return 0
    cursor = _execute(
        conn,
        "DELETE FROM outbox_events WHERE status = 'completed' AND processed_at < %s",
        (_cutoff(conn, days, now),),
    )
    return cursor.rowcount


def prune_agent_runs(conn: Any, days: int, now: datetime) -> int:
    """Delete superseded, undecided run versions older than the cutoff, in batches."""
    if days <= 0:
        return 0
    cutoff = _cutoff(conn, days, now)
    kept_for_people = " ".join(
        f"AND NOT EXISTS (SELECT 1 FROM {table} d WHERE d.agent_run_id = r.id)"
        for table in _DECISIONS_ON_A_RUN
    )
    select_batch = (
        "SELECT r.id FROM agent_runs r WHERE r.ingested_at < %s "
        "AND EXISTS (SELECT 1 FROM agent_runs n WHERE n.company_id = r.company_id "
        "AND n.external_ref = r.external_ref AND n.version > r.version) "
        f"{kept_for_people} ORDER BY r.id LIMIT {AGENT_RUN_BATCH}"
    )
    removed = 0
    while True:
        ids = [row[0] for row in _execute(conn, select_batch, (cutoff,)).fetchall()]
        if not ids:
            return removed
        marks = ", ".join(["%s"] * len(ids))
        _execute(
            conn,
            f"DELETE FROM verifications WHERE agent_run_id IN ({marks})",
            tuple(ids),
        )
        _execute(conn, f"DELETE FROM agent_runs WHERE id IN ({marks})", tuple(ids))
        conn.commit()
        removed += len(ids)


def prune_kpi_turns(conn: Any, days: int, now: datetime) -> tuple[int, int]:
    """Delete unchained KPI turn rows older than the cutoff; return (deleted, chained kept)."""
    if days <= 0:
        return 0, 0
    cutoff = _cutoff(conn, days, now)
    actions = _in_list(KPI_TURN_ACTIONS)
    kept = _execute(
        conn,
        f"SELECT COUNT(*) FROM audit_log WHERE action IN ({actions}) "
        "AND chain_seq IS NOT NULL AND created_at < %s",
        (cutoff,),
    ).fetchone()[0]
    cursor = _execute(
        conn,
        f"DELETE FROM audit_log WHERE action IN ({actions}) "
        "AND chain_seq IS NULL AND created_at < %s "
        + "".join(
            f"AND id NOT IN (SELECT audit_log_id FROM {table} "
            "WHERE audit_log_id IS NOT NULL) "
            for table in _POINT_AT_AUDIT_ROWS
        ),
        (cutoff,),
    )
    return cursor.rowcount, kept


def prune_glassroom(conn: Any, days: int, now: datetime) -> int:
    if days <= 0:
        return 0
    cursor = _execute(
        conn,
        "DELETE FROM task_events WHERE created_at < %s AND sprint_id IN "
        f"(SELECT id FROM sprints WHERE status IN ({_in_list(CLOSED_SPRINT_STATUSES)}))",
        (_cutoff(conn, days, now),),
    )
    return cursor.rowcount


def apply(
    conn: Any, days: dict[str, int] | None = None, now: datetime | None = None
) -> dict[str, int]:
    """Apply every retention setting once; commit after each kind. Returns what was deleted."""
    days = settings_from_env() if days is None else {**DEFAULTS, **days}
    now = now or datetime.now(UTC)
    report: dict[str, int] = {}
    report["outbox_events"] = prune_outbox(conn, days["RETENTION_OUTBOX_DAYS"], now)
    conn.commit()
    report["agent_runs"] = prune_agent_runs(
        conn, days["RETENTION_AGENT_RUNS_DAYS"], now
    )
    conn.commit()
    deleted, kept = prune_kpi_turns(conn, days["RETENTION_KPI_TURNS_DAYS"], now)
    report["kpi_turns"] = deleted
    report["kpi_turns_kept_for_chain"] = kept
    conn.commit()
    report["task_events"] = prune_glassroom(conn, days["RETENTION_GLASSROOM_DAYS"], now)
    conn.commit()
    return report
