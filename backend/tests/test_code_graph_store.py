"""The stored code knowledge graph (issue #438, second slice).

The daemon's ``workspace.graph`` (``vscode-daemon/code_graph.py``) runs for
real against ``tmp_path``; the backend's store
(``services/tools/code_graph_store.py``, the Postgres tables on SQLite here)
keeps what it reports, keyed by each file's sha256; the ``## Related code``
section (``services/tools/code_graph.py``) reads the stored graph when there
is one and asks the daemon on demand when there is none. The Neo4j store is
exercised through a fake driver.
"""

from __future__ import annotations

import asyncio
import json

import code_graph as daemon_graph  # the daemon's module, on sys.path via conftest
import pytest
import repo_map as daemon_map
import worker_stages
from config import settings
from models.agent_run import TASK_WORKER_PRODUCER, AgentRun, Verification
from models.code_graph import (
    EDGE_SOURCES,
    CodeGraphDefinition,
    CodeGraphEdge,
    CodeGraphFile,
)
from models.company import Company
from services.loop.worker import related
from services.tools import code_graph, code_graph_neo4j, code_graph_store
from sqlmodel import select
from workspace_read import ReadError

MARKER = "contents-that-must-never-leave-the-machine"


@pytest.fixture(autouse=True)
def map_state(tmp_path, monkeypatch):
    monkeypatch.setenv("FABRIQUE_MAP_STATE", str(tmp_path / "map-state"))
    monkeypatch.setattr(settings, "CODE_GRAPH_STORE", "postgres")


@pytest.fixture()
def project(tmp_path):
    root = tmp_path / "proj"
    (root / "pkg").mkdir(parents=True)
    (root / "pkg" / "db.py").write_text(
        f"# {MARKER}\ndef connect_database(url):\n    return url\n"
    )
    (root / "pkg" / "store.py").write_text(
        "from pkg.db import connect_database\n\n\n"
        "class Store:\n"
        "    def get_item(self, key):\n"
        f"        return connect_database('{MARKER}')\n"
    )
    (root / "pkg" / "api.py").write_text(
        "from pkg.store import Store\n\n\n"
        "def handle_request(key):\n    return Store().get_item(key)\n"
    )
    (root / "pkg" / "secrets.py").write_text("from pkg.store import Store\n")
    return root


def _report(root, known=None, **extra):
    return daemon_graph.graph_report(str(root), {"known": known or {}, **extra})


def _company(session, name="Graph Co"):
    company = Company(company_name=name, autonomy_level=2)
    session.add(company)
    session.commit()
    session.refresh(company)
    return company


class _Daemon:
    """A requester that runs the real daemon's handlers; *serves* names the
    subjects this daemon answers (an older one lacks ``workspace.graph``)."""

    def __init__(self, serves=("workspace.graph", "workspace.related")):
        self.serves, self.calls = set(serves), []

    def __call__(self, subject, payload, timeout):
        self.calls.append((subject, payload))
        if subject not in self.serves:
            raise RuntimeError("no responders")
        if subject == "workspace.graph":
            return daemon_graph.graph_report(payload["workspace_root"], payload)
        return daemon_graph.related(payload["workspace_root"], payload)


# --- the daemon: workspace.graph -----------------------------------------------


def test_the_daemon_reports_each_files_definitions_and_references(project):
    reply = _report(project)
    files = {f["path"]: f for f in reply["files"]}
    # A file that may hold secrets is never read, nor named.
    assert sorted(files) == ["pkg/api.py", "pkg/db.py", "pkg/store.py"]
    store = files["pkg/store.py"]
    assert len(store["sha256"]) == 64 and store["status"] == "parsed"
    assert ["Store", 4, 0] in store["defs"] and ["get_item", 5, 1] in store["defs"]
    assert ["connect_database", 2] in store["refs"]
    assert reply["removed"] == [] and reply["more"] is False
    assert reply["unchanged"] == 0 and reply["partial"] is False
    # Names, lines and hashes only, never the contents.
    assert MARKER not in json.dumps(reply)


