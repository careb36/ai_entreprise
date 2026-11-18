"""Which CI jobs a pull request runs, and how many e2e jobs share the machine.

Seven self-hosted runners share one machine of about 4 cores and 16 GB. A
pull request runs only the jobs its files can affect: ``changes.yml`` lists
the files once (``scripts/changed_paths.py`` over ``git diff base...head``) and
each workflow gates its job on one of its outputs. These tests hold that design
in place:

- no workflow filters ``on.pull_request`` by path, which would leave a
  required check waiting forever on a pull request that skips it;
- each gated job needs ``changes`` and runs when the filter failed, so a
  broken filter runs too much rather than too little;
- the matrix jobs (the five Docker builds, the two e2e runs) start and skip
  their steps, so each matrix check keeps its name;
- every path a backend test reads is in the backend filter;
- what a docs-only, a frontend-only and a backend-only change runs;
- a superseded run is cancelled (#559) and the e2e job asks for the ``e2e``
  runner label, which bounds how many run at once (docs/OPERATIONS.md,
  Runner capacity);
- the fake-model bench runs on a pull request only when it touches what the
  bench drives or grades (``bench-pr.yml``, with its own filter), as the
  nightly fake run, and the real-model jobs never run on one (#316).
"""

from __future__ import annotations

import importlib.util
import re
from pathlib import Path

import pytest
import yaml

REPO = Path(__file__).resolve().parents[2]
WORKFLOWS = REPO / ".github" / "workflows"
TESTS = Path(__file__).resolve().parent
RUNNER = ["self-hosted", "linux", "x64", "lafabrique"]

_spec = importlib.util.spec_from_file_location(
    "changed_paths", REPO / "scripts" / "changed_paths.py"
)
assert _spec and _spec.loader
changed_paths = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(changed_paths)

# Workflow file -> (gated job, output of changes.yml it reads).
GATED = {
    "backend-tests.yml": ("test", "backend"),
    "frontend-build.yml": ("build", "frontend"),
    "docker-build.yml": ("build", None),  # one output per image, by matrix
    "e2e.yml": ("e2e", "e2e"),
    "file-sizes.yml": ("check", "file-sizes"),
    "workflows.yml": ("lint", "workflows"),
}
DOCKER_IMAGES = ["backend", "janitor", "frontend", "llm-proxy", "vscode-daemon"]
FAIL_OPEN = "needs.changes.result != 'success'"


def _workflow(name: str) -> dict:
    data = yaml.safe_load((WORKFLOWS / name).read_text())
    # YAML 1.1 reads the bare key `on` as the boolean true.
    data["on"] = data.pop(True, data.get("on"))
    return data


def _filters() -> dict[str, list[str]]:
    job = _workflow("changes.yml")["jobs"]["filter"]
    step = next(s for s in job["steps"] if s.get("id") == "filter")
    return changed_paths.parse_filters(step["env"]["FILTERS"])


def _matches(filter_name: str, path: str, filters=None) -> bool:
    filters = filters or _filters()
    return any(changed_paths.glob_regex(p).match(path) for p in filters[filter_name])


def _outputs(files: list[str]) -> set[str]:
    """The outputs changes.yml sets to 'true' for a pull request with ``files``."""
    filters = _filters()
    hit = {name for name in filters if any(_matches(name, f, filters) for f in files)}
    if "workflows" in hit:
        return set(filters)
    return hit


# --- the filter --------------------------------------------------------------------


def test_changes_is_a_reusable_workflow_pinned_by_sha():
    data = _workflow("changes.yml")
    assert set(data["on"]) == {"workflow_call"}
    job = data["jobs"]["filter"]
    assert job["runs-on"] == RUNNER
    # Only a checkout: the filter is a script in this repository, not an action
    # the repository's allowlist has to admit.
    assert job["permissions"] == {"contents": "read"}
    checkout, step = job["steps"]
    assert re.match(r"^actions/checkout@[0-9a-f]{40}$", checkout["uses"])
    assert checkout["if"] == "github.event_name == 'pull_request'"
    assert checkout["with"]["fetch-depth"] == 0
    assert step["id"] == "filter"
    assert step["if"] == "github.event_name == 'pull_request'"
    assert "uses" not in step
    assert step["env"]["BASE_SHA"] == "${{ github.event.pull_request.base.sha }}"
    assert step["env"]["HEAD_SHA"] == "${{ github.event.pull_request.head.sha }}"
    assert "scripts/changed_paths.py" in step["run"]
    assert '"$GITHUB_OUTPUT"' in step["run"]
    assert set(_filters()) == set(job["outputs"])


