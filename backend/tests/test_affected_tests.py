"""The tests a change can break (``scripts/affected_tests.py``).

The script reads the test map in ``docs/architecture/contexts.json`` (each
test file and the contexts it exercises) and the contexts' dependency rule,
and prints the test files a set of changed files can break. These tests run
it on small maps and test folders of their own: a change in one context, a
change in a context others import reaching their tests, a test-only change
and the tests that import it, ``conftest.py`` and an unknown file falling back
to the whole suite, the files a rule names and the tests that name a script;
and on the repository's own map through ``--files`` and ``--run``.

By module (``scripts/affected_graph.py``, ``scripts/affected_modules.py``),
they build the import graph of a small repository of their own: the kinds of
import, patch strings, the routes a test calls, conftest's fixtures and the
events between modules, a body change against a top-level one, and the
fallbacks to the context rules.
"""

from __future__ import annotations

import importlib.util
import subprocess
import sys
from pathlib import Path

REPO = Path(__file__).resolve().parents[2]
SCRIPT = REPO / "scripts" / "affected_tests.py"
_spec = importlib.util.spec_from_file_location("affected_tests", SCRIPT)
assert _spec and _spec.loader
affected_tests = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(affected_tests)

CONTEXTS = {
    "contracts": {"depends_on": []},
    "platform": {"depends_on": ["contracts"]},
    "evidence": {"depends_on": ["contracts", "platform"]},
    "loop": {"depends_on": ["contracts", "platform", "evidence"]},
}
FILES = {
    "test_shapes.py": ["contracts"],
    "test_audit.py": ["platform"],
    "test_verify.py": ["evidence"],
    "test_worker.py": ["loop"],
    "test_script.py": ["repo"],
}
ALWAYS = {"test_rule.py": "reads every module"}
SOURCES = {
    "scripts/": "mentions",
    ".github/": ["mentions", "test_audit.py"],
    "backend/app/models/": "all",
}


def _data() -> dict:
    return {
        "contexts": CONTEXTS,
        "tests": {"sources": SOURCES, "always": ALWAYS, "files": FILES},
    }


def _tests(tmp_path: Path) -> Path:
    tests = tmp_path / "tests"
    tests.mkdir(parents=True)
    for name in [*FILES, *ALWAYS]:
        (tests / name).write_text("def test_x():\n    pass\n")
    (tests / "conftest.py").write_text("")
    # test_worker imports a helper and test_verify; the helper is no test.
    (tests / "fakes.py").write_text("FAKE = 1\n")
    (tests / "test_worker.py").write_text("import fakes\nfrom test_verify import x\n")
    (tests / "test_script.py").write_text('SCRIPT = "scripts/release_notes.py"\n')
    return tests


def _select(tmp_path: Path, *changed: str) -> dict:
    return affected_tests.select(list(changed), _data(), _tests(tmp_path))


def test_a_change_in_one_context_runs_its_tests_and_the_always_set(tmp_path):
    result = _select(tmp_path, "backend/app/services/loop/task_worker.py")
    assert result["everything"] is None
    # Nothing may import loop here, so only loop's tests and the always set.
    assert result["tests"] == ["test_rule.py", "test_worker.py"]


def test_a_change_below_reaches_every_context_that_may_import_it(tmp_path):
    evidence = _select(tmp_path / "e", "backend/app/services/evidence/risk.py")
    assert evidence["tests"] == ["test_rule.py", "test_verify.py", "test_worker.py"]
    platform = _select(tmp_path / "p", "backend/app/services/platform/audit.py")
    assert platform["tests"] == [
        "test_audit.py",
        "test_rule.py",
        "test_verify.py",
        "test_worker.py",
    ]
    assert any(
        "contexts reached: evidence, loop, platform" in n for n in platform["notes"]
    )


