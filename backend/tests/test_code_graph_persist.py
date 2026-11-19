"""The stored code graph never holds a task (issue #438, PR #681's e2e failure).

On a project the size of this repository (step 9d of the end-to-end run,
"the factory builds itself"), the first sync of a folder parses and stores
every file: on the CI runner the first task waited 2 min 17 s for it before
its model call, and each rework, in its own worktree, waited a minute more
for a full sync of a folder nobody reads again, until the sprint missed the
harness's wait. These tests hold that case down:

- WHEN the task worker builds a ``## Related code`` section THEN the sync
  runs off the task's path, and the section comes from the daemon on demand
  while the stored graph is not ready.
- WHILE a folder's sync is part way through (pages stored, more to come),
  its half-written graph is never read, and a second sync of the same
  folder does not start.
- WHEN a task works in its own worktree THEN only the company's project
  folder is stored; the worktree's section is computed on demand.
- WHEN a sync runs past ``SYNC_BUDGET_SECONDS`` THEN it stops between pages,
  incomplete, and the next sync goes on from what it stored.
"""

from __future__ import annotations

import threading
import time
from types import SimpleNamespace

import code_graph as daemon_graph  # the daemon's module, on sys.path via conftest
import pytest
import worker_stages
from config import settings
from models.company import Company
from models.sprint import BacklogItem, Sprint
from services.loop.worker import related
from services.tools import code_graph, code_graph_store, developer_tools


@pytest.fixture(autouse=True)
def map_state(tmp_path, monkeypatch):
    monkeypatch.setenv("FABRIQUE_MAP_STATE", str(tmp_path / "map-state"))
    monkeypatch.setattr(settings, "CODE_GRAPH_STORE", "postgres")
    yield
    assert code_graph.wait_for_persists(10.0)


@pytest.fixture()
def project(tmp_path):
    root = tmp_path / "proj"
    (root / "pkg").mkdir(parents=True)
    (root / "pkg" / "db.py").write_text("def connect_database(url):\n    return url\n")
    (root / "pkg" / "store.py").write_text(
        "from pkg.db import connect_database\n\n\n"
        "class Store:\n"
        "    def get_item(self, key):\n"
        "        return connect_database(key)\n"
    )
    (root / "pkg" / "api.py").write_text(
        "from pkg.store import Store\n\n\n"
        "def handle_request(key):\n    return Store().get_item(key)\n"
    )
    return root


class _SlowDaemon:
    """The real daemon's handlers, with ``workspace.graph`` held from its
    *hold_from*-th page until ``release`` is set: a sync of a large project,
    as slow as it likes."""

    def __init__(self, hold_from=0):
        self.release = threading.Event()
        self.graph_held = threading.Event()
        self.hold_from, self.pages = hold_from, 0
        self.calls: list[tuple[str, str]] = []
        self.lock = threading.Lock()

    def __call__(self, subject, payload, timeout):
        with self.lock:
            self.calls.append((subject, payload["workspace_root"]))
        if subject == "workspace.graph":
            with self.lock:
                page, self.pages = self.pages, self.pages + 1
            if page >= self.hold_from and not self.release.is_set():
                self.graph_held.set()
                assert self.release.wait(10.0), "the test never released the sync"
            return daemon_graph.graph_report(payload["workspace_root"], payload)
        return daemon_graph.related(payload["workspace_root"], payload)

    def subjects(self):
        with self.lock:
            return [subject for subject, _root in self.calls]


def _company(session):
    company = Company(company_name="Graph Co", autonomy_level=2)
    session.add(company)
    session.commit()
    session.refresh(company)
    return company


def _task(session, company):
    sprint = Sprint(
        company_id=company.id, sprint_name="S", scope_size="M", status="active"
    )
    session.add(sprint)
    session.commit()
    session.refresh(sprint)
    item = BacklogItem(sprint_id=sprint.id, title="T", description="d", status="todo")
    session.add(item)
    session.commit()
    session.refresh(item)
    return item


def _section(item, company, engine):
    return related.related_code_section(
        {"id": item.id}, {"role": "Developer"}, company.id, engine=engine
    )


@pytest.fixture()
def bound(project, monkeypatch, sqlite_session):
    """The task worker's bridge to a slow daemon, the task seeded with a file."""
    engine = sqlite_session.get_bind()
    worker_stages.use_engine(monkeypatch, engine)
    daemon = _SlowDaemon()
    developer_tools.bind(object(), object(), lambda company_id: str(project))
    monkeypatch.setattr(developer_tools, "_request_from_thread", daemon)
    monkeypatch.setattr(
        related, "seeds_for", lambda session, company_id, item_id: ["pkg/store.py"]
    )
    yield daemon, engine
    daemon.release.set()
    developer_tools.unbind()


