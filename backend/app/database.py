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

from collections.abc import Generator

import neo4j
from config import settings
from logging_config import logger
from sqlmodel import Session, create_engine

# ---------------------------------------------------------------------------
# PostgreSQL — SQLModel / SQLAlchemy engine
# ---------------------------------------------------------------------------


def _build_database_url() -> str:
    """Construct the PostgreSQL DSN from the POSTGRES_* settings."""
    s = settings
    return (
        f"postgresql+psycopg2://{s.POSTGRES_USER}:{s.POSTGRES_PASSWORD}"
        f"@{s.POSTGRES_HOST}:{s.POSTGRES_PORT}/{s.POSTGRES_DB}"
    )


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
engine = create_engine(
    _DATABASE_URL,
    pool_pre_ping=True,
    pool_size=5,
    max_overflow=10,
    connect_args=_connect_args(_DATABASE_URL),
)


def prepare_database() -> bool:
    """The schema as the migrations define it (``app/migrations.py``).

    The backend starts only on a database at the migrations' head. The
    container's entrypoint and the e2e run ``alembic upgrade head`` first;
    ``DB_AUTO_UPGRADE=true`` does it here, for a local database. A database
    behind head stops the start with both versions in the error, instead of
    being patched in place as the old startup did.

    Returns whether the vector memory (pgvector's ``vector_memory``) is there.
    """
    from config import settings
    from migrations import require_schema_at_head, upgrade_to_head

    if settings.DB_AUTO_UPGRADE:
        logger.info("DB_AUTO_UPGRADE: running the migrations to head")
        upgrade_to_head(engine)
    head = require_schema_at_head(engine)
    logger.info("Database schema at %s", head)
    return _check_vector_memory()


def _check_vector_memory() -> bool:
    # vector_memory is created by db/init-db/init.sql (it needs pgvector), not
    # by the models, so a database set up without that script has no such table.
    if engine.dialect.name != "postgresql":
        return False
    with engine.connect() as connection:
        has_vector_memory = connection.exec_driver_sql(
            "SELECT to_regclass('vector_memory') IS NOT NULL"
        ).scalar()
    if not has_vector_memory:
        logger.warning(
            "Table vector_memory is missing: memory search and RAG are "
            "unavailable until db/init-db/init.sql is applied to this database."
        )
    return bool(has_vector_memory)


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

    Credentials come from the NEO4J_* settings.  This function creates a
    new driver object each time it is called; callers are responsible for
    closing the driver when they are done.
    """
    return neo4j.GraphDatabase.driver(
        settings.NEO4J_URI,
        auth=(settings.NEO4J_USER, settings.NEO4J_PASSWORD),
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
