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

import sys
from pathlib import Path
from unittest import mock

import pytest

# The daemon tests load ``vscode-daemon/daemon.py`` by path, which does not put
# its directory on ``sys.path`` the way ``python daemon.py`` does at runtime.
# Without this, the daemon cannot import its sibling ``nats_scope``.
sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "vscode-daemon"))
# The Verifier benchmark lives in ``bench/verifier`` (ADR 0015), outside the
# backend, and runs the backend's own code; its test imports ``verifier.run``.
# Appended, not prepended: nothing under ``bench/`` may shadow a backend module.
sys.path.append(str(Path(__file__).resolve().parents[2] / "bench"))

from config import settings

# The tests never call models.dev: the catalogue stays on its snapshot.
settings.MODELS_CATALOG_REFRESH_HOURS = 0
from fastapi.testclient import TestClient
from models.agent_message import AgentMessage
from models.agent_run import AgentRun, AgentRunApproval, Verification
from models.api_token import ApiToken
from models.architecture import Architecture
from models.audit import AuditLog
from models.board_note import SprintBoardNote
from models.chat import (
    ChatApprovalMessage,
    ChatConnection,
    ChatIdentity,
    ChatInteraction,
    ChatLinkCode,
)

# ---------------------------------------------------------------------------
# Import the SQLModel table models so their metadata is registered before
# SQLModel.metadata.create_all() is called.  Only the subset actually used by
# the sprint router / agent worker tests is listed here; adding more is safe.
# ---------------------------------------------------------------------------
from models.company import (
    Agent,
    BusinessUnit,
    Company,
    CompanyMember,
    ProjectCredential,
    ProjectDiscovery,
    User,
)
from models.documentation import DocumentationPack
from models.event import TaskEvent
from models.github import GitHubRepoBinding
from models.integration import SprintIntegration
from models.manager import ManagerDecision, ManagerPolicy
from models.mcp import McpServer
from models.procedure import Procedure
from models.provider import ProviderConnection
from models.review import ReviewComment, RunPublication, RunReview
from models.run_secret import RunSecret
from models.session import RefreshToken, SessionFamily
from models.specification import Specification
from models.sprint import BacklogItem, OutboxEvent, Sprint
from models.sso import SsoConnection, SsoFlowState, SsoIdentity
from models.support import BugReport
from models.task_relation import TaskRelation
from models.team import Team, TeamMember
from models.webhook import CompanyWebhook
from models.wiki import WikiLink, WikiPage, WikiPageVersion, WikiSettings
from sqlalchemy.pool import StaticPool
from sqlmodel import Session, SQLModel, create_engine

# The fake OpenID Connect provider of the SSO tests, as a shared fixture.
from sso_fake_idp import fake_idp  # noqa: F401

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


@pytest.fixture(autouse=True)
def disable_auth_for_legacy_tests(monkeypatch):
    # The legacy suite predates authentication enforcement.
    monkeypatch.setattr(settings, "AUTH_REQUIRED", False)


@pytest.fixture(autouse=True)
def release_listens_to_sprint_closed():
    """A closed sprint's integration starts from ``sprint.closed``, as in the
    backend, where ``main.py`` installs the handler; a test that closes a
    sprint without the app gets it too."""
    from services.release import integration_jobs

    integration_jobs.install()


@pytest.fixture(autouse=True)
def no_research_tools(monkeypatch):
    """The research tools (``services/research_tools.py``) reach the network;
    a test that wants them turns ``RESEARCH_TOOLS_ENABLED`` on and fakes the
    HTTP client, as ``test_research_tools.py`` does."""
    monkeypatch.setattr(settings, "RESEARCH_TOOLS_ENABLED", False)
    # The delivery-time registry lookups (``services/tools/registry_lookup.py``,
    # the Verifier's ``grounded`` check) use the same network; a test that
    # wants them turns this on and fakes the HTTP client.
    monkeypatch.setattr(settings, "GROUNDING_LOOKUPS_ENABLED", False)


@pytest.fixture(autouse=True)
def no_nats_namespace(monkeypatch):
    """Tests assert on the plain subjects, so they must not inherit a prefix.

    ``NATS_NAMESPACE`` is read at publish/consume time; a developer who exported
    it for an e2e run would otherwise see every durable-subject assertion fail
    with a namespaced subject.
    """
    monkeypatch.delenv("NATS_NAMESPACE", raising=False)


