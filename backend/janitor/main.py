"""
Fabrique AI Janitor — daily database maintenance service.

Runs as a standalone long-lived container (or bare process).  Executes two
maintenance tasks every 24 hours:

1. **Retention** (``retention.py``, issue #328) — delivered outbox events
   older than ``RETENTION_OUTBOX_DAYS`` (7), and, when their settings are
   above 0, superseded run versions (``RETENTION_AGENT_RUNS_DAYS``), KPI
   turn rows outside the audit hash chain (``RETENTION_KPI_TURNS_DAYS``) and
   stored task events of closed sprints (``RETENTION_GLASSROOM_DAYS``), and
   personal notifications past ``RETENTION_NOTIFICATIONS_DAYS`` (90, never
   unbounded) or of a person who left the company. A row of a company's
   audit chain is never deleted.

2. **Vector index rebuild** — runs ``REINDEX INDEX CONCURRENTLY`` on the
   ``vector_memory_idx`` HNSW/IVFFlat index so that ANN search stays fast
   after large insert batches.  ``CONCURRENTLY`` means read queries are not
   blocked during the rebuild.

Why a separate container?
--------------------------
Maintenance operations that hold locks (even temporarily) can interfere with
online request-handling.  Isolating them in a dedicated container lets us
schedule them during low-traffic windows and restart them independently of the
main API.
"""

import logging
import os
import sys
import time

import psycopg2

try:  # the container copies retention.py beside this file
    import retention
except ImportError:  # run from backend/ as a package
    from janitor import retention

logging.basicConfig(
    level=logging.INFO, format="%(asctime)s - %(name)s - %(levelname)s - %(message)s"
)
logger = logging.getLogger("fabrique-janitor")


class MissingDatabasePassword(RuntimeError):
    """``POSTGRES_PASSWORD`` is unset or blank: the janitor does not start."""


def database_password() -> str:
    """The database password from ``POSTGRES_PASSWORD``, never a default.

    The janitor used to fall back to the placeholder from ``.env.example``,
    a password anyone can read; an unset or blank variable now stops it.
    """
    password = os.getenv("POSTGRES_PASSWORD", "")
    if not password.strip():
        raise MissingDatabasePassword(
            "POSTGRES_PASSWORD is not set: the janitor has no database password "
            "and does not start (set it in .env, docs/CONFIGURATION.md)."
        )
    return password


def check_configuration() -> None:
    """Exit with status 1 before the daily loop, whose errors are only logged."""
    try:
        database_password()
    except MissingDatabasePassword as exc:
        logger.error("Janitor refused to start: %s", exc)
        sys.exit(1)


def get_db_connection():
    """
    Establish a direct psycopg2 connection for the Janitor process.

    Uses psycopg2 directly (not SQLModel/SQLAlchemy) so the reindex can
    switch to ``autocommit = True``, which ``REINDEX CONCURRENTLY`` requires
    (that DDL command cannot run inside a transaction block).
    """
    return psycopg2.connect(
        host=os.getenv("POSTGRES_HOST", "localhost"),
        port=int(os.getenv("POSTGRES_PORT", "5432")),
        user=os.getenv("POSTGRES_USER", "factory"),
        password=database_password(),
        database=os.getenv("POSTGRES_DB", "factory"),
    )


def run_janitor_tasks():
    """
    Execute all daily maintenance tasks in a single database session.

    Retention runs in transactions (each batch of deletions commits whole or
    rolls back); only ``REINDEX CONCURRENTLY`` runs in autocommit, which it
    requires. All tasks are idempotent and safe to run repeatedly.
    """
    conn = None
    cursor = None
    try:
        conn = get_db_connection()
        conn.autocommit = False
        cursor = conn.cursor()

        # One-time migration for the retired approval queue (issue #42).
        cursor.execute("DROP TABLE IF EXISTS task_queue")
        logger.info("Janitor: Dropped retired task_queue table if present.")

        # 1. Retention. Cutoffs are naive UTC, so the session must be in UTC.
        cursor.execute("SET TIME ZONE 'UTC'")
        conn.commit()
        days = retention.settings_from_env()
        report = retention.apply(conn, days)
        logger.info("Janitor: retention %s deleted %s", days, report)
        if report.get("kpi_turns_kept_for_chain"):
            logger.info(
                "Janitor: kept %s KPI turn rows of a company's audit chain; "
                "archive the chain with its rows to shrink it (docs/OPERATIONS.md).",
                report["kpi_turns_kept_for_chain"],
            )

        # 2. Vector Index Rebuild (NON-BLOCKING for RAG queries). CONCURRENTLY
        # cannot run inside a transaction block: autocommit for this alone.
        conn.autocommit = True
        logger.info(
            "Janitor: Rebuilding vector memory index concurrently to prevent read blocks..."
        )
        cursor.execute("REINDEX INDEX CONCURRENTLY vector_memory_idx")
        logger.info("Janitor: Vector index rebuilt successfully.")

    except Exception as e:  # noqa: BLE001 - The janitor retries tomorrow.
        logger.error(f"Janitor failed: {e}")
    finally:
        if cursor:
            cursor.close()
        if conn:
            conn.close()


if __name__ == "__main__":
    # Simulate a daily cron job running in the background container
    # For production, tools like Celery or pure cron are better, but this demonstrates the isolated architecture.
    check_configuration()
    logger.info("Fabrique AI Janitor Service Online.")
    while True:
        run_janitor_tasks()
        time.sleep(int(os.getenv("JANITOR_INTERVAL_SECONDS", "86400")))