def test_every_filter_is_an_output_that_is_true_off_pull_requests():
    data = _workflow("changes.yml")
    filters = _filters()
    declared = data["on"]["workflow_call"]["outputs"]
    job_outputs = data["jobs"]["filter"]["outputs"]
    assert set(declared) == set(filters) == set(job_outputs)
    for name, expression in job_outputs.items():
        assert declared[name]["value"] == f"${{{{ jobs.filter.outputs.{name} }}}}"
        # A push to main, a schedule or a manual run runs everything ...
        assert expression.startswith("${{ github.event_name != 'pull_request' || ")
        # ... and so does a pull request that changes a workflow.
        assert "steps.filter.outputs.workflows == 'true'" in expression
        assert f"steps.filter.outputs.{name} == 'true'" in expression


def test_security_scans_are_never_filtered():
    data = _workflow("security-scan.yml")
    assert data["on"]["pull_request"] == {"branches": ["main"]}
    assert "changes" not in data["jobs"]
    for job in data["jobs"].values():
        assert "needs.changes" not in str(job.get("if", ""))


@pytest.mark.parametrize("path", sorted(WORKFLOWS.glob("*.yml")), ids=lambda p: p.name)
def test_no_workflow_filters_its_triggers_by_path(path):
    data = yaml.safe_load(path.read_text())
    data["on"] = data.pop(True, data.get("on"))
    triggers = data["on"] if isinstance(data["on"], dict) else {}
    for event, config in triggers.items():
        config = config or {}
        if isinstance(config, dict):
            assert "paths" not in config, (path.name, event)
            assert "paths-ignore" not in config, (path.name, event)


# --- the callers -------------------------------------------------------------------


@pytest.mark.parametrize("name", sorted(GATED))
def test_each_workflow_gates_its_job_on_the_filter_and_fails_open(name):
    data = _workflow(name)
    changes = data["jobs"]["changes"]
    assert changes["uses"] == "./.github/workflows/changes.yml"
    assert changes["permissions"] == {"contents": "read"}
    job_name, output = GATED[name]
    job = data["jobs"][job_name]
    assert job["needs"] == "changes"
    condition = job["if"]
    assert condition.startswith("${{ !cancelled()")
    if output is not None and "matrix" not in job.get("strategy", {}):
        assert FAIL_OPEN in condition
        assert f"needs.changes.outputs.{output} == 'true'" in condition


def test_each_docker_image_builds_only_when_its_context_changed():
    data = _workflow("docker-build.yml")
    job = data["jobs"]["build"]
    # No job-level filter: a skipped matrix job loses its "build (<image>)" name.
    assert job["if"] == "${{ !cancelled() }}"
    assert job["env"]["BUILD"] == (
        f"${{{{ {FAIL_OPEN} || needs.changes.outputs[matrix.filter] == 'true' }}}}"
    )
    include = job["strategy"]["matrix"]["include"]
    assert [e["name"] for e in include] == DOCKER_IMAGES
    filters = _filters()
    for entry in include:
        assert entry["filter"] == f"docker-{entry['name']}"
        assert entry["filter"] in filters
    first, *rest = job["steps"]
    assert first["if"] == "env.BUILD != 'true'"
    for step in rest:
        assert "env.BUILD == 'true'" in step["if"], step.get("name", step.get("uses"))


