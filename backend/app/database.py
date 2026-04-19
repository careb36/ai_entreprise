"""
Database connectivity layer for Fabrique Enterprise Backend.

Provides:
- A SQLModel / SQLAlchemy engine connected to PostgreSQL via ``psycopg2``.
- ``create_db_and_tables()`` — idempotent table creation called at startup.
- ``get_session()`` — FastAPI dependency that yields a scoped SQLModel session.
- ``get_neo4j_driver()`` — factory for the Neo4j bolt driver (Temporal Knowledge Graph).

Connection parameters are read from environment variables with safe defaults
for local development.
"""

import os
from typing import Generator

import neo4j
from sqlmodel import SQLModel, Session, create_engine

# ---------------------------------------------------------------------------
# PostgreSQL — SQLModel / SQLAlchemy engine
# ---------------------------------------------------------------------------

def _build_database_url() -> str:
    """Construct the PostgreSQL DSN from individual environment variables."""
    host = os.getenv("POSTGRES_HOST", "localhost")
    port = os.getenv("POSTGRES_PORT", "5432")
    user = os.getenv("POSTGRES_USER", "factory")
    password = os.getenv("POSTGRES_PASSWORD", "")
    db = os.getenv("POSTGRES_DB", "factory")
    return f"postgresql+psycopg2://{user}:{password}@{host}:{port}/{db}"


# Connection pool sized for moderate multi-tenant load:
# - pool_size=5  : permanent connections kept alive.
# - max_overflow=10: temporary connections allowed beyond the pool.
# - pool_pre_ping=True: validates connections before use to catch stale sockets.
engine = create_engine(
    _build_database_url(),
    pool_pre_ping=True,
    pool_size=5,
    max_overflow=10,
)


def _ensure_runtime_schema() -> None:
    """Apply lightweight additive fixes for local databases created by older schemas."""
    statements = (
        # agents — original additive fixes
        "ALTER TABLE IF EXISTS agents ADD COLUMN IF NOT EXISTS name VARCHAR(255) DEFAULT ''",
        "ALTER TABLE IF EXISTS agents ADD COLUMN IF NOT EXISTS model VARCHAR(255) DEFAULT ''",
        "ALTER TABLE IF EXISTS agents ADD COLUMN IF NOT EXISTS system_prompt TEXT",
        "ALTER TABLE IF EXISTS agents ADD COLUMN IF NOT EXISTS token_budget INTEGER DEFAULT 500000",
        # sprints — scope replaces duration_days
        "ALTER TABLE IF EXISTS sprints ADD COLUMN IF NOT EXISTS scope_size VARCHAR DEFAULT 'M'",
        "ALTER TABLE IF EXISTS sprints ALTER COLUMN duration_days DROP NOT NULL",
        # companies — default scope replaces default_sprint_length_days
        "ALTER TABLE IF EXISTS companies ADD COLUMN IF NOT EXISTS default_scope_size VARCHAR DEFAULT 'M'",
        "ALTER TABLE IF EXISTS companies ALTER COLUMN default_sprint_length_days DROP NOT NULL",
    )

    with engine.begin() as connection:
        for statement in statements:
            connection.exec_driver_sql(statement)


def create_db_and_tables() -> None:
    """Create all tables defined via SQLModel (idempotent — safe to call on startup)."""
    SQLModel.metadata.create_all(engine)
    _ensure_runtime_schema()


def get_session() -> Generator[Session, None, None]:
    """
    FastAPI dependency that yields a SQLModel Session.

    The session is automatically closed when the request completes,
    even if an exception is raised (context-manager behaviour of ``Session``).
    """
    with Session(engine) as session:
        yield session


# ---------------------------------------------------------------------------
# Neo4j driver (unchanged — Neo4j is not managed by SQLModel)
# ---------------------------------------------------------------------------

def get_neo4j_driver() -> neo4j.Driver:
    """
    Return a new Neo4j bolt driver instance.

    Credentials are read from environment variables.  This function creates a
    new driver object each time it is called; callers are responsible for
    closing the driver when they are done.
    """
    return neo4j.GraphDatabase.driver(
        os.getenv("NEO4J_URI", "bolt://localhost:7687"),
        auth=(os.getenv("NEO4J_USER", "neo4j"), os.getenv("NEO4J_PASSWORD", "")),
    )