def test_a_test_only_change_runs_it_and_the_tests_that_import_it(tmp_path):
    alone = _select(tmp_path / "a", "backend/tests/test_audit.py")
    assert alone["tests"] == ["test_audit.py", "test_rule.py"]
    imported = _select(tmp_path / "b", "backend/tests/test_verify.py")
    assert imported["tests"] == ["test_rule.py", "test_verify.py", "test_worker.py"]
    helper = _select(tmp_path / "c", "backend/tests/fakes.py")
    assert helper["tests"] == ["test_rule.py", "test_worker.py"]
    # A deleted test file is not handed to pytest.
    gone = _select(tmp_path / "d", "backend/tests/test_gone.py")
    assert gone["tests"] == ["test_rule.py"]


def test_conftest_and_shared_files_fall_back_to_the_whole_suite(tmp_path):
    result = _select(tmp_path / "a", "backend/tests/conftest.py")
    assert result["everything"] == "backend/tests/conftest.py: shared by every test"
    models = _select(tmp_path / "b", "backend/app/models/sprint.py")
    assert models["everything"] == (
        "backend/app/models/sprint.py: shared by every context"
    )


def test_an_unknown_file_falls_back_to_the_whole_suite(tmp_path):
    result = _select(tmp_path, "somewhere/new.py", "backend/app/services/loop/x.py")
    assert result["everything"] == "somewhere/new.py: outside every context and rule"


def test_mentions_and_named_tests(tmp_path):
    script = _select(tmp_path / "a", "scripts/release_notes.py")
    assert script["tests"] == ["test_rule.py", "test_script.py"]
    workflow = _select(tmp_path / "b", ".github/workflows/release.yml")
    assert workflow["tests"] == ["test_audit.py", "test_rule.py"]


def test_the_reverse_of_the_rule_and_the_top_of_a_set():
    assert affected_tests.dependants(CONTEXTS, {"evidence"}) == {"evidence", "loop"}
    assert affected_tests.top(CONTEXTS, {"platform", "loop", "contracts"}) == ["loop"]
    assert affected_tests.top(CONTEXTS, {"platform"}) == ["platform"]


def test_suggest_reads_a_helper_s_imports_and_whole_f_strings(tmp_path):
    (tmp_path / "factories.py").write_text("from services.tasks import events\n")
    (tmp_path / "test_other.py").write_text("from services.loop import task_worker\n")
    test = tmp_path / "test_new.py"
    test.write_text(
        "import factories\n"
        "from test_other import HELPER\n"
        'URL = f"/v1/sprints/{1}/events"\n'
    )
    # The piece "/v1/sprints/" is not a call to the sprint routes.
    assert affected_tests.urls_in(test) == {"/v1/sprints/X/events"}
    # A helper module is followed, another test file is not.
    assert affected_tests._with_helpers(test) == [test, tmp_path / "factories.py"]
    assert affected_tests.suggest(test, affected_tests.load()) == ["tasks"]


def _run(*args: str) -> subprocess.CompletedProcess:
    return subprocess.run(
        [sys.executable, "-I", str(SCRIPT), *args],
        check=False,
        capture_output=True,
        text=True,
    )


def test_files_on_the_repository_map():
    data = affected_tests.load()
    files = data["tests"]["files"]
    result = _run(
        "--by", "context", "--files", "backend/app/services/management/manager_agent.py"
    )
    assert result.returncode == 0, result.stderr
    printed = {line.rsplit("/", 1)[-1] for line in result.stdout.split()}
    management = {n for n, ctxs in files.items() if "management" in ctxs}
    assert management and management <= printed
    assert set(data["tests"]["always"]) - {"$comment"} <= printed
    assert "test_board.py" not in printed  # a tasks test: management is above
    assert "context management" in result.stderr

    everything = _run("--by", "context", "--files", "backend/tests/conftest.py")
    assert everything.stdout.split() == ["backend/tests"]
    assert "The whole suite: backend/tests/conftest.py" in everything.stderr


