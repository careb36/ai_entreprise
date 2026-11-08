"""
Database connectivity layer for Fabrique Enterprise Backend.

Provides:
- A SQLModel / SQLAlchemy engine connected to PostgreSQL via ``psycopg2``.
- ``prepare_database()`` — checks the schema is at the migrations' head at startup.
- ``get_session()`` — FastAPI dependency that yields a scoped SQLModel session.
- ``get_neo4j_driver()`` — factory for the Neo4j bolt driver (Temporal Knowledge Graph).

Connection parameters are read from environment variables with safe defaults
for local development.
"""

import os
from collections.abc import Generator

import neo4j
from logging_config import logger
from sqlmodel import Session, create_engine

# ---------------------------------------------------------------------------
# PostgreSQL — SQLModel / SQLAlchemy engine
# ---------------------------------------------------------------------------


def _build_database_url() -> str:
    """Return the DSN: ``DATABASE_URL`` when set, Postgres otherwise.

    ``DATABASE_URL`` exists so the desktop install (``run_fusion.py``) can run
    the factory with no infrastructure (``sqlite:///…/factory.db``).
    Production keeps the Postgres default.
    """
    override = os.getenv("DATABASE_URL")
    if override:
        return override
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
def _connect_args(url: str) -> dict[str, str]:
    """Pin a Postgres session to UTC.

    The models write aware UTC datetimes into ``timestamp`` columns and the
    board's comments carry naive UTC text. Postgres converts an aware value
    to the session's zone before dropping it, so a server set to another
    zone would keep the two in different clocks, and every comparison
    between them (a triage against a person's send-back, for one) would be
    off by the offset.
    """
    return {"options": "-c timezone=utc"} if url.startswith("postgresql") else {}


_DATABASE_URL = _build_database_url()
_IS_SQLITE = _DATABASE_URL.startswith("sqlite")
# Public alias: the portable code paths (row locking, JSON columns) need
# to branch on the engine without importing a private name.
IS_SQLITE = _IS_SQLITE

_ENGINE_ARGS: dict = {
    "connect_args": {"check_same_thread": False}
    if _IS_SQLITE
    else _connect_args(_DATABASE_URL),
}
if not _IS_SQLITE:
    _ENGINE_ARGS.update(pool_pre_ping=True, pool_size=5, max_overflow=10)

engine = create_engine(_DATABASE_URL, **_ENGINE_ARGS)


def prepare_database() -> None:
    """The schema as the migrations define it (``app/migrations.py``).

    The backend starts only on a database at the migrations' head. The
    container's entrypoint and the e2e run ``alembic upgrade head`` first;
    ``DB_AUTO_UPGRADE=true`` does it here, for a local database. A database
    behind head stops the start with both versions in the error, instead of
    being patched in place as the old startup did.
    """
    from config import settings
    from migrations import require_schema_at_head, upgrade_to_head

    if settings.DB_AUTO_UPGRADE:
        logger.info("DB_AUTO_UPGRADE: running the migrations to head")
        upgrade_to_head(engine)
    head = require_schema_at_head(engine)
    logger.info("Database schema at %s", head)
    _check_vector_memory()


def _check_vector_memory() -> None:
    # vector_memory is created by db/init-db/init.sql (it needs pgvector), not
    # by the models, so a database set up without that script has no such table.
    if engine.dialect.name != "postgresql":
        return
    with engine.connect() as connection:
        has_vector_memory = connection.exec_driver_sql(
            "SELECT to_regclass('vector_memory') IS NOT NULL"
        ).scalar()
    if not has_vector_memory:
        logger.warning(
            "Table vector_memory is missing: memory search and RAG are "
            "unavailable until db/init-db/init.sql is applied to this database."
        )


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


def neo4j_disabled() -> bool:
    """True when the install runs without the optional graph backend.

    ``NEO4J_ENABLED=false`` (the setting) or ``NEO4J_DISABLED=true`` (what the
    desktop launcher sets) both say so.
    """
    from config import settings

    if not settings.NEO4J_ENABLED:
        return True
    return os.getenv("NEO4J_DISABLED", "").strip().lower() in {"1", "true", "yes", "on"}


def get_neo4j_driver() -> neo4j.Driver:
    """
    Return a new Neo4j bolt driver instance.

    Credentials are read from environment variables.  This function creates a
    new driver object each time it is called; callers are responsible for
    closing the driver when they are done.

    A disabled graph (``neo4j_disabled``) fails fast instead of making every
    caller wait for the driver's connect timeout; the desktop install disables
    it because memory search is optional there.
    """
    if neo4j_disabled():
        raise RuntimeError("Neo4j is disabled (NEO4J_ENABLED=false)")
    return neo4j.GraphDatabase.driver(
        os.getenv("NEO4J_URI", "bolt://localhost:7687"),
        auth=(os.getenv("NEO4J_USER", "neo4j"), os.getenv("NEO4J_PASSWORD", "")),
    )


def ensure_memory_graph_schema(driver) -> None:
    """Backfill historical graph ownership without rewriting evidence metadata."""
    from models.company import Company
    from sqlmodel import select

    with Session(engine) as sql_session:
        company_ids = list(sql_session.exec(select(Company.id)).all())
    with driver.session() as session:
        session.run(
            """
            MATCH (e)
            WHERE any(label IN labels(e) WHERE label STARTS WITH 'Episodic_')
            SET e:Episodic
            """
        ).consume()
        session.run(
            """
            MATCH (e)
            WHERE (e:Memory OR any(label IN labels(e) WHERE label STARTS WITH 'Episodic'))
              AND e.company_id IS NULL AND e.metadata IS NOT NULL
            WITH e, toInteger(apoc.convert.fromJsonMap(e.metadata).company_id) AS company_id
            WHERE company_id IN $company_ids
            SET e.company_id = company_id
            """,
            company_ids=company_ids,
        ).consume()
        session.run(
            "CREATE INDEX episodic_company_id IF NOT EXISTS FOR (e:Episodic) ON (e.company_id)"
        ).consume()
