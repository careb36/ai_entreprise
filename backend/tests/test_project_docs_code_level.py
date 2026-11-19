"""The C4 code level of the documentation pack (issue #693,
``docs/design/PROJECT_DOCUMENTATION.md``).

For each component the architecture names, ``c4.md`` lists the files the
stored code graph (issue #438, ``services/tools/code_graph_store.py``) has
under the component's path, the names each defines and the references
between them. Which files a component is made of is the architecture's
claim (its ``implements`` edge to the module's path); the files, their
definitions and the references are observed, each with the sha256 the
workspace daemon reported. The two are separate fields of the snapshot and
separate statements in the document. With no stored graph the section says
"not observed"; the pack never asks the daemon; a stored file whose sha256
changes makes ``c4.md`` stale and leaves the diagrams alone.
"""

from __future__ import annotations

import copy
import re

import pytest
from config import settings
from models.company import Company, ProjectDiscovery
from project_docs_helpers import _docs, _Pack, _snapshot
from services.planning.project_docs import c4, collect, collect_code, pack
from services.tools import code_graph_store, code_level

ROOT = "/home/someone/src/board"
SHA_API = "a1" * 32
SHA_STORE = "b2" * 32
SHA_WEB = "c3" * 32
SHA_OTHER = "d4" * 32


def _file(path, sha256, defs=(), refs=()):
    return {
        "path": path,
        "sha256": sha256,
        "status": "parsed",
        "truncated": False,
        "defs": [(name, i + 1, 0) for i, name in enumerate(defs)],
        "refs": [(name, 1) for name in refs],
    }


FILES = [
    _file("src/api/app.py", SHA_API, defs=["handle_request"], refs=["TaskStore"]),
    _file("src/store/db.py", SHA_STORE, defs=["TaskStore", "connect"]),
    _file("web/main.ts", SHA_WEB, defs=["render"], refs=["handle_request"]),
    _file("tools/other.py", SHA_OTHER, defs=["helper"], refs=["TaskStore"]),
]


def _observed_snapshot():
    snapshot = _snapshot()
    snapshot["code_graph"] = {
        "status": "stored",
        "claims": [
            {
                "module": "MOD-1",
                "path": "src/api/",
                "source_ref": "architecture:5",
            },
            {
                "module": "MOD-2",
                "path": "src/store/",
                "source_ref": "architecture:5",
            },
            {"module": "MOD-3", "path": "web/", "source_ref": "architecture:5"},
        ],
        "observed": {
            "files": [
                {
                    "path": "src/api/app.py",
                    "sha256": SHA_API,
                    "modules": ["MOD-1"],
                    "defines": ["handle_request"],
                    "more_defines": 0,
                },
                {
                    "path": "src/store/db.py",
                    "sha256": SHA_STORE,
                    "modules": ["MOD-2"],
                    "defines": ["TaskStore", "connect"],
                    "more_defines": 0,
                },
            ],
            "references": [
                {
                    "from": "src/api/app.py",
                    "from_sha256": SHA_API,
                    "to": "src/store/db.py",
                    "to_sha256": SHA_STORE,
                    "names": ["TaskStore"],
                    "more_names": 0,
                }
            ],
            "more_files": 0,
            "more_references": 0,
        },
    }
    snapshot["code_graph_observed_at"] = "2026-11-18T09:00:00"
    return snapshot


# --- the document ----------------------------------------------------------------


def test_the_code_level_lists_each_components_files_and_their_references():
    docs, _ = _docs(_observed_snapshot())
    markdown = docs["c4"]["markdown"]
    code = markdown.split("## Code level", 1)[1]
    assert "### Board API (MOD-1)" in code
    assert "### Store (MOD-2)" in code
    assert "src/api/app.py" in code and "handle\\_request" in code
    assert SHA_API[:12] in code and SHA_STORE[:12] in code
    # The full sha256 the daemon reported is in the list of sources.
    assert SHA_API in markdown.split("## Sources", 1)[1]
    # Claims and observations are said to be different things.
    assert "**Claim.**" in code and "**Observed.**" in code
    assert "2026-11-18T09:00:00" in code
    # A component whose path holds no stored file is not observed, not invented.
    web = code.split("### Web client (MOD-3)", 1)[1].split("###", 1)[0]
    assert "not observed" in web and "main.ts" not in web
    assert "code level" not in docs["c4"]["missing"]
    assert "code_graph" in docs["c4"]["inputs"]


def test_the_claim_cites_the_architecture_as_a_claim_and_the_files_as_observed():
    docs, _ = _docs(_observed_snapshot())
    sources = docs["c4"]["sources"]
    claimed = [s for s in sources if s.get("claim")]
    assert claimed and all(s["kind"] == "module" for s in claimed)
    files = [s for s in sources if s["kind"] == "code_file"]
    assert {(s["id"], s["sha256"]) for s in files} == {
        ("src/api/app.py", SHA_API),
        ("src/store/db.py", SHA_STORE),
    }
    assert not any(s.get("claim") for s in files)