def test_run_hands_the_selection_and_extra_arguments_to_pytest(monkeypatch, capsys):
    calls = []

    def fake_run(command, cwd, env, check):
        calls.append((command, cwd, env["PYTHONPATH"]))
        return subprocess.CompletedProcess(command, 0)

    monkeypatch.setattr(affected_tests.subprocess, "run", fake_run)
    code = affected_tests.main(
        ["--by", "context", "--files", "backend/tests/test_board.py", "--run"]
        + ["--", "-q", "-x"]
    )
    assert code == 0
    command, cwd, pythonpath = calls[0]
    assert command[:3] == [sys.executable, "-m", "pytest"]
    assert "tests/test_board.py" in command
    assert command[-2:] == ["-q", "-x"]
    assert cwd == REPO / "backend" and pythonpath == ".:app"
    assert "backend/tests/test_board.py" in capsys.readouterr().out


# --- by module (scripts/affected_graph.py, scripts/affected_modules.py) -------

# A small repository: a module several reach, a lazy import, a relative one, a
# re-exporting package, two routers, main and lifecycle, an event and its
# handler, and conftest wiring them in.
FAKE_REPO = {
    "backend/app/services/__init__.py": "",
    "backend/app/services/evidence/__init__.py": "",
    "backend/app/services/evidence/verification.py": (
        "import re\n\n\ndef check():\n    return 1\n\n\nLIMIT = 3\n"
    ),
    "backend/app/services/loop/__init__.py": "",
    "backend/app/services/loop/worker.py": (
        "from services.evidence import verification\n\n\n"
        "def run():\n    return verification.check()\n"
    ),
    "backend/app/services/loop/relative.py": "from . import worker\n",
    "backend/app/services/loop/lazy.py": (
        "def f():\n    from services.evidence.verification import check\n"
        "    return check()\n"
    ),
    "backend/app/services/loop/close.py": (
        "from services.platform import hooks\n\n\ndef close():\n"
        '    hooks.emit("sprint.closed")\n'
    ),
    "backend/app/services/platform/__init__.py": "",
    "backend/app/services/platform/hooks.py": (
        "def on(event, handler):\n    pass\n\n\ndef emit(event):\n    pass\n"
    ),
    "backend/app/services/release/__init__.py": "",
    "backend/app/services/release/integration.py": (
        "from services.loop import worker\n\n\ndef integrate():\n"
        "    return worker.run()\n"
    ),
    "backend/app/services/release/jobs.py": (
        "from services.platform import hooks\n"
        "from services.release import integration\n\n\n"
        "def handler():\n    integration.integrate()\n\n\n"
        "def install():\n"
        '    hooks.on("sprint.closed", handler)\n'
    ),
    "backend/app/routers/__init__.py": (
        "from routers.items import router as items_router\n"
        "from routers.other import router as other_router\n"
    ),
    "backend/app/routers/items.py": (
        "from fastapi import APIRouter\nfrom services.loop import worker\n\n"
        'router = APIRouter(prefix="/v1")\n\n\n'
        '@router.get("/items/{item_id}")\ndef get(item_id):\n'
        "    return worker.run()\n"
    ),
    "backend/app/routers/other.py": (
        "from fastapi import APIRouter\n\n"
        'router = APIRouter(prefix="/v1")\n\n\n'
        '@router.get("/other")\ndef other():\n    return 2\n'
    ),
    "backend/app/lifecycle.py": "from services.loop import worker\n",
    "backend/app/main.py": (
        "import lifecycle\nfrom routers import items_router, other_router\n"
    ),
    "backend/app/models/__init__.py": "",
    "backend/app/data/names.json": "{}\n",
    "backend/tests/conftest.py": (
        "import pytest\n\n\n@pytest.fixture(autouse=True)\ndef wire():\n"
        "    from services.release import jobs\n\n    jobs.install()\n\n\n"
        "@pytest.fixture()\ndef test_client():\n    from app.main import app\n\n"
        "    yield app\n"
    ),
    "backend/tests/helper.py": "from services.loop import relative\n",
    "backend/tests/test_verify.py": (
        "from services.evidence.verification import check\n"
    ),
    "backend/tests/test_patch.py": (
        'from unittest import mock\n\nTARGET = mock.patch("services.loop.lazy.f")\n'
    ),
    "backend/tests/test_api.py": (
        'def test_x(test_client):\n    test_client.get(f"/v1/items/{1}")\n'
    ),
    "backend/tests/test_other_api.py": (
        'def test_y(test_client):\n    test_client.get("/v1/other")\n'
    ),
    "backend/tests/test_close.py": "from services.loop.close import close\n",
    "backend/tests/test_helper_user.py": "import helper\n",
    "backend/tests/test_plain.py": "def test_z():\n    pass\n",
    "backend/tests/test_walks.py": (
        'from pathlib import Path\n\nFILES = list(Path("services").rglob("*.py"))\n'
    ),
}
FAKE_DATA = {
    "contexts": {
        "platform": {"depends_on": []},
        "evidence": {"depends_on": ["platform"]},
        "loop": {"depends_on": ["platform", "evidence"]},
        "release": {"depends_on": ["platform", "evidence", "loop"]},
    },
    "tests": {
        "sources": {
            "backend/app/": "imports",
            "backend/app/models/": "all",
            "backend/app/data/": "loop",
        },
        "always": {"test_plain.py": "always"},
        "files": {"test_close.py": ["loop"], "test_verify.py": ["evidence"]},
    },
}
EVERY_FAKE_TEST = sorted(n.rsplit("/", 1)[-1] for n in FAKE_REPO if "/test_" in n)


