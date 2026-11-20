"""The related code in the Developer's prompt (issue #438, first slice).

The daemon's ``workspace.related`` (``vscode-daemon/code_graph.py``) runs for
real against ``tmp_path``; the recorded runs that wrote a file
(``services/evidence/touched.py``) are read from SQLite rows; the section
(``services/tools/code_graph.py``) and its seeds
(``services/loop/worker/related.py``) reach the Developer's prompt through a
requester that runs the real daemon.
"""

from __future__ import annotations

import asyncio
import importlib.util
import json
from pathlib import Path

import code_graph as daemon_graph  # the daemon's module, on sys.path via conftest
import pytest
import worker_stages
from config import settings
from models.agent_run import TASK_WORKER_PRODUCER, AgentRun, Verification
from models.architecture import Architecture
from models.company import Company
from models.specification import Specification
from models.sprint import BacklogItem, Sprint
from services.evidence import touched
from services.loop.worker import execute as worker_execute
from services.loop.worker import related
from services.tools import code_graph, developer_tools
from workspace_read import ReadError

_DAEMON_PATH = Path(__file__).resolve().parents[2] / "vscode-daemon" / "daemon.py"
MARKER = "contents-that-must-never-leave-the-machine"


def _load_daemon():
    spec = importlib.util.spec_from_file_location("fabrique_daemon_graph", _DAEMON_PATH)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


@pytest.fixture(autouse=True)
def map_state(tmp_path, monkeypatch):
    monkeypatch.setenv("FABRIQUE_MAP_STATE", str(tmp_path / "map-state"))


@pytest.fixture()
def project(tmp_path):
    root = tmp_path / "proj"
    for folder in ("pkg", "web", "node_modules/lib"):
        (root / folder).mkdir(parents=True)
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
    (root / "pkg" / "cli.py").write_text(
        "from pkg.api import handle_request\n\n\ndef main():\n"
        "    return handle_request('k')\n"
    )
    # A file that may hold secrets is never read, nor named.
    (root / "pkg" / "secrets.py").write_text("from pkg.store import Store\n")
    (root / "node_modules" / "lib" / "index.js").write_text(
        "export function useStore() { return Store; }\n"
    )
    (root / "web" / "util.ts").write_text(
        "export function formatName(name: string) {\n  return name;\n}\n"
    )
    (root / "web" / "app.tsx").write_text(
        "import { formatName } from './util';\n"
        "export function App() {\n  return formatName('a');\n}\n"
    )
    return root


def _ask(root, seeds, **extra):
    return daemon_graph.related(str(root), {"seeds": seeds, **extra})


def _by_path(reply):
    return {f["path"]: f for f in reply["files"]}


# --- the daemon: workspace.related -------------------------------------------


def test_edges_carry_the_names_that_create_them_in_both_directions(project):
    reply = _ask(project, ["pkg/store.py"])
    files = _by_path(reply)
    assert set(files) == {"pkg/store.py", "pkg/db.py", "pkg/api.py"}
    store = files["pkg/store.py"]
    assert store["seed"] is True and store["hop"] == 0 and len(store["sha256"]) == 64
    assert store["uses"] == [{"path": "pkg/db.py", "names": ["connect_database"]}]
    assert store["used_by"] == [{"path": "pkg/api.py", "names": ["Store", "get_item"]}]
    assert files["pkg/api.py"]["hop"] == 1 and files["pkg/api.py"]["seed"] is False
    # cli uses api: one hop further, so its edge shows on api but cli is not reached.
    assert {"path": "pkg/cli.py", "names": ["handle_request"]} in files["pkg/api.py"][
        "used_by"
    ]
    assert reply["partial"] is False
    assert reply["seeds_matched"] == ["pkg/store.py"]
    assert reply["seeds_unmatched"] == []


def test_two_hops_reach_the_files_of_the_files(project):
    files = _by_path(_ask(project, ["pkg/store.py"], hops=2))
    assert files["pkg/cli.py"]["hop"] == 2
    # More than two is two.
    assert _by_path(_ask(project, ["pkg/store.py"], hops=9)) == files


