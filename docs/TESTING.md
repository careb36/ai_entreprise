# Testing

How to check a change, what each suite runs, and what CI runs. What a single
test covers is in its module docstring; which tests a file can break is
`scripts/affected_tests.py --explain`.

## Before you push

```bash
scripts/check.sh quick   # while working: what your change can break
scripts/check.sh full    # once before pushing: everything CI runs
scripts/land.sh <branch> # merge with main, run `full` on the merge, fast-forward main
```

- **`quick`** reads the files changed against `CHECK_BASE` (default
  `origin/main`) and runs the affected backend tests, ruff on the changed
  Python files, `npm run test:changed`, eslint and `tsc` for frontend changes,
  actionlint for workflow changes, and always the rule checks
  (`check_architecture.py`, `check_frontend_architecture.py`,
  `check_file_sizes.py`, `check_config.py`, `check_changelog.py`).
- **`full`** runs the whole backend suite in parallel, ruff, the LLM proxy's
  tests, the whole frontend (`tsc`, lint, tests, build), actionlint and the rule
  checks.
- Both print each step's status and time and exit non-zero on a failure. They
  install `frontend/node_modules` or a Poetry venv only when it is missing or
  its lock file changed. Useful variables: `CHECK_PYTHON`,
  `CHECK_BACKEND_WORKERS` (cap on a shared machine), `CHECK_FILES`,
  `CHECK_DRY_RUN=1`, `CHECK_REPORT=<file>`.
- **`land.sh`** is the gate into `main`: it merges in a clean worktree, runs
  `check.sh full` on the merge commit, fast-forwards `main` only on success and
  writes a verification record (`refs/notes/verification`). `--push` mirrors
  `main` and the notes; `--verify <sha>` re-runs a record elsewhere. A
  protected path needs `LAND_OWNER=1`. Options and exit codes:
  [`OPERATIONS.md`](OPERATIONS.md), Landing locally.

## Backend

```bash
cd backend && PYTHONPATH=.:app python3 -m pytest tests -q              # all, in parallel
cd backend && PYTHONPATH=.:app python3 -m pytest tests/test_x.py -n 0  # a few, one process
```

- **No services needed.** Postgres, NATS, Neo4j and the proxy are replaced by
  in-memory SQLite and mocks.
- **Parallel by default** with pytest-xdist (`-n auto --dist loadgroup`, from
  `conftest.py`); `-n 0` or `--pdb` runs in one process. CI uses `-n 4`.
- **Isolation.** Each worker has its own database, emptied after every test.
  Module-level state that could leak between tests is listed in
  `conftest.PROCESS_STATE` and cleared after each test; `test_process_state.py`
  fails on a new unlisted container. Timing tests share one worker
  (`xdist_group("wall_clock")`).
- **Lint** with the ruff version CI pins: `pip install "ruff==0.17.1"`, then
  `ruff check app tests && ruff format --check app tests`.
- **New dependencies** go in `backend/pyproject.toml` with the refreshed
  `poetry.lock` in the same commit.

### Only the tests a change can break

Every `backend/tests/test_*.py` has an entry in `tests` in
`docs/architecture/contexts.json` (the contexts it exercises);
`check_architecture.py` fails on a test file without one. Start a new entry
with `python3 scripts/affected_tests.py --suggest backend/tests/<file>`.

```bash
python3 scripts/affected_tests.py --files backend/app/services/loop/task_worker.py
python3 scripts/affected_tests.py --explain              # why each test runs
python3 scripts/affected_tests.py --by context           # coarser selection
<backend venv>/bin/python scripts/affected_tests.py --run -- -q
```

`--by module` (default) follows an import graph of `backend/app`,
`vscode-daemon`, `bench` and the tests; `--by context` takes every test of the
changed module's context. Both fall back to the whole suite, and say why, for
`conftest.py`, lock files, models, migrations, `contexts.json` or an unknown
path.

## Frontend

```bash
cd frontend && npx tsc --noEmit && npm run lint && npm run test:run && npm run build
```

- `npm run lint` enforces i18n: literal text in JSX or in `title`, `alt`,
  `placeholder`, `aria-label` or `label` fails. Every string goes in both
  `src/i18n/en.json` and `es.json`; `src/i18n/locales.test.ts` keeps their keys
  identical.