def _fake_repo(root: Path) -> Path:
    for rel, text in FAKE_REPO.items():
        (root / rel).parent.mkdir(parents=True, exist_ok=True)
        (root / rel).write_text(text)
    return root


def _by_module(root: Path, *changed: str, lines=None, assume=None) -> dict:
    graph = affected_tests._sibling("affected_graph").build(root)
    return affected_tests.select(
        list(changed),
        FAKE_DATA,
        root / "backend" / "tests",
        by="module",
        lines=lines or {},
        graph=graph,
        assume=assume,
    )


VERIFY = "backend/app/services/evidence/verification.py"


def test_the_graph_reads_every_kind_of_import(tmp_path):
    graph = affected_tests._sibling("affected_graph").build(_fake_repo(tmp_path))
    uses = graph.uses
    app = "backend/app/services/"
    assert VERIFY in uses[app + "loop/worker.py"]  # from package import module
    assert VERIFY in uses[app + "loop/lazy.py"]  # inside a function
    assert app + "loop/worker.py" in uses[app + "loop/relative.py"]  # relative
    assert app + "loop/lazy.py" in uses["backend/tests/test_patch.py"]  # patch
    assert app + "loop/relative.py" in uses["backend/tests/helper.py"]
    assert "backend/tests/helper.py" in uses["backend/tests/test_helper_user.py"]
    # A re-export: main uses the router module and only loads the package.
    assert "backend/app/routers/__init__.py" in graph.loads["backend/app/main.py"]
    # main mounts the routers: a load, not a use.
    assert "backend/app/routers/items.py" not in uses["backend/app/main.py"]
    assert "backend/app/routers/items.py" in graph.loads["backend/app/main.py"]
    # The emitter uses the module that registers the handler.
    assert app + "release/jobs.py" in uses[app + "loop/close.py"]
    # A URL reaches its router; the test_client fixture reaches main.
    assert "backend/app/routers/items.py" in uses["backend/tests/test_api.py"]
    assert "backend/app/routers/other.py" not in uses["backend/tests/test_api.py"]
    fixture = "backend/tests/conftest.py::test_client"
    assert fixture in uses["backend/tests/test_api.py"]
    assert "backend/app/main.py" in uses[fixture]
    assert app + "release/jobs.py" in uses["backend/tests/conftest.py"]