def test_a_folder_seed_matches_every_file_under_it(project):
    for seed in ("web", "web/", "./web/"):
        reply = _ask(project, [seed])
        files = _by_path(reply)
        assert {p for p, f in files.items() if f["seed"]} == {
            "web/app.tsx",
            "web/util.ts",
        }
        assert files["web/app.tsx"]["uses"] == [
            {"path": "web/util.ts", "names": ["formatName"]}
        ]
        assert reply["seeds_matched"] == [seed]


def test_unmatched_seeds_are_named_and_never_read(project):
    reply = _ask(
        project,
        [
            "missing.py",
            "../proj/pkg/db.py",
            "/etc/passwd",
            "pkg/secrets.py",
            ".",
            "pkg/db.py",
        ],
    )
    assert reply["seeds_matched"] == ["pkg/db.py"]
    assert reply["seeds_unmatched"] == [
        "missing.py",
        "../proj/pkg/db.py",
        "/etc/passwd",
        "pkg/secrets.py",
        ".",
    ]
    paths = set(_by_path(reply))
    assert "pkg/secrets.py" not in json.dumps(reply["files"])
    assert not any(p.startswith("node_modules") for p in paths)
    assert _ask(project, ["nothing/here.py"])["files"] == []


def test_no_file_contents_leave_the_machine(project):
    reply = _ask(project, ["pkg"], hops=2)
    assert reply["files"]
    text = json.dumps(reply)
    assert MARKER not in text and "return url" not in text
    for row in reply["files"]:
        assert set(row) == {
            "path",
            "sha256",
            "hop",
            "seed",
            "uses",
            "used_by",
            "more_uses",
            "more_used_by",
        }


def test_caps_on_files_edges_and_names_and_partial(project):
    many = "\n".join(f"def helper_number_{i}():\n    return {i}\n" for i in range(12))
    (project / "pkg" / "many.py").write_text(many)
    (project / "pkg" / "caller.py").write_text(
        "from pkg import many\n"
        + "\n".join(f"many.helper_number_{i}()" for i in range(12))
    )
    for i in range(12):
        (project / "pkg" / f"user_{i}.py").write_text(
            "from pkg.many import helper_number_0\nhelper_number_0()\n"
        )
    reply = _ask(project, ["pkg/many.py"], max_files=200)
    files = _by_path(reply)
    many_row = files["pkg/many.py"]
    assert len(many_row["used_by"]) == daemon_graph.MAX_EDGES
    assert many_row["more_used_by"] == 13 - daemon_graph.MAX_EDGES
    caller_edge = next(e for e in many_row["used_by"] if e["path"] == "pkg/caller.py")
    assert len(caller_edge["names"]) == daemon_graph.MAX_NAMES
    # The tightest tie comes first.
    assert many_row["used_by"][0]["path"] == "pkg/caller.py"
    assert len(files) == 14 and reply["partial"] is False
    cut = _ask(project, ["pkg/many.py"], max_files=3)
    assert len(cut["files"]) == 3 and cut["partial"] is True
    assert [f["path"] for f in cut["files"]][:2] == ["pkg/many.py", "pkg/caller.py"]


def test_the_deadline_stops_the_walk_and_says_so(project, monkeypatch):
    monkeypatch.setattr(daemon_graph, "DEADLINE_SECONDS", -1.0)
    reply = _ask(project, ["pkg/store.py"])
    assert reply["partial"] is True and reply["files"] == []


def test_the_parse_cache_is_shared_with_the_repository_map(project, monkeypatch):
    import repo_map

    repo_map.build_map(str(project), 3000)  # warms the parse cache
    monkeypatch.setattr(
        repo_map, "parse_file", lambda *a: pytest.fail("parsed a cached file again")
    )
    assert _by_path(_ask(project, ["pkg/store.py"]))["pkg/store.py"]["used_by"]


def test_a_request_over_its_bounds_is_refused(project):
    with pytest.raises(ReadError) as too_many:
        _ask(project, [f"m{i}.py" for i in range(daemon_graph.MAX_SEEDS + 1)])
    assert too_many.value.status == 400 and "seeds" in too_many.value.message
    with pytest.raises(ReadError):
        daemon_graph.related(str(project), {"seeds": "pkg/store.py"})
    with pytest.raises(ReadError):
        _ask(project, ["pkg/store.py"], hops="two")
    with pytest.raises(ReadError) as missing:
        daemon_graph.related(str(project / "nope"), {"seeds": ["a.py"]})
    assert missing.value.status == 404
    assert daemon_graph.MAX_SEEDS == code_graph.MAX_SEEDS