def test_an_unchanged_file_is_neither_reported_nor_parsed(project, monkeypatch):
    first = _report(project)
    known = {f["path"]: f["sha256"] for f in first["files"]}
    parsed = []
    real = daemon_map.parse_file
    monkeypatch.setattr(
        daemon_map, "parse_file", lambda r, t: parsed.append(r) or real(r, t)
    )
    (project / "pkg" / "db.py").write_text("def connect_database(url, retries):\n")
    (project / "pkg" / "api.py").unlink()
    again = _report(project, known)
    assert [f["path"] for f in again["files"]] == ["pkg/db.py"]
    assert again["unchanged"] == 1 and again["removed"] == ["pkg/api.py"]
    assert parsed == ["pkg/db.py"]


def test_the_report_comes_in_pages(project):
    page = _report(project, max_files=1)
    assert len(page["files"]) == 1 and page["more"] is True
    known = {f["path"]: f["sha256"] for f in page["files"]}
    rest = _report(project, known)
    assert len(rest["files"]) == 2 and rest["more"] is False


def test_a_report_request_over_its_bounds_is_refused(project):
    for bad in (
        {"known": []},
        {"known": {"a.py": 3}},
        {
            "known": {
                f"f{i}.py": "0" * 64 for i in range(daemon_graph.REPORT_MAX_KNOWN + 1)
            }
        },
        {"known": {}, "max_files": "many"},
    ):
        with pytest.raises(ReadError) as refused:
            daemon_graph.graph_report(str(project), bad)
        assert refused.value.status == 400
    assert ("workspace.graph", daemon_graph.handle_graph) in daemon_graph.HANDLERS


def test_the_daemon_answers_workspace_graph_over_nats(project):
    import importlib.util
    from pathlib import Path

    path = Path(__file__).resolve().parents[2] / "vscode-daemon" / "daemon.py"
    spec = importlib.util.spec_from_file_location("fabrique_daemon_store", path)
    daemon = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(daemon)
    replies = []

    class _Msg:
        def __init__(self, body):
            self.data, self.reply = json.dumps(body).encode(), "inbox"

    async def reply(nc, msg, body):
        replies.append(body)

    async def run():
        for body in ({"workspace_root": str(project), "known": {}}, {"known": {}}):
            await daemon_graph.handle_graph(
                None,
                _Msg(body),
                reply,
                daemon._request_body,
                daemon._resolve_target_path,
            )

    asyncio.run(run())
    assert len(replies[0]["files"]) == 3 and replies[1]["error"]["status"] == 400


# --- the store: keyed by sha256 --------------------------------------------------


def _store(session):
    return code_graph_store.SqlStore(session.get_bind())


def _defs(session):
    rows = session.exec(
        select(CodeGraphDefinition, CodeGraphFile).where(
            CodeGraphDefinition.file_id == CodeGraphFile.id
        )
    ).all()
    return {(f.path, d.name): d.id for d, f in rows}


def test_a_sync_stores_the_graph_and_rewrites_only_changed_files(
    project, sqlite_session
):
    company = _company(sqlite_session)
    store, daemon, root = _store(sqlite_session), _Daemon(), str(project)
    done = code_graph_store.sync(store, daemon, company.id, root)
    assert done == {"pages": 1, "replaced": 3, "removed": 0, "complete": True}
    before = _defs(sqlite_session)
    assert ("pkg/store.py", "get_item") in before
    # Nothing changed: the daemon is asked with every known hash and nothing is written.
    again = code_graph_store.sync(store, daemon, company.id, root)
    assert again["replaced"] == 0 and len(daemon.calls[-1][1]["known"]) == 3
    assert _defs(sqlite_session) == before
    # One file changes, one goes: only their rows move.
    (project / "pkg" / "db.py").write_text("def open_database(url):\n    return url\n")
    (project / "pkg" / "api.py").unlink()
    changed = code_graph_store.sync(store, daemon, company.id, root)
    assert changed["replaced"] == 1 and changed["removed"] == 1
    after = _defs(sqlite_session)
    assert after[("pkg/store.py", "get_item")] == before[("pkg/store.py", "get_item")]
    assert ("pkg/db.py", "connect_database") not in after
    assert ("pkg/db.py", "open_database") in after
    assert not any(path == "pkg/api.py" for path, _ in after)
    # Another company's graph of the same folder is its own.
    other = _company(sqlite_session, "Other Co")
    assert store.known(other.id, root) == {}