def test_each_e2e_run_starts_and_skips_its_steps_when_nothing_changed():
    job = _workflow("e2e.yml")["jobs"]["e2e"]
    assert job["if"] == "${{ !cancelled() }}"
    assert job["env"]["E2E_RUN"] == (
        f"${{{{ {FAIL_OPEN} || needs.changes.outputs.e2e == 'true' }}}}"
    )
    first, *rest = job["steps"]
    assert first["if"] == "env.E2E_RUN != 'true'"
    for step in rest:
        assert "env.E2E_RUN == 'true'" in step["if"], step.get("name", step.get("uses"))


# --- what a change runs ------------------------------------------------------------


def test_a_docs_only_change_runs_nothing_filtered():
    assert _outputs(["docs/TESTING.md", "README.md", "docs/design/X.md"]) == set()


def test_a_changelog_only_change_runs_the_backend_suite_that_reads_it():
    # test_release_notes.py parses CHANGELOG.md, so it is the one doc that
    # starts the backend tests; nothing else runs.
    assert _outputs(["CHANGELOG.md"]) == {"backend"}


def test_a_frontend_only_change():
    assert _outputs(["frontend/src/App.tsx"]) == {
        "frontend",
        "file-sizes",
        "docker-frontend",
    }
    assert _outputs(["docs/architecture/frontend.json"]) == {"frontend", "backend"}


def test_a_backend_only_change():
    assert _outputs(["backend/app/main.py"]) == {
        "backend",
        "e2e",
        "file-sizes",
        "docker-backend",
    }
    assert "docker-janitor" in _outputs(["backend/janitor/main.py"])


def test_daemon_proxy_and_e2e_script_changes():
    assert _outputs(["vscode-daemon/daemon.py"]) == {
        "backend",
        "e2e",
        "file-sizes",
        "docker-vscode-daemon",
    }
    assert _outputs(["llm-proxy/app.py"]) == {
        "backend",
        "e2e",
        "file-sizes",
        "docker-llm-proxy",
    }
    for path in ("scripts/e2e/run.sh", "nats/start.sh", "docker-compose.dev.yml"):
        assert "e2e" in _outputs([path]), path


def test_a_workflow_change_runs_everything():
    assert _outputs([".github/workflows/bench.yml"]) == set(_filters())
    assert _outputs([".github/actionlint.yaml"]) == set(_filters())


_REPO_PATH = re.compile(r'(?:\bREPO(?:_ROOT)?|parents\[2\])((?:\s*/\s*"[^"\n]+")+)')


def _paths_the_backend_tests_read() -> dict[str, str]:
    found = {}
    for test in sorted(TESTS.glob("*.py")):
        for match in _REPO_PATH.finditer(test.read_text()):
            path = "/".join(re.findall(r'"([^"\n]+)"', match.group(1)))
            found.setdefault(path, test.name)
    return found


def test_every_path_a_backend_test_reads_starts_the_backend_tests():
    filters = _filters()
    paths = _paths_the_backend_tests_read()
    assert "CHANGELOG.md" in paths and "vscode-daemon/daemon.py" in paths
    missing = []
    for path, test in paths.items():
        # A folder is covered when a file inside it would be.
        candidates = (path, f"{path}/x")
        if not any(
            _matches(name, c, filters)
            for name in ("backend", "workflows")
            for c in candidates
        ):
            missing.append(f"{path} ({test})")
    assert not missing, "add to the backend filter in changes.yml: " + ", ".join(
        missing
    )


def test_the_glob_reads_like_the_filter():
    glob = changed_paths.glob_regex
    assert glob("backend/**").match("backend/app/main.py")
    assert glob("docker-compose*.yml").match("docker-compose.yml")
    assert not glob("docker-compose*.yml").match("deploy/docker-compose.yml")
    assert not glob("scripts/*.py").match("scripts/e2e/x.py")


# --- capacity ----------------------------------------------------------------------


@pytest.mark.parametrize(
    "name",
    [*sorted(GATED), "security-scan.yml", "bench-pr.yml"],
    ids=lambda n: n.removesuffix(".yml"),
)
def test_a_new_push_cancels_the_superseded_run(name):
    # #559: per ref, so another pull request's run is never cancelled.
    concurrency = _workflow(name)["concurrency"]
    assert concurrency["group"] == f"{name.removesuffix('.yml')}-${{{{ github.ref }}}}"
    assert concurrency["cancel-in-progress"] is True


