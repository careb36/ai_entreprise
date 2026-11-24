"""The documentation build and the snippets' names (``doc_build``, ``doc_snippets``, ``loop/docs_checks``).

A delivery that changes documentation gets two more observations: the
project's docs build run through the daemon in the sandbox (pass, refuted,
unverifiable; no check at all without a build command, never a pass), and
the project names its fenced snippets import or call looked up in the
daemon's code graph (a name with no definition is a finding, never a
refutation). The build command comes from the stack profile, detected from a
MkDocs, Sphinx or Docusaurus configuration.
"""

from __future__ import annotations

import asyncio
import hashlib

import abilities  # the daemon's modules, on sys.path via conftest
import code_graph as daemon_graph
import pytest
from config import settings
from services.evidence import delivery_gate, doc_build, doc_snippets, markdown_refs
from services.loop import docs_checks
from services.workspace import daemon_liveness, delivery_commands, stack_profiles
from services.workspace.project_profile import Context, _detect_both

GUIDE = """# Guide

```python
from pkg.store import Store, open_store
from pkg import db
import pkg.api as api

api.handle_request("k")
```

```ts
import { formatName, type missingHelper } from './web/util'
import React from 'react'
```

```bash
from pkg.store import NotPython
```
"""


def _sha(text: str) -> str:
    return hashlib.sha256(text.encode()).hexdigest()


def _evidence(files: dict[str, str], tree: list[str], root: str = "/p") -> dict:
    return {
        "workspace_root": root,
        "workspace_writes": [
            {"path": p, "status": "written", "sha256": _sha(c)}
            for p, c in files.items()
        ],
        "project_files": {"files": tree, "truncated": False},
    }


TREE = ["pkg/__init__.py", "pkg/store.py", "pkg/db.py", "pkg/api.py", "web/util.ts"]


# --- the docs build check ------------------------------------------------------


def _build(**record) -> dict | None:
    return doc_build.check(
        {"docs_build": {"applicable": True, "command": "mkdocs build", **record}}
    )


def test_a_build_that_exits_zero_passes_with_the_output_digest():
    check = _build(exit_code=0, output_sha256="a" * 64, available=True)
    assert check["outcome"] == "pass" and "exit 0" in check["observation"]
    assert "aaaaaaaaaaaa" in check["observation"]


def test_a_failed_build_refutes_with_its_first_errors_and_the_gate_names_it():
    output = "INFO building\nERROR - Config value 'nav': a file is missing\nWARNING x\n"
    errors = doc_build.first_errors(output)
    assert errors == ["ERROR - Config value 'nav': a file is missing"]
    check = _build(exit_code=1, errors=errors, available=True)
    assert check["outcome"] == "refuted" and "a file is missing" in check["observation"]
    assert (
        delivery_gate._GATE_PROBLEMS[doc_build.CHECK_NAME]
        == "documentation build failed"
    )


def test_no_build_command_is_not_applicable_never_a_pass():
    assert (
        doc_build.check({"docs_build": {"applicable": False, "reason": "none"}}) is None
    )
    assert doc_build.check({}) is None


@pytest.mark.parametrize(
    "record",
    [
        {"exit_code": 127, "available": False},
        {"exit_code": None, "reason": "no sandbox policy"},
        {"exit_code": None, "timed_out": True},
        {"exit_code": 1, "refused": True, "reason": "denied"},
    ],
)
def test_a_build_that_did_not_really_run_is_unverifiable(record):
    assert _build(**record)["outcome"] == "unverifiable"


def test_a_missing_builder_is_told_apart_from_a_failure():
    assert doc_build.unavailable(1, "/usr/bin/python: No module named mkdocs")
    assert doc_build.unavailable(127, "")
    assert not doc_build.unavailable(1, "ERROR - broken nav")


# --- where the build command comes from -----------------------------------------


@pytest.mark.parametrize(
    "files,expected",
    [
        (
            ["mkdocs.yml"],
            "python -m mkdocs build -f mkdocs.yml --site-dir /tmp/fabrique-docs-site",
        ),
        (["docs/conf.py"], "python -m sphinx -b html -q docs /tmp/fabrique-docs-site"),
        (
            ["docs/source/conf.py"],
            "python -m sphinx -b html -q docs/source /tmp/fabrique-docs-site",
        ),
        (
            ["website/docusaurus.config.ts"],
            "cd website && npx --no-install docusaurus build --out-dir /tmp/fabrique-docs-site",
        ),
        (["README.md"], None),
    ],
)
def test_the_docs_build_is_detected_from_the_docs_configuration(
    tmp_path, files, expected
):
    for name in files:
        (tmp_path / name).parent.mkdir(parents=True, exist_ok=True)
        (tmp_path / name).write_text("")
    assert stack_profiles.docs_build(str(tmp_path)) == expected
    profile, _folders = _detect_both(str(tmp_path))
    assert (profile or {}).get("docs_build") == expected
    # A company may name its own, or turn it off.
    resolved = stack_profiles.resolve(profile, {"docs_build": ""})
    assert resolved["docs_build"] is None