def test_a_body_change_selects_the_tests_that_use_the_module(tmp_path):
    root = _fake_repo(tmp_path)
    result = _by_module(root, VERIFY, lines={VERIFY: {5}})
    assert result["everything"] is None
    assert [g.kind for g in result["groups"]] == ["body"]
    # test_close: closing runs the handler, which reaches verification. Not
    # test_other_api: main reaches worker only through lifecycle (wiring).
    assert result["tests"] == [
        "test_api.py",
        "test_close.py",
        "test_helper_user.py",
        "test_patch.py",
        "test_plain.py",
        "test_verify.py",
        "test_walks.py",
    ]
    reasons = result["groups"][0].tests
    assert (
        "routers/items.py (calls /v1/items/{})" in reasons["backend/tests/test_api.py"]
    )
    assert "walks a folder" in reasons["backend/tests/test_walks.py"]


def test_a_top_level_change_selects_every_test_that_loads_the_module(tmp_path):
    root = _fake_repo(tmp_path)
    # Line 8 (``LIMIT = 3``) is outside every function body; conftest's autouse
    # fixture loads jobs -> integration -> worker -> verification for every test.
    top = _by_module(root, VERIFY, lines={VERIFY: {8}})
    assert [g.kind for g in top["groups"]] == ["top"]
    assert top["tests"] == EVERY_FAKE_TEST
    # Without line information the change is read as top-level, unless told.
    assert _by_module(root, VERIFY)["tests"] == EVERY_FAKE_TEST
    assert "test_other_api.py" not in _by_module(root, VERIFY, assume="body")["tests"]


def test_a_module_a_fixture_acts_on_once_loaded_is_no_edge(tmp_path):
    root = _fake_repo(tmp_path)
    (root / "backend/tests/conftest.py").write_text(
        "import pytest\n\n\ndef _install(jobs):\n    jobs.install()\n\n\n"
        "@pytest.fixture(autouse=True)\ndef wire():\n"
        '    when_loaded("services.release.jobs", _install)\n'
    )
    # Only the tests that reach verification themselves (an import, a URL), and
    # the ones that emit the event jobs handles (``hooks.emit`` to ``hooks.on``).
    assert _by_module(root, VERIFY, assume="top")["tests"] == [
        "test_api.py",
        "test_close.py",
        "test_helper_user.py",
        "test_patch.py",
        "test_plain.py",
        "test_verify.py",
        "test_walks.py",
    ]


def test_the_router_rule_and_the_wiring(tmp_path):
    root = _fake_repo(tmp_path)
    items = _by_module(root, "backend/app/routers/items.py", assume="body")
    assert items["tests"] == ["test_api.py", "test_plain.py"]
    other = _by_module(root, "backend/app/routers/other.py", assume="body")
    assert other["tests"] == ["test_other_api.py", "test_plain.py"]
    # lifecycle is imported by main itself: every test that builds the app.
    life = _by_module(root, "backend/app/lifecycle.py", assume="body")
    assert life["tests"] == ["test_api.py", "test_other_api.py", "test_plain.py"]
    # conftest calls jobs on every test: a change to it selects them all.
    jobs = _by_module(root, "backend/app/services/release/jobs.py", assume="body")
    assert jobs["tests"] == EVERY_FAKE_TEST
    # A handler's dependency reaches the emitter's tests, not every test.
    integration = "backend/app/services/release/integration.py"
    assert _by_module(root, integration, assume="body")["tests"] == [
        "test_close.py",
        "test_plain.py",
        "test_walks.py",
    ]


def test_a_changed_helper_or_test_selects_itself_and_its_importers(tmp_path):
    root = _fake_repo(tmp_path)
    helper = _by_module(root, "backend/tests/helper.py")
    assert helper["tests"] == ["test_helper_user.py", "test_plain.py"]
    alone = _by_module(root, "backend/tests/test_verify.py")
    assert alone["tests"] == ["test_plain.py", "test_verify.py"]