def test_the_e2e_job_asks_for_the_e2e_label_only_once_the_variable_names_it():
    job = _workflow("e2e.yml")["jobs"]["e2e"]
    runs_on = job["runs-on"]
    assert runs_on == (
        "${{ fromJSON(("
        f"{FAIL_OPEN} || needs.changes.outputs.e2e == 'true') && "
        "(vars.E2E_RUNS_ON || "
        """'["self-hosted","linux","x64","lafabrique"]') || """
        """'["self-hosted","linux","x64","lafabrique"]') }}"""
    )
    labels = yaml.safe_load((REPO / ".github" / "actionlint.yaml").read_text())
    assert "e2e" in labels["self-hosted-runner"]["labels"]
    # Not a global concurrency group: a third arrival would cancel the pending
    # run, which reports as a failed check.
    assert "${{ github.ref }}" in _workflow("e2e.yml")["concurrency"]["group"]
    assert "concurrency" not in job


def test_the_e2e_services_take_an_optional_memory_cap():
    job = _workflow("e2e.yml")["jobs"]["e2e"]
    start = next(s for s in job["steps"] if s.get("name", "").startswith("Start"))
    assert start["env"]["PG_MEMORY"] == "${{ vars.E2E_POSTGRES_MEMORY }}"
    assert start["env"]["NATS_MEMORY"] == "${{ vars.E2E_NATS_MEMORY }}"
    assert '"${pg_limits[@]}"' in start["run"]
    assert '"${nats_limits[@]}"' in start["run"]


def test_the_backend_suite_runs_four_workers_unless_told_otherwise():
    job = _workflow("backend-tests.yml")["jobs"]["test"]
    step = next(s for s in job["steps"] if s.get("name") == "Run tests")
    assert step["env"]["WORKERS"] == "${{ vars.BACKEND_TEST_WORKERS || '4' }}"
    assert '-n "$WORKERS" --dist loadgroup' in step["run"]


# --- the bench on pull requests (#316) ---------------------------------------------

BENCH_GATE = (
    "${{ !cancelled() && (needs.changes.result != 'success' "
    "|| needs.changes.outputs.bench == 'true') }}"
)
# The steps bench-pr.yml shares with bench.yml's fake job, held equal.
BENCH_SHARED_STEPS = [
    "Start Postgres, NATS and pick free ports",
    "Set up Python 3.12 (uv)",
    "Install the backend",
    "Run the bench (fake LLM)",
    "Compare with the previous scorecard",
    "Stop the containers",
]


def _bench_filter() -> list[str]:
    job = _workflow("bench-pr.yml")["jobs"]["changes"]
    step = next(s for s in job["steps"] if s.get("id") == "filter")
    filters = changed_paths.parse_filters(step["env"]["FILTERS"])
    assert set(filters) == {"bench"}
    return filters["bench"]


def _bench_runs_for(files: list[str]) -> bool:
    globs = _bench_filter()
    return any(changed_paths.glob_regex(g).match(f) for g in globs for f in files)


def _named_steps(job: dict) -> dict[str, dict]:
    return {s["name"]: s for s in job["steps"] if "name" in s}


def test_the_pull_request_bench_runs_only_on_pull_requests_into_main():
    data = _workflow("bench-pr.yml")
    assert data["on"] == {"pull_request": {"branches": ["main"]}}
    assert set(data["jobs"]) == {"changes", "bench"}


def test_the_bench_filter_is_a_pinned_checkout_and_the_changes_script():
    job = _workflow("bench-pr.yml")["jobs"]["changes"]
    assert job["runs-on"] == RUNNER
    assert job["permissions"] == {"contents": "read"}
    assert job["outputs"] == {"bench": "${{ steps.filter.outputs.bench }}"}
    checkout, step = job["steps"]
    assert re.match(r"^actions/checkout@[0-9a-f]{40}$", checkout["uses"])
    assert checkout["with"]["fetch-depth"] == 0
    assert checkout["with"]["persist-credentials"] is False
    assert step["env"]["BASE_SHA"] == "${{ github.event.pull_request.base.sha }}"
    assert step["env"]["HEAD_SHA"] == "${{ github.event.pull_request.head.sha }}"
    assert "scripts/changed_paths.py" in step["run"]
    assert '"$GITHUB_OUTPUT"' in step["run"]