# --- snippet names ---------------------------------------------------------------


def test_snippets_yield_the_project_names_they_import_or_call():
    evidence = _evidence({"docs/guide.md": GUIDE}, TREE)
    asked = doc_snippets.wanted(evidence, [{"path": "docs/guide.md", "content": GUIDE}])
    got = {(r["module"], r["name"], r["how"]): r for r in asked["names"]}
    assert set(got) == {
        ("pkg.store", "Store", "import"),
        ("pkg.store", "open_store", "import"),
        ("pkg", "db", "import"),
        ("pkg.api", "handle_request", "call"),
        ("./web/util", "formatName", "import"),
        ("./web/util", "missingHelper", "import"),
    }
    # A submodule of the listing is found there; nothing else before the daemon.
    assert got[("pkg", "db", "import")]["found"] is True
    assert got[("pkg.store", "Store", "import")]["found"] is None
    assert got[("pkg.store", "Store", "import")]["line"] == 4


def test_fenced_blocks_keep_their_language_and_lines():
    blocks = markdown_refs.fenced_blocks(GUIDE)
    assert [lang for lang, _ in blocks] == ["python", "ts", "bash"]
    assert blocks[0][1][0] == (4, "from pkg.store import Store, open_store")


def test_a_snippet_name_that_does_not_exist_is_a_finding_not_a_refutation():
    rows = [
        {
            "document": "docs/guide.md",
            "line": 4,
            "module": "pkg.store",
            "name": "Store",
            "found": True,
        },
        {
            "document": "docs/guide.md",
            "line": 4,
            "module": "pkg.store",
            "name": "open_store",
            "found": False,
        },
    ]
    check = doc_snippets.check({"docs_snippets": {"observed": True, "names": rows}})
    assert check["outcome"] == "pass"
    assert check["findings"] == [
        "docs/guide.md:4 `open_store` from pkg.store (no definition in the project's parse)"
    ]
    assert "1 of 2" in check["observation"]


def test_names_the_daemon_could_not_settle_are_unverifiable_and_none_is_no_check():
    rows = [{"document": "d.md", "line": 1, "module": "m", "name": "x", "found": None}]
    check = doc_snippets.check(
        {"docs_snippets": {"observed": False, "reason": "not announced", "names": rows}}
    )
    assert (
        check["outcome"] == "unverifiable" and "not announced" in check["observation"]
    )
    assert doc_snippets.check({"docs_snippets": {"names": []}}) is None
    assert doc_snippets.check({}) is None


# --- the loop's observations -------------------------------------------------------


class _Nats:
    """NATS for the loop: the daemon's real ``workspace.related``, a scripted ``workspace.execute``."""

    def __init__(self, execute: dict | None = None):
        self.execute = execute or {}
        self.calls: list[tuple[str, dict]] = []

    async def request(self, subject, payload, timeout=None):
        self.calls.append((subject, payload))
        if subject == "workspace.related":
            return daemon_graph.related(payload["workspace_root"], payload)
        assert subject == "workspace.execute"
        return self.execute


@pytest.fixture()
def project(tmp_path, monkeypatch):
    monkeypatch.setenv("FABRIQUE_MAP_STATE", str(tmp_path / "map-state"))
    root = tmp_path / "proj"
    (root / "pkg").mkdir(parents=True)
    (root / "web").mkdir()
    (root / "pkg" / "__init__.py").write_text("")
    (root / "pkg" / "db.py").write_text("def connect():\n    return 1\n")
    (root / "pkg" / "store.py").write_text("class Store:\n    pass\n")
    (root / "pkg" / "api.py").write_text("def handle_request(key):\n    return key\n")
    (root / "web" / "util.ts").write_text(
        "export function formatName(n: string) {\n  return n;\n}\n"
    )
    return root


@pytest.fixture()
def announced():
    daemon_liveness.reset()
    daemon_liveness.observe_heartbeat(
        {"daemon_id": "d1", "abilities": abilities.announcement()}
    )
    yield
    daemon_liveness.reset()


def _with_profile(monkeypatch, profile: dict):
    async def _profile(self):
        return profile

    async def _box(ctx):
        return {"mode": "container"}, None

    monkeypatch.setattr(settings, "DELIVERY_TESTS_ENABLED", True)
    monkeypatch.setattr(Context, "profile", _profile)
    monkeypatch.setattr(delivery_commands, "sandbox_box", _box)