def test_the_store_keeps_only_what_the_daemon_observed(project, sqlite_session):
    company = _company(sqlite_session)
    store = _store(sqlite_session)
    good = {"path": "a.py", "sha256": "a" * 64, "defs": [["f", 1, 0]], "refs": []}
    store.apply(
        company.id,
        str(project),
        code_graph_store.clean_files(
            [
                good,
                {**good, "path": "../out.py"},
                {**good, "path": "/etc/x.py"},
                {**good, "path": "b.py", "sha256": "not-a-hash"},
                {**good, "path": "c.py", "defs": [["f", "x", 0]], "refs": [[3, 1]]},
                "not a file",
            ]
        ),
        [],
    )
    known = store.known(company.id, str(project))
    assert known == {"a.py": "a" * 64, "c.py": "a" * 64}
    # A malformed definition or reference is dropped, the file kept.
    assert _defs(sqlite_session).keys() == {("a.py", "f")}


def test_the_stored_graph_gives_the_same_edges_as_the_daemons_walk(
    project, sqlite_session
):
    company = _company(sqlite_session)
    store, root = _store(sqlite_session), str(project)
    code_graph_store.sync(store, _Daemon(), company.id, root)
    stored = code_graph_store.related(store, company.id, root, ["pkg/store.py"], 1, 30)
    live = daemon_graph.related(root, {"seeds": ["pkg/store.py"], "hops": 1})

    def edges(reply):
        return {
            f["path"]: (
                {e["path"]: e["names"] for e in f["uses"]},
                {e["path"]: e["names"] for e in f["used_by"]},
                f["hop"],
                f["sha256"],
            )
            for f in reply["files"]
        }

    assert edges(stored) == edges(live)
    assert stored["seeds_matched"] == ["pkg/store.py"] and stored["stored"] is True
    assert (
        code_graph_store.related(store, company.id, root, ["nothing.py"], 1, 30) is None
    )
    assert (
        code_graph_store.related(store, company.id, "/elsewhere", ["pkg"], 1, 30)
        is None
    )


def test_a_name_defined_in_too_many_files_gives_no_stored_edge(sqlite_session):
    company = _company(sqlite_session)
    store = _store(sqlite_session)
    many = daemon_map.MAP_MAX_DEFINERS + 1
    files = [
        {
            "path": f"m{i}.py",
            "sha256": f"{i:064x}",
            "defs": [["run_it", 1, 0]],
            "refs": [],
        }
        for i in range(many)
    ] + [{"path": "user.py", "sha256": "f" * 64, "defs": [], "refs": [["run_it", 2]]}]
    store.apply(company.id, "/p", code_graph_store.clean_files(files), [])
    reply = code_graph_store.related(store, company.id, "/p", ["user.py"], 1, 30)
    assert [f["path"] for f in reply["files"]] == ["user.py"]
    assert code_graph_store.MAX_DEFINERS == daemon_map.MAP_MAX_DEFINERS


# --- the edges from records ------------------------------------------------------


def _verified_run(session, company_id, path, sha):
    run = AgentRun(
        company_id=company_id,
        external_ref="task-7",
        version=1,
        producer=TASK_WORKER_PRODUCER,
        subject={"backlog_item_id": 7, "title": "Store"},
        artifact_digest="0" * 64,
        artifact_size=0,
    )
    session.add(run)
    session.commit()
    session.refresh(run)
    writes = [{"path": path, "status": "written", "sha256": sha}]
    session.add(
        Verification(
            company_id=company_id,
            agent_run_id=run.id,
            verdict="verified",
            evidence={"workspace_writes": writes},
        )
    )
    session.commit()
    return run


ARCH = {
    "modules": [
        {
            "id": "MOD-1",
            "name": "Store",
            "path": "pkg/store.py",
            "covers": ["REQ-1"],
            "depends_on": ["Db"],
        },
        {"id": "MOD-2", "name": "Db", "path": "pkg/db.py", "covers": ["REQ-2"]},
    ]
}