def test_without_a_stored_graph_the_code_level_says_not_observed():
    snapshot = _snapshot()
    docs, _ = _docs(snapshot)
    code = docs["c4"]["markdown"].split("## Code level", 1)[1]
    assert "not observed" in code
    assert "code level" in docs["c4"]["missing"]
    snapshot["code_graph"] = {"status": "not_stored", "claims": [], "observed": None}
    docs, _ = _docs(snapshot)
    assert "code level" in docs["c4"]["missing"]
    assert "Claim." not in docs["c4"]["markdown"]


def test_the_code_level_is_written_in_spanish_too():
    snapshot = _observed_snapshot()
    snapshot["language"] = "es"
    docs, _ = _docs(snapshot)
    markdown = docs["c4"]["markdown"]
    assert "## Nivel de código" in markdown
    assert "**Afirmación.**" in markdown and "**Observado.**" in markdown
    assert "no observado" in markdown
    english = _docs(_observed_snapshot())[0]["c4"]
    assert [s["sha256"] for s in docs["c4"]["sources"] if "sha256" in s] == [
        s["sha256"] for s in english["sources"] if "sha256" in s
    ]


def test_definition_names_cannot_become_markdown_structure():
    snapshot = _observed_snapshot()
    files = snapshot["code_graph"]["observed"]["files"]
    files[0]["defines"] = ["x | y](javascript:alert(1))", "<img src=x>"]
    files[0]["path"] = "src/api/`evil`|.py"
    markdown = _docs(snapshot)[0]["c4"]["markdown"]
    assert "<img" not in markdown and "&lt;img src=x&gt;" in markdown
    # A link needs an unescaped "](": every bracket from the parse is escaped.
    assert not re.search(r"(?<!\\)\]\(", markdown)
    assert "`evil`" not in markdown and "src/api/\\`evil\\`\\|.py" in markdown


def test_a_changed_file_makes_the_c4_document_stale_and_not_the_diagrams():
    snapshot = _observed_snapshot()
    stored = _Pack(*pack.build_documents(snapshot))
    assert pack.lint(stored, snapshot)["ok"] is True
    later = copy.deepcopy(snapshot)
    later["code_graph_observed_at"] = "2026-11-19T09:00:00"
    assert pack.lint(stored, later)["ok"] is True
    later["code_graph"]["observed"]["files"][0]["sha256"] = "ee" * 32
    result = pack.lint(stored, later)
    stale = {d["key"] for d in result["documents"] if d["stale"]}
    assert stale == {"c4", "index"} and result["ok"] is False
    assert not any(d["stale"] for d in result["diagrams"])


def test_the_diagrams_do_not_read_the_code_graph():
    snapshot = _observed_snapshot()
    bare = copy.deepcopy(snapshot)
    bare["code_graph"] = None
    assert c4.diagrams(snapshot) == c4.diagrams(bare)


# --- the collector, on the stored graph ------------------------------------------


def _company(session):
    company = Company(company_name="Code level Co", autonomy_level=2)
    session.add(company)
    session.commit()
    session.refresh(company)
    session.add(ProjectDiscovery(company_id=company.id, project_path=ROOT))
    session.commit()
    return company


@pytest.fixture(autouse=True)
def postgres_store(monkeypatch):
    monkeypatch.setattr(settings, "CODE_GRAPH_STORE", "postgres")


@pytest.fixture()
def no_daemon(monkeypatch):
    def refuse(*_args, **_kwargs):
        raise AssertionError("the pack asked the daemon")

    monkeypatch.setattr(code_graph_store, "sync", refuse)


def test_the_collector_maps_the_stored_files_to_the_architecture(
    sqlite_session, no_daemon
):
    company = _company(sqlite_session)
    store = code_graph_store.SqlStore(sqlite_session.get_bind())
    store.apply(company.id, ROOT, FILES, [])
    arch = _snapshot()["architecture"]
    graph, observed_at = collect_code.code_graph(sqlite_session, company.id, arch)
    assert graph["status"] == "stored" and observed_at
    assert graph["claims"] == [
        {"module": "MOD-1", "path": "src/api/", "source_ref": "architecture:5"},
        {"module": "MOD-2", "path": "src/store/", "source_ref": "architecture:5"},
        {"module": "MOD-3", "path": "web/", "source_ref": "architecture:5"},
    ]
    files = {f["path"]: f for f in graph["observed"]["files"]}
    assert set(files) == {"src/api/app.py", "src/store/db.py", "web/main.ts"}
    assert files["src/api/app.py"]["sha256"] == SHA_API
    assert files["src/api/app.py"]["modules"] == ["MOD-1"]
    assert files["src/store/db.py"]["defines"] == ["TaskStore", "connect"]
    edges = {(r["from"], r["to"]): r for r in graph["observed"]["references"]}
    assert set(edges) == {
        ("src/api/app.py", "src/store/db.py"),
        ("web/main.ts", "src/api/app.py"),
    }
    assert edges[("src/api/app.py", "src/store/db.py")]["names"] == ["TaskStore"]
    assert edges[("web/main.ts", "src/api/app.py")]["to_sha256"] == SHA_API
    # No claim is in an observed field and no observation in a claim.
    assert all(set(c) == {"module", "path", "source_ref"} for c in graph["claims"])
    assert "source_ref" not in str(graph["observed"])
    # The project folder (it can carry a person's name) is not in the pack.
    assert ROOT not in str(graph)