def _observe(nats, evidence, declared):
    asyncio.run(
        docs_checks.observe(nats, {"id": 1, "company_id": 1}, evidence, declared)
    )


def test_the_loop_looks_up_snippet_names_and_runs_the_build(
    project, announced, monkeypatch
):
    _with_profile(
        monkeypatch,
        {"docs_build": "python -m mkdocs build", "source": {"docs_build": "detected"}},
    )
    nats = _Nats(
        {
            "exit_code": 2,
            "stdout": "INFO start\n",
            "stderr": "ERROR - nav entry missing.md\n",
        }
    )
    evidence = _evidence({"docs/guide.md": GUIDE}, TREE, str(project))
    _observe(nats, evidence, [{"path": "docs/guide.md", "content": GUIDE}])
    names = {r["name"]: r for r in evidence["docs_snippets"]["names"]}
    assert (
        names["Store"]["found"] is True
        and names["Store"]["files"][0]["path"] == "pkg/store.py"
    )
    assert len(names["Store"]["files"][0]["sha256"]) == 64
    assert (
        names["open_store"]["found"] is False
        and names["missingHelper"]["found"] is False
    )
    assert (
        names["handle_request"]["found"] is True
        and names["formatName"]["found"] is True
    )
    check = doc_snippets.check(evidence)
    assert check["outcome"] == "pass" and len(check["findings"]) == 2
    build = evidence["docs_build"]
    output = "INFO start\nERROR - nav entry missing.md\n"
    assert build["exit_code"] == 2 and build["output_sha256"] == _sha(output)
    assert (
        build["errors"] == ["ERROR - nav entry missing.md"]
        and build["source"] == "detected"
    )
    [execute] = [p for s, p in nats.calls if s == "workspace.execute"]
    assert execute["command"] == "python -m mkdocs build" and execute["sandbox"] == {
        "mode": "container"
    }
    assert doc_build.check(evidence)["outcome"] == "refuted"


def test_a_passing_build_and_no_command_at_all(project, announced, monkeypatch):
    evidence = _evidence({"README.md": "# Hi\n"}, ["README.md"], str(project))
    _with_profile(monkeypatch, {"docs_build": "python -m mkdocs build"})
    _observe(_Nats({"exit_code": 0, "stdout": "INFO done\n"}), evidence, [])
    assert doc_build.check(evidence)["outcome"] == "pass"
    assert "docs_snippets" not in evidence  # no snippet, no lookup
    evidence = _evidence({"README.md": "# Hi\n"}, ["README.md"], str(project))
    _with_profile(monkeypatch, {"docs_build": None})
    nats = _Nats()
    _observe(nats, evidence, [])
    assert evidence["docs_build"]["applicable"] is False
    assert doc_build.check(evidence) is None and nats.calls == []
    # Commands off for the deployment: applicable, not run, unverifiable.
    monkeypatch.setattr(settings, "DELIVERY_TESTS_ENABLED", False)
    _with_profile(monkeypatch, {"docs_build": "python -m mkdocs build"})
    monkeypatch.setattr(settings, "DELIVERY_TESTS_ENABLED", False)
    evidence = _evidence({"README.md": "# Hi\n"}, ["README.md"], str(project))
    _observe(nats, evidence, [])
    assert doc_build.check(evidence)["outcome"] == "unverifiable" and nats.calls == []


def test_a_delivery_without_documentation_observes_nothing(
    project, announced, monkeypatch
):
    _with_profile(monkeypatch, {"docs_build": "python -m mkdocs build"})
    evidence = _evidence({"pkg/new.py": "x = 1\n"}, TREE, str(project))
    nats = _Nats()
    _observe(nats, evidence, [])
    assert "docs_build" not in evidence and nats.calls == []


def test_unannounced_lookup_leaves_the_names_unverifiable(project, monkeypatch):
    daemon_liveness.reset()
    old = abilities.announcement()
    old["features"]["workspace.related"] = []
    daemon_liveness.observe_heartbeat({"daemon_id": "d1", "abilities": old})
    try:
        _with_profile(monkeypatch, {"docs_build": None})
        evidence = _evidence({"docs/guide.md": GUIDE}, TREE, str(project))
        nats = _Nats()
        _observe(nats, evidence, [{"path": "docs/guide.md", "content": GUIDE}])
    finally:
        daemon_liveness.reset()
    assert evidence["docs_snippets"]["observed"] is False
    assert doc_snippets.check(evidence)["outcome"] == "unverifiable"
    assert not [s for s, _ in nats.calls if s == "workspace.related"]