def test_the_daemon_answers_workspace_related_over_nats(project):
    daemon = _load_daemon()
    assert (
        "workspace.related",
        daemon_graph.handle_related,
    ) in daemon.code_graph.HANDLERS
    assert "*code_graph.HANDLERS" in _DAEMON_PATH.read_text()
    replies = []

    class _Msg:
        def __init__(self, body):
            self.data = json.dumps(body).encode()
            self.reply = "inbox"

    async def reply(nc, msg, body):
        replies.append(body)

    async def run():
        for body in (
            {"workspace_root": str(project), "seeds": ["pkg/store.py"]},
            {"seeds": ["pkg/store.py"]},
            {"workspace_root": str(project), "seeds": 3},
        ):
            await daemon_graph.handle_related(
                None,
                _Msg(body),
                reply,
                daemon._request_body,
                daemon._resolve_target_path,
            )

    asyncio.run(run())
    assert replies[0]["seeds_matched"] == ["pkg/store.py"]
    assert replies[1]["error"]["status"] == 400
    assert replies[2]["error"]["status"] == 400


# --- the evidence: which recorded runs wrote which files ----------------------


def _company(session, name):
    company = Company(company_name=name, autonomy_level=2)
    session.add(company)
    session.commit()
    session.refresh(company)
    return company


def _run(session, company_id, ref, title, version=1, producer=TASK_WORKER_PRODUCER):
    run = AgentRun(
        company_id=company_id,
        external_ref=ref,
        version=version,
        producer=producer,
        subject={"backlog_item_id": int(ref.split("-")[-1]), "title": title},
        artifact_digest="0" * 64,
        artifact_size=0,
    )
    session.add(run)
    session.commit()
    session.refresh(run)
    return run


def _verify(session, run, verdict, writes):
    evidence = {"workspace_writes": writes}
    session.add(
        Verification(
            company_id=run.company_id,
            agent_run_id=run.id,
            verdict=verdict,
            evidence=evidence,
        )
    )
    session.commit()


def _written(*paths, status="written"):
    return [{"path": p, "status": status, "sha256": "a" * 64} for p in paths]


@pytest.fixture()
def runs(sqlite_session):
    s = sqlite_session
    company = _company(s, "Touched Co")
    other = _company(s, "Other Co")
    first = _run(s, company.id, "task-11", "Add the store")
    _verify(
        s,
        first,
        "verified",
        _written("pkg/store.py") + _written("pkg/x.py", status="failed"),
    )
    refuted = _run(s, company.id, "task-12", "Break the api")
    _verify(s, refuted, "refuted", _written("pkg/api.py", "pkg/store.py"))
    # A queued delivery: no report at first, then the daemon's; the latest verdict counts.
    queued = _run(s, company.id, "task-13", "Queued")
    _verify(s, queued, "unverifiable", None)
    _verify(s, queued, "verified", _written("pkg/db.py"))
    rework = _run(s, company.id, "task-11", "Add the store", version=2)
    _verify(s, rework, "verified", _written("pkg/cache.py"))
    # Not the task worker's, another company's: never read.
    _verify(
        s,
        _run(s, company.id, "ext-14", "Other producer", producer="gh"),
        "verified",
        _written("pkg/store.py"),
    )
    _verify(
        s,
        _run(s, other.id, "task-15", "Elsewhere"),
        "verified",
        _written("pkg/store.py"),
    )
    return {"company": company, "first": first, "refuted": refuted, "queued": queued}


def test_runs_touching_reads_the_daemons_reports_newest_first(sqlite_session, runs):
    found = touched.runs_touching(
        sqlite_session, runs["company"].id, ["pkg/store.py", "pkg/db.py", "pkg/x.py"]
    )
    assert [(r["title"], r["verdict"], r["written"]) for r in found] == [
        ("Queued", "verified", ["pkg/db.py"]),
        ("Break the api", "refuted", ["pkg/store.py"]),
        ("Add the store", "verified", ["pkg/store.py"]),  # x.py failed: not written
    ]
    assert (
        found[1]["run_id"] == runs["refuted"].id and found[1]["backlog_item_id"] == 12
    )
    assert (
        len(
            touched.runs_touching(
                sqlite_session, runs["company"].id, ["pkg/store.py"], 1
            )
        )
        == 1
    )
    assert touched.runs_touching(sqlite_session, runs["company"].id, []) == []
    assert touched.runs_touching(sqlite_session, None, ["pkg/store.py"]) == []
    assert (
        touched.runs_touching(sqlite_session, runs["company"].id, ["nowhere.py"]) == []
    )


