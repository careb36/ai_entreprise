import os
import time
import logging
import psycopg2

logging.basicConfig(level=logging.INFO, format='%(asctime)s - %(name)s - %(levelname)s - %(message)s')
logger = logging.getLogger("fabrique-janitor")

def get_db_connection():
    """Establish connection for the Janitor script."""
    return psycopg2.connect(
        host=os.getenv("POSTGRES_HOST", "localhost"),
        port=int(os.getenv("POSTGRES_PORT", "5432")),
        user=os.getenv("POSTGRES_USER", "factory"),
        password=os.getenv("POSTGRES_PASSWORD", "your_secure_password_here"),
        database=os.getenv("POSTGRES_DB", "factory")
    )

def run_janitor_tasks():
    logger.info("Starting Daily AI Janitor Tasks...")
    conn = None
    cursor = None
    try:
        conn = get_db_connection()
        # CONCURRENTLY requires auto-commit mode (cannot be run inside a transaction block)
        conn.autocommit = True
        cursor = conn.cursor()

        # 1. Clean Outbox (Uses the new partial index)
        cursor.execute(
            "DELETE FROM outbox_events WHERE status = 'completed' AND processed_at < NOW() - INTERVAL '7 days'"
        )
        logger.info(f"Janitor: Deleted {cursor.rowcount} old outbox events.")

        # 2. Clean Task Queue (Uses the new partial index and completed_at)
        cursor.execute(
            "DELETE FROM task_queue WHERE status IN ('completed', 'failed') AND completed_at < NOW() - INTERVAL '30 days'"
        )
        logger.info(f"Janitor: Deleted {cursor.rowcount} old tasks from queue.")

        # 3. Vector Index Rebuild (NON-BLOCKING for RAG queries)
        logger.info("Janitor: Rebuilding vector memory index concurrently to prevent read blocks...")
        cursor.execute("REINDEX INDEX CONCURRENTLY vector_memory_idx")
        logger.info("Janitor: Vector index rebuilt successfully.")

    except Exception as e:
        logger.error(f"Janitor failed: {e}")
    finally:
        if cursor:
            cursor.close()
        if conn:
            conn.close()

if __name__ == "__main__":
    # Simulate a daily cron job running in the background container
    # For production, tools like Celery or pure cron are better, but this demonstrates the isolated architecture.
    logger.info("Fabrique AI Janitor Service Online.")
    while True:
        run_janitor_tasks()
        # Run every 24 hours
        time.sleep(86400)