@pytest.fixture(autouse=True)
def daemon_state_in_tmp(monkeypatch, tmp_path):
    """The daemon's delivery journal (``vscode-daemon/delivery_journal.py``)
    writes under ``FABRIQUE_DAEMON_STATE``; each test gets its own, never
    the developer's home."""
    monkeypatch.setenv("FABRIQUE_DAEMON_STATE", str(tmp_path / "daemon-state"))
    # The edit tool's originals (``vscode-daemon/workspace_edit.py``) too.
    monkeypatch.setenv("FABRIQUE_EDIT_STATE", str(tmp_path / "edit-state"))
    # And the snapshots that undo a delivery (``vscode-daemon/delivery_snapshot.py``).
    monkeypatch.setenv("FABRIQUE_SNAPSHOT_STATE", str(tmp_path / "snapshot-state"))


@pytest.fixture(autouse=True)
def no_scrum_master_proxy(monkeypatch):
    """The ScrumMaster's triage of a blocked task never reaches a real proxy.

    A test that blocks a task gets the deterministic default (no decision)
    unless it patches ``scrum_master.ask`` or ``scrum_master.LLMProxyClient``.
    """
    from services.learning import scrum_master

    class _NoProxy:
        def __init__(self, base_url=None):
            self.timeout = None

        def chat(self, messages, model, role, bu_id=None):
            return {"error": "no LLM proxy in tests"}

    monkeypatch.setattr(scrum_master, "LLMProxyClient", _NoProxy)
    # The wiki's compilation at sprint close (#445) asks the same way.
    from services.learning import wiki_compile

    monkeypatch.setattr(wiki_compile, "LLMProxyClient", _NoProxy)


SQLModel.metadata.create_all(
    _engine,
    tables=[
        Company.__table__,
        User.__table__,
        CompanyMember.__table__,
        BusinessUnit.__table__,
        Agent.__table__,
        ProjectCredential.__table__,
        ProjectDiscovery.__table__,
        Sprint.__table__,
        BacklogItem.__table__,
        OutboxEvent.__table__,
        AgentRun.__table__,
        AgentRunApproval.__table__,
        Verification.__table__,
        AuditLog.__table__,
        GitHubRepoBinding.__table__,
        BugReport.__table__,
        AgentMessage.__table__,
        Procedure.__table__,
        McpServer.__table__,
        ProviderConnection.__table__,
        RunReview.__table__,
        ReviewComment.__table__,
        RunPublication.__table__,
        Specification.__table__,
        Architecture.__table__,
        ApiToken.__table__,
        TaskEvent.__table__,
        RunSecret.__table__,
        CompanyWebhook.__table__,
        SprintIntegration.__table__,
        ManagerPolicy.__table__,
        ManagerDecision.__table__,
        SsoConnection.__table__,
        SsoIdentity.__table__,
        SsoFlowState.__table__,
        DocumentationPack.__table__,
        SessionFamily.__table__,
        RefreshToken.__table__,
        ChatConnection.__table__,
        ChatIdentity.__table__,
        ChatLinkCode.__table__,
        ChatApprovalMessage.__table__,
        ChatInteraction.__table__,
        SprintBoardNote.__table__,
        TaskRelation.__table__,
        Team.__table__,
        TeamMember.__table__,
        WikiPage.__table__,
        WikiPageVersion.__table__,
        WikiLink.__table__,
        WikiSettings.__table__,
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

    async def enable_jetstream(self):
        return False

    async def publish(self, subject, payload, msg_id=None):
        pass

    async def subscribe(self, subject, callback):
        pass

    async def consume(self, subject, durable, callback, **kwargs):
        pass

    @property
    def is_connected(self):
        return False


@pytest.fixture()
def test_client(sqlite_session):
    """
    FastAPI TestClient with get_session overridden to the in-memory SQLite session.

    Startup hooks that touch Postgres (prepare_database), NATS, and Neo4j
    are all patched out so no external services are required.
    """
    from database import get_session

    from app.main import app

    def override_get_session():
        yield sqlite_session

    app.dependency_overrides[get_session] = override_get_session

    fake_nats = _FakeNATSClient()

    with (
        # Lifecycle dependencies are imported into the lifecycle module namespace.
        mock.patch("lifecycle.prepare_database", return_value=None),
        mock.patch(
            "lifecycle.get_neo4j_driver", side_effect=Exception("neo4j stubbed")
        ),
        mock.patch("lifecycle.NATSClient", return_value=fake_nats),
        TestClient(app, raise_server_exceptions=False) as client,
    ):
        # Ensure app.state has a nats_client so router code that reads it won't crash
        if not hasattr(app.state, "nats_client") or app.state.nats_client is None:
            app.state.nats_client = fake_nats
        yield client

    app.dependency_overrides.clear()