def test_the_snapshot_carries_the_code_graph_and_a_changed_file_makes_c4_stale(
    sqlite_session, no_daemon, monkeypatch
):
    company = _company(sqlite_session)
    arch = _snapshot()["architecture"]
    monkeypatch.setattr(collect, "_architecture", lambda _row: copy.deepcopy(arch))
    store = code_graph_store.SqlStore(sqlite_session.get_bind())
    store.apply(company.id, ROOT, FILES, [])
    first = collect.snapshot(sqlite_session, company)
    assert first["code_graph"]["status"] == "stored"
    stored = _Pack(*pack.build_documents(first))
    assert pack.lint(stored, collect.snapshot(sqlite_session, company))["ok"] is True
    # An unrelated file re-reported: nothing the pack cites changed.
    store.apply(company.id, ROOT, [_file("tools/other.py", "ff" * 32)], [])
    assert pack.lint(stored, collect.snapshot(sqlite_session, company))["ok"] is True
    store.apply(
        company.id,
        ROOT,
        [_file("src/store/db.py", "0e" * 32, defs=["TaskStore", "connect"])],
        [],
    )
    result = pack.lint(stored, collect.snapshot(sqlite_session, company))
    stale = {d["key"] for d in result["documents"] if d["stale"]}
    assert "c4" in stale and result["ok"] is False
    assert not any(d["stale"] for d in result["diagrams"])


@pytest.mark.parametrize("case", ["nothing_stored", "store_off", "no_folder"])
def test_the_collector_says_not_stored_rather_than_inventing(
    sqlite_session, no_daemon, monkeypatch, case
):
    company = _company(sqlite_session)
    arch = _snapshot()["architecture"]
    if case == "store_off":
        store = code_graph_store.SqlStore(sqlite_session.get_bind())
        store.apply(company.id, ROOT, FILES, [])
        monkeypatch.setattr(settings, "CODE_GRAPH_STORE", "off")
    if case == "no_folder":
        monkeypatch.setattr(collect_code, "project_folder", lambda *_a: None)
    graph, observed_at = collect_code.code_graph(sqlite_session, company.id, arch)
    assert graph["observed"] is None and observed_at is None
    assert graph["status"] == "not_stored"
    # The claims stand on their own: they come from the architecture.
    assert [c["module"] for c in graph["claims"]] == ["MOD-1", "MOD-2", "MOD-3"]


def test_without_an_architecture_there_is_no_code_level(sqlite_session, no_daemon):
    company = _company(sqlite_session)
    assert collect_code.code_graph(sqlite_session, company.id, None) == (None, None)


def test_a_store_that_fails_reads_as_not_stored(sqlite_session, monkeypatch):
    class Broken:
        def known(self, *_args):
            raise RuntimeError("down")

    monkeypatch.setattr(code_graph_store, "store_for", lambda _engine: Broken())
    graph = code_level.stored(
        sqlite_session.get_bind(), 1, ROOT, _snapshot()["architecture"], 5
    )
    assert graph["status"] == "not_stored" and graph["observed"] is None


def test_the_files_per_component_and_the_references_are_bounded(sqlite_session):
    company = _company(sqlite_session)
    many = [
        _file(f"src/api/m{i:03}.py", f"{i:064x}", defs=[f"f{i}"], refs=["TaskStore"])
        for i in range(code_level.MAX_FILES_PER_MODULE + 5)
    ]
    store = code_graph_store.SqlStore(sqlite_session.get_bind())
    store.apply(company.id, ROOT, [*many, FILES[1]], [])
    graph = code_level.stored(
        sqlite_session.get_bind(), company.id, ROOT, _snapshot()["architecture"], 5
    )
    observed = graph["observed"]
    api = [f for f in observed["files"] if "MOD-1" in f["modules"]]
    assert len(api) == code_level.MAX_FILES_PER_MODULE
    assert observed["more_files"] == 5
    assert len(observed["references"]) <= code_level.MAX_REFERENCES