def test_written_by_task_unions_the_tasks_own_runs(sqlite_session, runs):
    assert touched.written_by_task(sqlite_session, runs["company"].id, 11) == [
        "pkg/cache.py",
        "pkg/store.py",
    ]
    assert touched.written_by_task(sqlite_session, runs["company"].id, 99) == []


# --- the section ---------------------------------------------------------------


class _Requester:
    def __init__(self, root=None, reply=None, error=None):
        self.root, self.reply, self.error = root, reply, error
        self.calls = []

    def __call__(self, subject, payload, timeout):
        self.calls.append((subject, payload, timeout))
        if subject != code_graph.SUBJECT or self.error:
            raise self.error or developer_tools.DaemonReplyError("no responders")
        if self.reply is not None:
            return self.reply
        return daemon_graph.related(payload["workspace_root"], payload)


def test_the_section_names_what_the_seeds_use_and_what_uses_them(
    project, sqlite_session, runs, monkeypatch
):
    requester = _Requester()
    text = code_graph.related_section(
        requester,
        str(project),
        ["pkg/store.py", "pkg/store.py", "  "],
        company_id=runs["company"].id,
        engine=sqlite_session.get_bind(),
    )
    _subject, payload, timeout = requester.calls[0]
    assert (
        payload["seeds"] == ["pkg/store.py"] and timeout == code_graph.TIMEOUT_SECONDS
    )
    assert text.startswith("\n\n## Related code\n") and "not evidence" in text
    assert "### Starting files\n- pkg/store.py" in text
    assert "### What they use\n- pkg/db.py: connect_database" in text
    assert "- pkg/api.py: Store, get_item" in text
    assert "a change could break these" in text
    assert "### Recent runs that touched these files" in text
    assert (
        '"Break the api" (refuted — REFUTED: its delivery did not hold): pkg/api.py, pkg/store.py'
        in text
    )
    assert '"Add the store" (verified): pkg/store.py' in text
    assert MARKER not in text
    assert len(text) <= settings.CODE_GRAPH_MAX_CHARS


def test_the_section_keeps_to_its_budget(project, monkeypatch):
    monkeypatch.setattr(settings, "CODE_GRAPH_MAX_CHARS", 300)
    text = code_graph.related_section(
        _Requester(), str(project), ["pkg"], company_id=None, engine=None
    )
    assert len(text) <= 300 and text.endswith("search for the rest]")


def test_every_failure_leaves_the_section_out(project, monkeypatch):
    def section(requester, seeds=("pkg/store.py",), root=str(project)):
        return code_graph.related_section(
            requester, root, list(seeds), company_id=None, engine=None
        )

    unused = _Requester()
    assert section(unused, seeds=()) == "" and section(unused, seeds=["", " "]) == ""
    assert section(unused, root=None) == ""
    assert unused.calls == []
    # An older daemon without the subject, a timeout, an error.
    assert (
        section(_Requester(error=developer_tools.DaemonReplyError("no responders")))
        == ""
    )
    assert section(_Requester(error=TimeoutError())) == ""
    assert section(_Requester(reply={"error": "x"})) == ""
    assert section(_Requester(reply={"files": []})) == ""
    assert section(_Requester(reply={"files": [{"path": "a.py", "seed": False}]})) == ""
    assert section(_Requester(), seeds=["nothing.py"]) == ""
    monkeypatch.setattr(settings, "CODE_GRAPH_MAX_CHARS", 0)
    disabled = _Requester()
    assert section(disabled) == "" and disabled.calls == []


# --- the seeds and the Developer's prompt ------------------------------------------


