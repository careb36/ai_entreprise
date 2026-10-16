"""
Shared pytest fixtures for the Fabrique backend test suite.

Provides:
- ``sqlite_session`` — a SQLModel Session backed by an in-memory SQLite engine.
- ``test_client``    — a FastAPI TestClient with ``get_session`` overridden to the
                       in-memory SQLite session, so no Postgres connection is needed.

Usage::

    def test_something(sqlite_session, test_client):
        ...
"""

import asyncio
import types
import unittest.mock as mock

import pytest
from fastapi.testclient import TestClient
from sqlmodel import Session, SQLModel, create_engine
from sqlalchemy.pool import StaticPool

# ---------------------------------------------------------------------------
# Import the SQLModel table models so their metadata is registered before
# SQLModel.metadata.create_all() is called.  Only the subset actually used by
# the sprint router / agent worker tests is listed here; adding more is safe.
# ---------------------------------------------------------------------------
from models.company import (  # noqa: F401
    Company,
    BusinessUnit,
    Agent,
    ProjectCredential,
    User,
    CompanyMember,
)
from models.sprint import Sprint, BacklogItem, OutboxEvent  # noqa: F401
from models.agent_run import AgentRun, Verification  # noqa: F401
from models.audit import AuditLog  # noqa: F401
from models.github import GitHubRepoBinding  # noqa: F401


# ---------------------------------------------------------------------------
# In-memory SQLite engine — shared across all tests within a session via
# StaticPool so every Session sees the same in-memory database.
# ---------------------------------------------------------------------------
_SQLITE_URL = "sqlite://"

_engine = create_engine(
    _SQLITE_URL,
    connect_args={"check_same_thread": False},
    poolclass=StaticPool,
)

SQLModel.metadata.create_all(
    _engine,
    tables=[
        Company.__table__,
        User.__table__,
        CompanyMember.__table__,
        BusinessUnit.__table__,
        Agent.__table__,
        ProjectCredential.__table__,
        Sprint.__table__,
        BacklogItem.__table__,
        OutboxEvent.__table__,
        AgentRun.__table__,
        Verification.__table__,
        AuditLog.__table__,
        GitHubRepoBinding.__table__,
    ],
)


@pytest.fixture()
def sqlite_session():
    """Yield a SQLModel Session backed by the shared in-memory SQLite engine."""
    with Session(_engine) as session:
        yield session
        session.rollback()  # isolate each test


class _FakeNATSClient:
    """Minimal stub for NATSClient so no broker is required during tests."""

    async def connect(self):
        pass

    async def close(self):
        pass

    async def drain(self):
        pass

    async def publish(self, subject, payload):
        pass

    async def subscribe(self, subject, callback):
        pass

    @property
    def is_connected(self):
        return False


@pytest.fixture()
def test_client(sqlite_session):
    """
    FastAPI TestClient with get_session overridden to the in-memory SQLite session.

    Startup hooks that touch Postgres (create_db_and_tables), NATS, and Neo4j
    are all patched out so no external services are required.
    """
    import database
    from app.main import app
    from database import get_session

    def override_get_session():
        yield sqlite_session

    app.dependency_overrides[get_session] = override_get_session

    fake_nats = _FakeNATSClient()

    with (
        # Patch the startup-time calls inside main.py:
        # main.py is loaded as the "app.main" module in this PYTHONPATH setup,
        # so we must patch names inside that module's namespace.
        mock.patch("app.main.create_db_and_tables", return_value=None),
        mock.patch("app.main.get_neo4j_driver", side_effect=Exception("neo4j stubbed")),
        mock.patch("app.main.NATSClient", return_value=fake_nats),
    ):
        with TestClient(app, raise_server_exceptions=False) as client:
            # Ensure app.state has a nats_client so router code that reads it won't crash
            if not hasattr(app.state, "nats_client") or app.state.nats_client is None:
                app.state.nats_client = fake_nats
            yield client

    app.dependency_overrides.clear()