def test_the_module_fallbacks(tmp_path):
    root = _fake_repo(tmp_path)
    conftest = _by_module(root, "backend/tests/conftest.py")
    assert conftest["everything"] == "backend/tests/conftest.py: shared by every test"
    models = _by_module(root, "backend/app/models/__init__.py")
    assert models["everything"].endswith("shared by every context")
    unknown = _by_module(root, "somewhere/else.txt")
    assert unknown["everything"] == "somewhere/else.txt: outside every context and rule"
    # A data file is not a module: its context rule, with the reason.
    data = _by_module(root, "backend/app/data/names.json")
    assert data["tests"] == ["test_close.py", "test_plain.py"]
    assert any("not a Python module of the graph" in n for n in data["notes"])
    # A deleted module: nothing in the graph imports it any more.
    gone = _by_module(root, "backend/app/services/loop/gone.py")
    assert any("deleted" in n for n in gone["notes"])
    # A module imported by a computed name sends every module to its context.
    hooks = "backend/app/services/platform/hooks.py"
    assert "test_verify.py" not in _by_module(root, hooks, assume="body")["tests"]
    (root / "backend/app/services/loop/plugins.py").write_text(
        "import importlib\n\n\ndef load(name):\n"
        "    return importlib.import_module(name)\n"
    )
    opaque = _by_module(root, hooks, assume="body")
    assert "test_verify.py" in opaque["tests"]  # platform's dependants, by the rule
    assert any("computed name" in n for n in opaque["notes"])


def test_changed_lines_and_their_kind(tmp_path):
    modules = affected_tests._sibling("affected_modules")
    diff = (
        "diff --git a/x.py b/x.py\n--- a/x.py\n+++ b/x.py\n"
        "@@ -3,0 +4,2 @@ def f():\n+    a = 1\n+    b = 2\n"
        "@@ -9 +10,0 @@\n-gone = 1\n"
        "+++ /dev/null\n"
    )
    assert modules.lines_from_diff(diff) == {"x.py": {4, 5, 10, 11}}
    source = tmp_path / "m.py"
    source.write_text(
        "import os\n\n\n@decorate\ndef f(a=os.sep):\n    return a\n\n\nX = 1\n"
    )
    assert modules.classify(source, {6})[0] == "body"
    assert modules.classify(source, {4})[0] == "top"  # the decorator
    assert modules.classify(source, {5})[0] == "top"  # the signature's default
    assert modules.classify(source, {1})[0] == "top"  # an import
    assert modules.classify(source, None)[0] == "top"
    source.write_text("def broken(:\n")
    assert modules.classify(source, {1}) == ("top", "cannot be parsed (SyntaxError)")


def test_the_router_matching():
    graph_module = affected_tests._sibling("affected_graph")
    table = {
        "r/items.py": [["v1", "items", "{item_id}"], ["v1", "items"]],
        "r/teams.py": [["v1", "companies", "{cid}", "teams"]],
    }
    hole = graph_module._HOLE
    assert graph_module.routers_for("/v1/items/7", table) == {"r/items.py"}
    assert graph_module.routers_for(f"/v1/companies/{hole}/teams", table) == {
        "r/teams.py"
    }
    # A leading part (a base the test adds to) matches every route it starts.
    assert graph_module.routers_for("/v1/", table) == {"r/items.py", "r/teams.py"}
    assert graph_module.routers_for("/v1/items/7/extra", table) == set()
    assert graph_module.routers_for("/health", table) == set()


def test_by_module_on_the_repository():
    result = _run(
        "--files", "backend/app/routers/webhooks.py", "--assume", "body", "--explain"
    )
    assert result.returncode == 0, result.stderr
    printed = {line.rsplit("/", 1)[-1] for line in result.stdout.split()}
    assert "test_webhooks.py" in printed
    assert "test_board.py" not in printed
    assert "body change" in result.stderr
    assert "calls /v1/" in result.stderr  # --explain names the route
    by_context = _run("--by", "context", "--files", "backend/app/routers/webhooks.py")
    context_printed = {line.rsplit("/", 1)[-1] for line in by_context.stdout.split()}
    assert len(printed) < len(context_printed)
    # A test that walks every route runs for any router; the contexts miss it.
    assert "test_mass_assignment.py" in printed - context_printed
