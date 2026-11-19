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

import importlib.abc
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


@pytest.hookimpl(tryfirst=True)
def pytest_cmdline_main(config):
    """Run in parallel by default: ``-n auto --dist loadgroup`` when
    pytest-xdist is installed and the command did not choose ``-n`` itself.

    A hook rather than ``addopts``: ``addopts = "-n auto"`` would make every
    run that turns the plugin off (``-p no:xdist``) or lacks it fail with
    "unrecognized arguments: -n". ``-n 0`` runs in one process (for ``--pdb``,
    which xdist refuses, the default is not applied either). It runs before
    xdist's own hook, which turns ``auto`` into the core count. Called for
    this conftest when the run names ``tests`` or a path inside it.
    """
    if not config.pluginmanager.hasplugin("xdist"):
        return
    # A worker calls this hook too, with ``-n`` cleared: without this guard
    # every worker would start workers of its own, without end.
    if hasattr(config, "workerinput"):
        return
    if config.option.numprocesses is not None or config.option.usepdb:
        return
    config.option.numprocesses = "auto"
    if config.option.dist == "no":
        # loadgroup: tests marked ``xdist_group`` share one worker.
        config.option.dist = "loadgroup"


def pytest_configure(config):
    # xdist registers the mark itself; this keeps a run without it
    # (``-p no:xdist``) free of "unknown mark" warnings.
    config.addinivalue_line(
        "markers", "xdist_group(name): run on the same xdist worker as the group"
    )


from config import settings