def test_the_architecture_and_the_verifications_give_edges_with_their_source(
    project, sqlite_session
):
    company = _company(sqlite_session)
    store, root = _store(sqlite_session), str(project)
    run = _verified_run(sqlite_session, company.id, "pkg/store.py", "b" * 64)
    code_graph_store.record_edges(
        store, sqlite_session.get_bind(), company.id, root, ARCH, architecture_id=3
    )
    rows = sqlite_session.exec(select(CodeGraphEdge)).all()
    got = {(r.kind, r.from_ref, r.to_ref, r.source) for r in rows}
    assert ("implements", "module:MOD-1", "requirement:REQ-1", "architecture") in got
    assert ("implements", "module:MOD-1", "file:pkg/store.py", "architecture") in got
    assert ("depends_on", "module:MOD-1", "module:MOD-2", "architecture") in got
    assert ("verified", "file:pkg/store.py", f"run:{run.id}", "verification") in got
    verified = next(r for r in rows if r.kind == "verified")
    # The sha256 the daemon reported in that run, copied from the record.
    assert verified.detail["sha256"] == "b" * 64
    assert verified.source_ref.startswith("verification:")
    assert {r.source_ref for r in rows if r.source == "architecture"} == {
        "architecture:3"
    }
    assert {r.source for r in rows} <= set(EDGE_SOURCES)
    # Recording again replaces each source's edges; nothing is duplicated.
    code_graph_store.record_edges(
        store,
        sqlite_session.get_bind(),
        company.id,
        root,
        {"modules": []},
        architecture_id=4,
    )
    sqlite_session.expire_all()
    rows = sqlite_session.exec(select(CodeGraphEdge)).all()
    assert [r.kind for r in rows] == ["verified"]


# --- the section reads the stored graph ------------------------------------------


def test_the_section_reads_the_stored_graph_and_falls_back(project, sqlite_session):
    company = _company(sqlite_session)
    engine, root = sqlite_session.get_bind(), str(project)
    # Nothing stored: on demand, as in the first slice.
    live = _Daemon(serves=("workspace.related",))
    text = code_graph.related_section(
        live, root, ["pkg/store.py"], company_id=company.id, engine=engine
    )
    assert [c[0] for c in live.calls] == ["workspace.related"]
    assert "- pkg/api.py: Store, get_item" in text
    # Stored: the section comes from the store, the daemon is not asked to walk.
    code_graph_store.sync(_store(sqlite_session), _Daemon(), company.id, root)
    silent = _Daemon(serves=())
    stored = code_graph.related_section(
        silent, root, ["pkg/store.py"], company_id=company.id, engine=engine
    )
    assert silent.calls == []
    assert "- pkg/api.py: Store, get_item" in stored
    assert "### What they use\n- pkg/db.py: connect_database" in stored
    assert "as the daemon last reported it" in stored and MARKER not in stored
    # Off: the store is neither read nor written.
    settings_off = _Daemon(serves=("workspace.related",))
    try:
        settings.CODE_GRAPH_STORE = "off"
        code_graph.related_section(
            settings_off, root, ["pkg/store.py"], company_id=company.id, engine=engine
        )
        assert code_graph.persist(settings_off, engine, company.id, root) is None
    finally:
        settings.CODE_GRAPH_STORE = "postgres"
    assert [c[0] for c in settings_off.calls] == ["workspace.related"]


def test_the_task_worker_syncs_the_graph_before_the_section(
    project, sqlite_session, monkeypatch
):
    from models.architecture import Architecture
    from models.specification import Specification
    from models.sprint import BacklogItem, Sprint
    from services.tools import developer_tools

    worker_stages.use_engine(monkeypatch, sqlite_session.get_bind())
    company = _company(sqlite_session)
    spec = Specification(company_id=company.id, title="Spec", status="approved")
    sqlite_session.add(spec)
    sqlite_session.commit()
    sqlite_session.refresh(spec)
    sqlite_session.add(
        Architecture(
            company_id=company.id,
            specification_id=spec.id,
            title="Arch",
            status="approved",
            modules=ARCH["modules"],
        )
    )
    sprint = Sprint(
        company_id=company.id,
        sprint_name="S",
        scope_size="M",
        status="active",
        specification_id=spec.id,
    )
    sqlite_session.add(sprint)
    sqlite_session.commit()
    sqlite_session.refresh(sprint)
    item = BacklogItem(
        sprint_id=sprint.id,
        title="T",
        description="d",
        status="todo",
        covers=["REQ-1.1"],
    )
    sqlite_session.add(item)
    sqlite_session.commit()
    sqlite_session.refresh(item)
    daemon = _Daemon()
    developer_tools.bind(object(), object(), lambda company_id: str(project))
    monkeypatch.setattr(developer_tools, "_request_from_thread", daemon)
    try:
        text = related.related_code_section(
            {"id": item.id},
            {"role": "Developer"},
            company.id,
            engine=sqlite_session.get_bind(),
        )
    finally:
        developer_tools.unbind()
    assert [c[0] for c in daemon.calls] == ["workspace.graph"]
    assert "- pkg/api.py: Store, get_item" in text
    store = _store(sqlite_session)
    assert set(store.known(company.id, str(project))) == {
        "pkg/api.py",
        "pkg/db.py",
        "pkg/store.py",
    }
    kinds = {r.kind for r in sqlite_session.exec(select(CodeGraphEdge)).all()}
    assert {"implements", "depends_on"} <= kinds


