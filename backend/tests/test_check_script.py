"""The one command for every check (``scripts/check.sh``).

The script is bash; these tests parse it with ``bash -n`` and run its modes
with ``CHECK_DRY_RUN=1``, which prints the steps a mode would run and runs
none of them: ``quick`` picks its steps from the changed files
(``CHECK_FILES`` stands in for git here), ``full`` lists everything CI runs,
and an unknown mode is refused.
"""

from __future__ import annotations

import os
import shutil
import subprocess
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parents[2]
SCRIPT = REPO / "scripts" / "check.sh"

pytestmark = pytest.mark.skipif(shutil.which("bash") is None, reason="needs bash")

RULE_STEPS = [
    "backend dependency rule",
    "frontend layer rule",
    "file sizes",
    "changelog fragments",
]


def _run(*args: str, **env: str) -> subprocess.CompletedProcess[str]:
    environment = {**os.environ, "CHECK_DRY_RUN": "1", **env}
    environment.pop("CHECK_BASE", None)
    return subprocess.run(
        ["bash", str(SCRIPT), *args],
        cwd=REPO,
        env=environment,
        capture_output=True,
        text=True,
        timeout=60,
        check=False,
    )


def _steps(output: str) -> list[str]:
    return [
        line.split("]", 1)[1].split(":", 1)[0].strip()
        for line in output.splitlines()
        if line.startswith("[dry-run]")
    ]


def test_the_script_parses():
    result = subprocess.run(
        ["bash", "-n", str(SCRIPT)], capture_output=True, text=True, check=False
    )
    assert result.returncode == 0, result.stderr


def test_an_unknown_or_missing_mode_is_refused():
    for args in (("everything",), ()):
        result = _run(*args)
        assert result.returncode == 2
        assert "check.sh quick" in result.stderr


def test_help_names_both_modes():
    result = _run("--help")
    assert result.returncode == 0
    assert "check.sh quick" in result.stdout and "check.sh full" in result.stdout


def test_quick_with_nothing_changed_runs_only_the_rule_checks():
    result = _run("quick", CHECK_FILES="")
    assert result.returncode == 0, result.stderr
    assert _steps(result.stdout) == RULE_STEPS


def test_quick_runs_the_backend_steps_for_a_backend_change():
    result = _run(
        "quick", CHECK_FILES="backend/app/main.py backend/tests/test_check_script.py"
    )
    assert result.returncode == 0, result.stderr
    steps = _steps(result.stdout)
    assert steps == [
        "backend affected tests",
        "ruff check (changed)",
        "ruff format (changed)",
        *RULE_STEPS,
    ]
    assert "affected_tests.py --files backend/app/main.py" in result.stdout
    assert "--run -- -n 0" in result.stdout
    assert "check app/main.py tests/test_check_script.py" in result.stdout


def test_quick_runs_the_frontend_steps_for_a_frontend_change():
    result = _run("quick", CHECK_FILES="frontend/src/App.tsx frontend/package.json")
    assert result.returncode == 0, result.stderr
    steps = _steps(result.stdout)
    assert steps == ["frontend changed tests", "eslint (changed)", "tsc", *RULE_STEPS]
    assert "npm run test:changed" in result.stdout
    assert "npx eslint src/App.tsx\n" in result.stdout


def test_quick_lints_the_workflows_when_one_changed():
    result = _run("quick", CHECK_FILES=".github/workflows/e2e.yml")
    steps = _steps(result.stdout) + [
        line for line in result.stdout.splitlines() if "actionlint: skipped" in line
    ]
    assert any("actionlint" in step for step in steps)


def test_full_lists_everything_ci_runs_with_the_workers_asked_for():
    result = _run("full", CHECK_BACKEND_WORKERS="2")
    assert result.returncode == 0, result.stderr
    steps = _steps(result.stdout)
    for step in (
        "backend tests (-n 2)",
        "ruff check",
        "ruff format",
        "llm-proxy tests",
        "tsc",
        "eslint",
        "frontend tests",
        "frontend build",
        *RULE_STEPS,
    ):
        assert step in steps, (step, steps)
    assert "-n 2 --dist loadgroup" in result.stdout