# The tests never call models.dev: the catalogue stays on its snapshot.
settings.MODELS_CATALOG_REFRESH_HOURS = 0
import shared_sqlite
from fastapi.testclient import TestClient
from models.agent_message import AgentMessage
from models.agent_run import AgentRun, AgentRunApproval, Verification
from models.api_token import ApiToken
from models.architecture import Architecture
from models.audit import AuditLog
from models.audit_anchor import AuditAnchor, AuditAnchorSettings
from models.board_note import SprintBoardNote
from models.chat import (
    ChatApprovalMessage,
    ChatConnection,
    ChatIdentity,
    ChatInteraction,
    ChatLinkCode,
)
from models.code_graph import (
    CodeGraphDefinition,
    CodeGraphEdge,
    CodeGraphFile,
    CodeGraphReference,
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
from models.finding import Finding
from models.github import GitHubRepoBinding
from models.intake import IntakeItem
from models.integration import SprintIntegration
from models.integration_review import IntegrationReview, IntegrationReviewComment
from models.invitation import CompanyInvitation
from models.manager import ManagerDecision, ManagerPolicy
from models.mcp import McpServer
from models.person_statement import PersonStatement
from models.procedure import Procedure
from models.progress import SprintProgressSnapshot
from models.provider import ProviderConnection
from models.review import ReviewComment, RunPublication, RunReview
from models.rsi_cycle import RsiCycle
from models.run_secret import RunSecret
from models.saved_view import SavedView, SavedViewPreference
from models.security_review import WstgReview
from models.session import RefreshToken, SessionFamily
from models.sod import AccessReview, SodPolicy
from models.specification import Specification
from models.sprint import BacklogItem, OutboxEvent, Sprint
from models.sso import (
    SsoConnection,
    SsoDomain,
    SsoDomainCheck,
    SsoFlowState,
    SsoIdentity,
)
from models.support import BugReport
from models.task_relation import TaskRelation
from models.team import Team, TeamMember
from models.webhook import CompanyWebhook
from models.wiki import WikiLink, WikiPage, WikiPageVersion, WikiSettings
from sqlalchemy.pool import StaticPool
from sqlmodel import Session, SQLModel, create_engine

# ---------------------------------------------------------------------------
# In-memory SQLite engine — shared across all tests within a session via
# StaticPool so every Session sees the same in-memory database. An in-memory
# database belongs to its process, so under pytest-xdist each worker has its
# own and no worker sees another's rows. The one connection's transaction
# belongs to the checkout that opened it (``shared_sqlite``): the app writes
# from ``asyncio.to_thread`` while the loop goes on with sessions of its own.
# ---------------------------------------------------------------------------
_SQLITE_URL = "sqlite://"

_engine = create_engine(
    _SQLITE_URL,
    connect_args=shared_sqlite.connect_args(),
    poolclass=StaticPool,
)
shared_sqlite.install(_engine)


# ---------------------------------------------------------------------------
# A fixture acts on a module only once the test has loaded it (``when_loaded``):
# a test loads only what it uses, so a change at a module's top level selects
# only the tests that load it (``scripts/affected_tests.py --by module``).
# ---------------------------------------------------------------------------


class _AfterImport(importlib.abc.MetaPathFinder):
    """Runs a test's pending actions on a module right after its first import."""

    def __init__(self) -> None:
        self.pending: dict[str, list] = {}

    def find_spec(self, name, path=None, target=None):
        if name not in self.pending:
            return None
        for finder in sys.meta_path:
            if finder is not self and hasattr(finder, "find_spec"):
                spec = finder.find_spec(name, path, target)
                if spec is not None:
                    break
        else:
            return None
        if spec.loader is None:
            return spec
        exec_module = spec.loader.exec_module

        def exec_then_act(module):
            exec_module(module)
            for act in self.pending.pop(name, []):
                act(module)

        spec.loader.exec_module = exec_then_act
        return spec


_after_import = _AfterImport()
sys.meta_path.insert(0, _after_import)


def when_loaded(name, act):
    """``act(module)`` now if the module *name* is loaded, else right after a
    later import of it during this test. ``scripts/affected_graph.py`` draws
    no edge from a fixture to the module it names here: a test reaches that
    module through its own imports, or the fixture does nothing."""
    for loaded in (name, "app." + name):
        if loaded in sys.modules:
            act(sys.modules[loaded])
    if name not in sys.modules:
        _after_import.pending.setdefault(name, []).append(act)


# What the process keeps by a row's id (or another key a later test reuses),
# emptied after every test: the tables ``sqlite_session`` empties, and the
# in-memory database of a test's own engine, hand out their ids again, so a
# later test's task 1 is not this test's. A module never loaded keeps nothing.
# ``test_process_state.py`` fails on a module-level container of ``services``
# that is neither here nor in its list of safe ones.
PROCESS_STATE = {
    "services.loop.worker.intake.LAST_AUTONOMOUS_QUEUE_AT": "by task id",
    "services.management.manager_observe._last_credit_probe": "by company id",
    "services.management.manager_observe._bridge": "a test's NATS, loop, requester",
    # A background integration that an earlier test's event loop dropped
    # mid-attempt never released its lock, so a later test's sprint 1 found
    # it held and was never integrated.
    "services.platform.locks._local_held": "by sprint or task id",
    "services.platform.login_throttle._records": "sign-in failures by account",
    "services.platform.oidc._discovery_cache": "by issuer, reused by each fake IdP",
    "services.platform.oidc._jwks_cache": "by JWKS URL, holding another test's keys",
    "services.models.routing_policy._warned": "warned once: a later test sees none",
    "services.release.integration._LOCKS": "by sprint id",
    "services.release.integration_jobs._RUNNING": "integrations, each of a sprint id",
    "services.tools.developer_tools._bridge": "a test's NATS and closed event loop",
    "services.workspace.daemon_liveness._daemons": "heartbeats by daemon id",
    "services.workspace.workspace_snapshot._tree_cache": "trees by workspace path",
    "routers.agents.sse_connections": "Glassroom subscribers by sprint id",
    "routers.agents.recent_sse_events": "Glassroom events by sprint id",
}


@pytest.fixture(autouse=True)
def forget_process_state():
    """After each test, ``PROCESS_STATE`` emptied and the pending actions of
    ``when_loaded`` dropped. The first autouse fixture: its teardown runs last."""
    yield
    _after_import.pending.clear()
    empty_process_state()


def empty_process_state():
    """Empty each container of ``PROCESS_STATE`` in the modules loaded."""
    for dotted in PROCESS_STATE:
        name, attr = dotted.rsplit(".", 1)
        for loaded in (name, "app." + name):
            # A module still importing may not define it yet (nothing kept);
            # test_every_listed_container_exists catches a misspelt name.
            container = getattr(sys.modules.get(loaded), attr, None)
            if container is not None:
                container.clear()  # atomic under the GIL


def _listen_for_closed_sprints(hooks):
    """Install ``integration_jobs`` if it is loaded, else a stand-in that
    loads and installs it when a sprint first closes."""
    jobs = sys.modules.get("services.release.integration_jobs")
    # A test whose first import is integration_jobs loads hooks from inside
    # it: the module is then in sys.modules without install() yet, and counts
    # as not loaded (the stand-in installs it at the first close).
    if jobs is not None and hasattr(jobs, "install"):
        hooks.off(hooks.SPRINT_CLOSED, _integrate_after_close)
        jobs.install()
    else:
        hooks.on(hooks.SPRINT_CLOSED, _integrate_after_close)


def _integrate_after_close(**payload):
    """Stands in for ``integration_jobs.on_sprint_closed`` until a sprint closes."""
    from services.platform import hooks
    from services.release import integration_jobs

    hooks.off(hooks.SPRINT_CLOSED, _integrate_after_close)
    if integration_jobs.on_sprint_closed not in hooks.handlers(hooks.SPRINT_CLOSED):
        # Not installed meanwhile (``main.py`` installs it when the app is built).
        integration_jobs.install()
        integration_jobs.on_sprint_closed(**payload)


@pytest.fixture(autouse=True)
def disable_auth_for_legacy_tests(monkeypatch):
    # The legacy suite predates authentication enforcement.
    monkeypatch.setattr(settings, "AUTH_REQUIRED", False)


@pytest.fixture(autouse=True)
def sign_in_limits_out_of_the_way(monkeypatch):
    """The suite signs in and registers far more often than a person would,
    all from the test client's one address: the limits of the password
    routes (issue #541) are lifted, and their counters start empty (the
    per-account backoff is ``PROCESS_STATE``). ``test_auth_limits.py``
    lowers them to test them."""
    monkeypatch.setattr(settings, "LOGIN_RATE_LIMIT", "100000/minute")
    monkeypatch.setattr(settings, "REGISTER_RATE_LIMIT", "100000/minute")
    monkeypatch.setattr(settings, "LOGIN_FAILURES_BEFORE_BACKOFF", 100000)
    when_loaded("security", lambda security: security.limiter.reset())


@pytest.fixture(autouse=True)
def release_listens_to_sprint_closed():
    """A closed sprint's integration starts from ``sprint.closed``, as in the
    backend, where ``main.py`` installs the handler; a test that closes a
    sprint without the app gets it too. Only a test that loads
    ``platform.hooks`` can emit the event, and release is loaded only when a
    sprint closes (``_listen_for_closed_sprints``)."""
    when_loaded("services.platform.hooks", _listen_for_closed_sprints)


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

    class _NoProxy:
        def __init__(self, base_url=None):
            self.timeout = None

        def chat(self, messages, model, role, bu_id=None):
            return {"error": "no LLM proxy in tests"}

    def no_proxy(module):
        monkeypatch.setattr(module, "LLMProxyClient", _NoProxy)

    when_loaded("services.learning.scrum_master", no_proxy)
    # The wiki's compilation at sprint close (#445) asks the same way.
    when_loaded("services.learning.wiki_compile", no_proxy)


SQLModel.metadata.create_all(
    _engine,
    tables=[
        Company.__table__,
        User.__table__,
        CompanyMember.__table__,
        CompanyInvitation.__table__,
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
        AuditAnchor.__table__,
        AuditAnchorSettings.__table__,
        GitHubRepoBinding.__table__,
        BugReport.__table__,
        AgentMessage.__table__,
        Procedure.__table__,
        Finding.__table__,
        RsiCycle.__table__,
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
        SsoDomain.__table__,
        SsoDomainCheck.__table__,
        DocumentationPack.__table__,
        SessionFamily.__table__,
        RefreshToken.__table__,
        ChatConnection.__table__,
        ChatIdentity.__table__,
        ChatLinkCode.__table__,
        ChatApprovalMessage.__table__,
        ChatInteraction.__table__,
        SprintBoardNote.__table__,
        CodeGraphFile.__table__,
        CodeGraphDefinition.__table__,
        CodeGraphReference.__table__,
        CodeGraphEdge.__table__,
        SavedView.__table__,
        SavedViewPreference.__table__,
        SprintProgressSnapshot.__table__,
        TaskRelation.__table__,
        Team.__table__,
        TeamMember.__table__,
        SodPolicy.__table__,
        AccessReview.__table__,
        IntegrationReview.__table__,
        IntegrationReviewComment.__table__,
        WikiPage.__table__,
        WikiPageVersion.__table__,
        WikiLink.__table__,
        WikiSettings.__table__,
        IntakeItem.__table__,
        WstgReview.__table__,
        PersonStatement.__table__,
    ],
)


@pytest.fixture()
def sqlite_session():
    """Yield a SQLModel Session backed by the shared in-memory SQLite engine."""
    with Session(_engine) as session:
        yield session
        session.rollback()  # isolate each test
    _empty_shared_database()


def _empty_shared_database():
    """Delete every row a test committed to the shared database.

    A rollback undoes only what was never committed, and most tests commit.
    Left in place, the rows of every earlier test were read again by each
    later worker cycle that scans all the tasks of all sprints (the review
    cycle reviewed other tests' tasks): one such test took 49 s at the end of
    a serial run and under a second alone, and which test paid depended on
    the order, so on how xdist spread the tests over its workers.
    """
    # Every table in the database, not only the ones created above: some
    # tests create the tables they need (``AssistantRuntimeState``...) on the shared engine.
    with _engine.begin() as connection:
        names = connection.exec_driver_sql(
            "SELECT name FROM sqlite_master"
            " WHERE type = 'table' AND name NOT LIKE 'sqlite_%'"
        ).scalars()
        for name in list(names):
            connection.exec_driver_sql(f'DELETE FROM "{name}"')
    # What the process keeps by a row's id goes too: ``forget_process_state``.


@pytest.fixture()
def fake_idp(monkeypatch):
    """The fake OpenID Connect provider of the SSO tests (``sso_fake_idp.py``)."""
    from sso_fake_idp import serve_fake_idp

    yield from serve_fake_idp(monkeypatch)


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
