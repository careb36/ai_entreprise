"""
Shared pytest fixtures for the Fabrique backend test suite.

Uses an in-memory SQLite database so tests run without requiring PostgreSQL,
Neo4j, or NATS.  The ``engine`` fixture is scoped to the *session* level so
tables are created once; individual tests receive a fresh ``Session`` that is
rolled back after each test to guarantee isolation.
"""

import os

# ── Patch env vars BEFORE any app module is imported ─────────────────────────
# This prevents the database.py module-level ``create_engine`` call from
# trying to connect to a real PostgreSQL instance.
os.environ.setdefault("POSTGRES_HOST", "localhost")
os.environ.setdefault("POSTGRES_DB", "test")
os.environ.setdefault("POSTGRES_USER", "test")
os.environ.setdefault("POSTGRES_PASSWORD", "test")

# Use a deterministic JWT secret for all auth tests.
os.environ.setdefault("JWT_SECRET_KEY", "test-secret-key-for-unit-tests")
os.environ.setdefault("JWT_ALGORITHM", "HS256")
os.environ.setdefault("JWT_ACCESS_TOKEN_EXPIRE_MINUTES", "60")

# Disable Fernet so crypto tests can opt-in explicitly.
os.environ.pop("FERNET_SECRET_KEY", None)

import sys
from pathlib import Path

# Make sure the ``app`` package (and its submodules) are importable from tests.
sys.path.insert(0, str(Path(__file__).parent.parent / "app"))

import pytest
from sqlmodel import SQLModel, Session, create_engine
from sqlalchemy.pool import StaticPool


# ── In-memory SQLite engine ───────────────────────────────────────────────────

@pytest.fixture(scope="session")
def test_engine():
    """
    Single in-memory SQLite engine shared across the test session.

    ``StaticPool`` is required so that all connections reuse the same
    in-memory database (SQLite's default creates a new DB per connection).
    ``check_same_thread=False`` allows the engine to be used from multiple
    threads / coroutines during async test execution.
    """
    engine = create_engine(
        "sqlite:///:memory:",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    SQLModel.metadata.create_all(engine)
    return engine


@pytest.fixture()
def db_session(test_engine):
    """
    Yield a SQLModel Session backed by the in-memory engine.

    The session is rolled back after each test so that state never leaks
    between test functions.
    """
    with Session(test_engine) as session:
        yield session
        session.rollback()
