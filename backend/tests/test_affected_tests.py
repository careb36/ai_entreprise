"""The tests a change can break (``scripts/affected_tests.py``).

The script reads the test map in ``docs/architecture/contexts.json`` (each
test file and the contexts it exercises) and the contexts' dependency rule,
and prints the test files a set of changed files can break. These tests run
it on small maps and test folders of their own: a change in one context, a
change in a context others import reaching their tests, a test-only change
and the tests that import it, ``conftest.py`` and an unknown file falling back
to the whole suite, the files a rule names and the tests that name a script;
and on the repository's own map through ``--files`` and ``--run``.
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
    result = _run("--files", "backend/app/services/management/manager_agent.py")
    assert result.returncode == 0, result.stderr
    printed = {line.rsplit("/", 1)[-1] for line in result.stdout.split()}
    management = {n for n, ctxs in files.items() if "management" in ctxs}
    assert management and management <= printed
    assert set(data["tests"]["always"]) - {"$comment"} <= printed
    assert "test_board.py" not in printed  # a tasks test: management is above
    assert "context management" in result.stderr

    everything = _run("--files", "backend/tests/conftest.py")
    assert everything.stdout.split() == ["backend/tests"]
    assert "The whole suite: backend/tests/conftest.py" in everything.stderr


def test_run_hands_the_selection_and_extra_arguments_to_pytest(monkeypatch, capsys):
    calls = []

    def fake_run(command, cwd, env, check):
        calls.append((command, cwd, env["PYTHONPATH"]))
        return subprocess.CompletedProcess(command, 0)

    monkeypatch.setattr(affected_tests.subprocess, "run", fake_run)
    code = affected_tests.main(
        ["--files", "backend/tests/test_board.py", "--run", "--", "-q", "-x"]
    )
    assert code == 0
    command, cwd, pythonpath = calls[0]
    assert command[:3] == [sys.executable, "-m", "pytest"]
    assert "tests/test_board.py" in command
    assert command[-2:] == ["-q", "-x"]
    assert cwd == REPO / "backend" and pythonpath == ".:app"
    assert "backend/tests/test_board.py" in capsys.readouterr().out