def test_an_older_daemon_leaves_the_on_demand_path(project, sqlite_session):
    company = _company(sqlite_session)
    engine, root = sqlite_session.get_bind(), str(project)
    older = _Daemon(serves=("workspace.related",))
    assert code_graph.persist(older, engine, company.id, root) is None
    text = code_graph.related_section(
        older, root, ["pkg/store.py"], company_id=company.id, engine=engine
    )
    assert [c[0] for c in older.calls] == ["workspace.graph", "workspace.related"]
    assert "- pkg/api.py: Store, get_item" in text


# --- the Neo4j store, behind NEO4J_ENABLED ---------------------------------------


class _FakeNeo4j:
    def __init__(self, rows=None):
        self.runs, self.rows = [], rows or {}

    def session(self):
        return self

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        return False

    def run(self, cypher, **params):
        self.runs.append((" ".join(cypher.split()), params))
        for key, rows in self.rows.items():
            if key in cypher:
                return rows
        return []

    def close(self):
        pass


def test_the_store_follows_the_setting_and_neo4j_enabled(monkeypatch, sqlite_session):
    engine = sqlite_session.get_bind()
    monkeypatch.setattr(settings, "CODE_GRAPH_STORE", "neo4j")
    monkeypatch.setattr(settings, "NEO4J_ENABLED", False)
    assert isinstance(code_graph_store.store_for(engine), code_graph_store.SqlStore)
    monkeypatch.setattr(settings, "NEO4J_ENABLED", True)
    fake = _FakeNeo4j()
    monkeypatch.setattr(code_graph_neo4j, "_driver", lambda: fake)
    assert isinstance(code_graph_store.store_for(engine), code_graph_neo4j.Neo4jStore)
    monkeypatch.setattr(settings, "CODE_GRAPH_STORE", "off")
    assert code_graph_store.store_for(engine) is None


def test_the_neo4j_store_replaces_only_the_reported_files(monkeypatch):
    fake = _FakeNeo4j(
        rows={
            "RETURN f.path AS path, f.sha256 AS sha256": [
                {"path": "a.py", "sha256": "a" * 64}
            ]
        }
    )
    monkeypatch.setattr(code_graph_neo4j, "_driver", lambda: fake)
    store = code_graph_neo4j.Neo4jStore()
    assert store.known(1, "/p") == {"a.py": "a" * 64}
    files = code_graph_store.clean_files(
        [
            {
                "path": "b.py",
                "sha256": "b" * 64,
                "defs": [["g", 1, 0]],
                "refs": [["f", 1]],
            }
        ]
    )
    store.apply(1, "/p", files, ["a.py"])
    writes = [(c, p) for c, p in fake.runs if "MERGE (f:CodeFile" in c]
    assert len(writes) == 1 and writes[0][1]["path"] == "b.py"
    assert writes[0][1]["defs"] == [{"name": "g", "line": 1, "depth": 0}]
    removed = [p for c, p in fake.runs if "DETACH DELETE f" in c]
    assert removed and removed[0]["paths"] == ["a.py"]
    # Every statement is scoped to the company and the folder.
    assert all(p.get("company_id") == 1 for _c, p in fake.runs)