def test_the_pull_request_bench_is_gated_on_the_filter_and_fails_open():
    job = _workflow("bench-pr.yml")["jobs"]["bench"]
    assert job["needs"] == "changes"
    assert job["if"] == BENCH_GATE
    assert FAIL_OPEN in job["if"]
    # It runs a pull request's code: no token that writes, no secret.
    assert job["permissions"] == {"contents": "read", "actions": "write"}
    assert "secrets." not in str(_workflow("bench-pr.yml"))
    # It takes an e2e slot once the variable names the label.
    assert job["runs-on"] == (
        "${{ fromJSON(vars.E2E_RUNS_ON || "
        """'["self-hosted","linux","x64","lafabrique"]') }}"""
    )


def test_the_pull_request_bench_is_the_nightly_fake_run():
    nightly = _workflow("bench.yml")["jobs"]["bench"]
    pr = _workflow("bench-pr.yml")["jobs"]["bench"]
    assert pr["env"] == nightly["env"]
    assert pr["timeout-minutes"] == nightly["timeout-minutes"]
    assert pr["steps"][0]["uses"] == nightly["steps"][0]["uses"]
    ours, theirs = _named_steps(pr), _named_steps(nightly)
    for name in BENCH_SHARED_STEPS:
        for key in ("run", "env", "if", "working-directory"):
            assert ours[name].get(key) == theirs[name].get(key), (name, key)
    # Compared with the same committed reference cards, on the same thresholds.
    compare = ours["Compare with the previous scorecard"]
    assert "scripts/bench/compare.py" in compare["run"]
    assert "--dir bench/scorecards " in compare["run"]
    # None of the nightly extras (the strict budgets, the issue), no real model.
    assert set(ours) == {*BENCH_SHARED_STEPS, "Keep the scorecard and the logs"}
    assert "BENCH_LLM_URL" not in str(pr) and "BENCH_TOKEN_BUDGET" not in str(pr)


def test_real_model_jobs_never_run_on_a_pull_request():
    data = _workflow("bench.yml")
    assert set(data["on"]) == {"workflow_dispatch", "schedule"}
    jobs = data["jobs"]
    assert "vars.BENCH_REAL_MODELS == 'true'" in jobs["bench-real"]["if"]
    for name in ("watch", "revert"):
        assert "github.ref == 'refs/heads/main'" in jobs[name]["if"], name


def test_every_action_in_the_bench_workflows_is_pinned_by_sha():
    pinned = re.compile(r"^[\w.-]+/[\w.-]+@[0-9a-f]{40}$")
    for workflow in ("bench.yml", "bench-pr.yml"):
        for name, job in _workflow(workflow)["jobs"].items():
            for step in job.get("steps", []):
                if "uses" in step:
                    assert pinned.match(step["uses"]), (workflow, name)


@pytest.mark.parametrize(
    "path",
    [
        "bench/specs/01-cli-wordcount/spec.json",
        "bench/scorecards/20261114T201804Z.json",
        "scripts/bench/compare.py",
        ".github/workflows/bench.yml",
        ".github/workflows/bench-pr.yml",
    ],
)
def test_a_change_to_the_bench_itself_runs_it(path):
    assert _bench_runs_for([path])


@pytest.mark.parametrize(
    "path",
    [
        "CHANGELOG.md",
        "docs/TESTING.md",
        "frontend/src/App.tsx",
        "backend/app/services/loop/task_worker.py",
        "backend/alembic/versions/x.py",
        "backend/poetry.lock",
        "vscode-daemon/daemon.py",
        "backend/tests/test_ci_paths.py",
        "llm-proxy/app.py",
        "scripts/e2e/run.sh",
        ".github/workflows/e2e.yml",
    ],
)
def test_other_changes_skip_the_bench(path):
    assert not _bench_runs_for([path])