def test_module_paths_follow_the_tasks_requirements():
    arch = {
        "modules": [
            {"id": "MOD-1", "path": "pkg/store.py", "covers": ["REQ-1"]},
            {"id": "MOD-2", "path": "web/", "covers": ["REQ-2"]},
            {"id": "MOD-3", "path": "", "covers": ["REQ-1"]},
            {"id": "MOD-4", "path": "pkg/store.py", "covers": ["REQ-1.1"]},
        ]
    }
    assert related.module_paths(arch, ["REQ-1.1"]) == ["pkg/store.py"]
    assert related.module_paths(arch, ["REQ-2.3", "REQ-1.2"]) == [
        "pkg/store.py",
        "web/",
    ]
    assert related.module_paths(arch, []) == []


def _task(session, covers, arch_modules):
    company = _company(session, "Graph Co")
    spec = Specification(company_id=company.id, title="Spec", status="approved")
    session.add(spec)
    session.commit()
    session.refresh(spec)
    session.add(
        Architecture(
            company_id=company.id,
            specification_id=spec.id,
            title="Arch",
            status="approved",
            modules=arch_modules,
        )
    )
    sprint = Sprint(
        company_id=company.id,
        sprint_name="S",
        scope_size="M",
        status="active",
        specification_id=spec.id,
    )
    session.add(sprint)
    session.commit()
    session.refresh(sprint)
    item = BacklogItem(
        sprint_id=sprint.id,
        title="Cache items",
        description="d",
        status="todo",
        covers=covers,
    )
    session.add(item)
    session.commit()
    session.refresh(item)
    return company, item


def _prompt(company_id, item):
    return worker_execute._developer_prompt(
        {"id": item.id, "title": item.title, "description": item.description},
        {"role": "Developer"},
        "S",
        None,
        None,
        None,
        company_id,
        None,
        None,
        None,
    )


MODULES = [
    {"id": "MOD-1", "name": "Store", "path": "pkg/store.py", "covers": ["REQ-1"]}
]


def test_the_related_code_reaches_the_developers_prompt(
    project, sqlite_session, monkeypatch
):
    worker_stages.use_engine(monkeypatch, sqlite_session.get_bind())
    company, item = _task(sqlite_session, ["REQ-1.1"], MODULES)
    # Its own earlier run wrote web/app.tsx: a rework seed.
    _verify(
        sqlite_session,
        _run(sqlite_session, company.id, f"task-{item.id}", item.title),
        "refuted",
        _written("web/app.tsx"),
    )
    requester = _Requester()
    developer_tools.bind(object(), object(), lambda company_id: str(project))
    monkeypatch.setattr(developer_tools, "_request_from_thread", requester)
    try:
        text, _, measure = _prompt(company.id, item)
    finally:
        developer_tools.unbind()
    asked = [c for c in requester.calls if c[0] == code_graph.SUBJECT]
    assert asked[0][1]["seeds"] == ["pkg/store.py", "web/app.tsx"]
    assert "## Related code" in text and "- pkg/api.py: Store, get_item" in text
    assert '"Cache items" (refuted — REFUTED' in text
    names = [s["name"] for s in measure["sections"]]
    assert "related_code" in names
    assert names.index("related_code") > names.index("task")
    assert (
        next(s for s in measure["sections"] if s["name"] == "related_code")["priority"]
        == 3
    )


def test_without_seeds_the_prompt_is_unchanged(project, sqlite_session, monkeypatch):
    worker_stages.use_engine(monkeypatch, sqlite_session.get_bind())
    company, item = _task(sqlite_session, ["REQ-9.1"], MODULES)
    requester = _Requester()
    developer_tools.bind(object(), object(), lambda company_id: str(project))
    monkeypatch.setattr(developer_tools, "_request_from_thread", requester)
    try:
        text, _, measure = _prompt(company.id, item)
        monkeypatch.setattr(settings, "CODE_GRAPH_MAX_CHARS", 0)
        before, _, before_measure = _prompt(company.id, item)
    finally:
        developer_tools.unbind()
    assert not [c for c in requester.calls if c[0] == code_graph.SUBJECT]
    assert text == before and measure == before_measure
    assert "related_code" not in [s["name"] for s in measure["sections"]]
    # No daemon bound at all: nothing asked, the same prompt.
    assert _prompt(company.id, item)[0] == before