def test_a_slow_sync_never_holds_the_task(bound, project, sqlite_session):
    daemon, engine = bound
    company = _company(sqlite_session)
    item = _task(sqlite_session, company)
    started = time.monotonic()
    text = _section(item, company, engine)
    assert time.monotonic() - started < 5.0
    # The sync is under way, held; the section came from the daemon on demand.
    assert daemon.graph_held.wait(5.0)
    assert "workspace.related" in daemon.subjects()
    assert "- pkg/api.py: Store, get_item" in text
    assert "as the daemon last reported it" not in text
    daemon.release.set()
    assert code_graph.wait_for_persists(10.0)
    store = code_graph_store.SqlStore(engine)
    assert set(store.known(company.id, str(project))) == {
        "pkg/api.py",
        "pkg/db.py",
        "pkg/store.py",
    }
    # Once stored, the next task's section reads the store.
    before = len(daemon.subjects())
    stored = _section(item, company, engine)
    assert code_graph.wait_for_persists(10.0)
    assert "as the daemon last reported it" in stored
    assert "workspace.related" not in daemon.subjects()[before:]


def test_one_sync_per_folder_and_its_partial_graph_is_not_read(
    bound, project, sqlite_session, monkeypatch
):
    _daemon, engine = bound
    daemon = _SlowDaemon(hold_from=1)
    monkeypatch.setattr(code_graph_store, "PAGE_FILES", 1)
    company = _company(sqlite_session)
    root = str(project)
    # The first page is stored, the daemon says more, the second is held.
    first = code_graph.persist_soon(daemon, engine, company.id, root)
    assert first is not None and daemon.graph_held.wait(5.0)
    store = code_graph_store.SqlStore(engine)
    assert len(store.known(company.id, root)) == 1
    assert code_graph.half_written(company.id, root)
    # A second sync of the same folder does not start,
    assert code_graph.persist_soon(daemon, engine, company.id, root) is None
    # and the section asks the daemon instead of reading the half-written graph.
    before = len(daemon.subjects())
    text = code_graph.related_section(
        daemon, root, ["pkg/store.py"], company_id=company.id, engine=engine
    )
    assert daemon.subjects()[before:] == ["workspace.related"]
    assert "as the daemon last reported it" not in text
    daemon.release.set()
    assert code_graph.wait_for_persists(10.0)
    assert not code_graph.half_written(company.id, root)
    assert len(store.known(company.id, root)) == 3
    assert daemon.subjects().count("workspace.graph") == 3
    # Whole, the stored graph is read again.
    stored = code_graph.related_section(
        daemon, root, ["pkg/store.py"], company_id=company.id, engine=engine
    )
    assert "as the daemon last reported it" in stored


def test_a_tasks_worktree_is_not_stored(
    bound, project, tmp_path, sqlite_session, monkeypatch
):
    daemon, engine = bound
    daemon.release.set()
    company = _company(sqlite_session)
    item = _task(sqlite_session, company)
    worktree = tmp_path / "worktree"
    worktree.mkdir()
    for path in project.rglob("*.py"):
        target = worktree / path.relative_to(project)
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(path.read_text())
    real_root = developer_tools.tool_root

    def tool_root(company_id, item_id=None, *, engine=None):
        return str(worktree) if item_id is not None else real_root(company_id)

    monkeypatch.setattr(developer_tools, "tool_root", tool_root)
    text = _section(item, company, engine)
    assert code_graph.wait_for_persists(10.0)
    graph_roots = {
        root for subject, root in daemon.calls if subject == "workspace.graph"
    }
    assert graph_roots == {str(project)}
    store = code_graph_store.SqlStore(engine)
    assert store.known(company.id, str(worktree)) == {}
    assert len(store.known(company.id, str(project))) == 3
    # The worktree's section is the daemon's walk of the worktree.
    assert ("workspace.related", str(worktree)) in daemon.calls
    assert "- pkg/api.py: Store, get_item" in text


def test_a_sync_stops_at_its_time_budget(project, sqlite_session, monkeypatch):
    company = _company(sqlite_session)
    store = code_graph_store.SqlStore(sqlite_session.get_bind())
    monkeypatch.setattr(code_graph_store, "PAGE_FILES", 1)
    monkeypatch.setattr(code_graph_store, "SYNC_BUDGET_SECONDS", 0.2)
    pages: list[int] = []
    # A clock the daemon call advances: a loaded runner storing a page slowly
    # must not change how many pages fit in the budget.
    clock = [0.0]
    monkeypatch.setattr(
        code_graph_store, "time", SimpleNamespace(monotonic=lambda: clock[0])
    )

    def slow(subject, payload, timeout):
        pages.append(len(payload["known"]))
        clock[0] += 0.15
        return daemon_graph.graph_report(payload["workspace_root"], payload)

    done = code_graph_store.sync(store, slow, company.id, str(project))
    assert done["complete"] is False and done["pages"] == 2
    assert len(store.known(company.id, str(project))) == 2
    # The next sync goes on from what was stored, not from the start.
    monkeypatch.setattr(code_graph_store, "SYNC_BUDGET_SECONDS", 30.0)
    done = code_graph_store.sync(store, slow, company.id, str(project))
    assert done["complete"] is True and pages[2] == 2
    assert len(store.known(company.id, str(project))) == 3