- `src/uiRules.test.ts` checks what lint cannot see (text in template literals,
  images without `alt`, unnamed buttons and links, unlabelled controls,
  clickable elements a keyboard cannot reach). Its baseline,
  `src/test/uiRulesBaseline.json`, only shrinks.
- The layer rule is `scripts/check_frontend_architecture.py`
  ([`contexts/frontend.md`](contexts/frontend.md)).

## LLM proxy and daemon

```bash
python -m pytest llm-proxy/tests -q                                  # the proxy's Poetry environment
cd backend && poetry run python -m pytest ../vscode-daemon/tests -q  # as CI runs it
```

## End to end

`scripts/e2e/run.sh` starts the backend, the workspace daemon and a fake LLM
proxy with deterministic answers (`scripts/e2e/fake_llm.py`) against a Postgres
and a NATS you provide (`docker compose up -d postgres nats`, or
`E2E_POSTGRES_*` and `E2E_NATS_URL`). It drives one company through several
sprints and checks the result through the API: blocked tasks and their triage,
the retrospective and an adopted procedure, carry-over, rework budgets, a web
project's app check, and self-hosting. CI's `e2e.yml` runs it twice, with the
host sandbox and the container sandbox. The steps and what each asserts are in
the script.

## The bench

`scripts/bench/run.sh` drives the reference specifications in `bench/specs/`
through the factory with real or fake models, grades the result with
person-written acceptance tests in a sandbox, and writes a scorecard that
`scripts/bench/compare.py` compares with a baseline. Real-model runs need
`BENCH_TOKEN_BUDGET`. Everything else is in
[`bench/README.md`](../bench/README.md).

The **Verifier benchmark** (`bench/verifier/`) feeds deliveries whose claims
the evidence contradicts, plus honest controls, to the real Verifier: every lie
must come back `refuted` or `unverifiable`, every control `verified`
(`test_verificador_benchmark.py`; by hand,
`cd backend && PYTHONPATH=.:app:../bench python -m verifier.run`).

## CI

All jobs run on the self-hosted runners (`[self-hosted, linux, x64,
lafabrique]`), with one exception: the `probe-arm64` job of
`browser-image-probe.yml`, run only by hand, builds and probes the browser
sandbox image on GitHub's arm64 runner (`ubuntu-24.04-arm`), because under
QEMU on the x64 runner the browser exits before it opens a page (issue #529).
It holds no secret and a read-only token. The self-hosted runners share one
machine, so a pull request runs only the jobs
its files can affect. `.github/workflows/changes.yml` computes that once
through `scripts/changed_paths.py`; the filters live in that workflow and
`test_ci_paths.py` tests them.

| Workflow | Runs on a change to |
|---|---|
| `backend-tests.yml` | the backend, proxy, daemon, bench, NATS config and the scripts the tests read |
| `frontend-build.yml` | `frontend/` and the frontend layer rule |
| `docker-build.yml` | each image's build context |
| `e2e.yml` (host and container) | the backend, daemon, proxy, NATS, `scripts/e2e/`, `scripts/self_host/`, compose files |
| `file-sizes.yml` | source and scripts |
| `workflows.yml` (actionlint) | `.github/workflows/` |
| `bench-pr.yml` | the bench itself (the nightly `bench.yml` covers the backend) |
| `security-scan.yml` | always |

A docs-only change runs only the security scan. Setting the repository
variable `E2E_RUNS_ON` to a runner label carried by two runners limits e2e to
two jobs at once ([`OPERATIONS.md`](OPERATIONS.md), Runner capacity).

### Security checks locally

```bash
# Dependencies
poetry -C backend export --format requirements.txt --all-groups --output /tmp/backend.txt
pip-audit --requirement /tmp/backend.txt --require-hashes --disable-pip
pip-audit --requirement vscode-daemon/requirements.txt --require-hashes --disable-pip
cd frontend && npm audit --package-lock-only --omit=dev --audit-level=high

# Secrets and static analysis on a branch's commits
gitleaks detect --source . --config .gitleaks.toml --redact --no-banner --log-opts "origin/main..HEAD"
uv tool run --from "semgrep==1.182.1" semgrep scan --config p/security-audit \
  --severity ERROR --error --metrics=off --baseline-commit "$(git merge-base origin/main HEAD)" .
```

Licences are scanned by `scripts/licence_scan.py`; `security-scan.yml` has the
exact steps.

## Manual check

```bash
curl http://localhost:8000/health
open http://localhost:8000/docs     # the API, interactive
```
