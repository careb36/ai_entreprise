# The factory builds itself (issue #329)

La Fabrique asks its projects for numbered specifications, an architecture
with ADRs and quality standards. This folder runs La Fabrique on itself
through the same entry points a user has, the way OpenHands develops
OpenHands with OpenHands (design only, no code taken).

| File | What it is |
|---|---|
| `self_host.py` | The harness: makes the "La Fabrique" company and drives it through the public API. Idempotent. |
| `render_docs.py` | Renders `docs/ARCHITECTURE.md` and `docs/adr/0012`… from `docs/architecture/architecture.json`; `--write` writes them. |
| `fixtures/issue-535-specification.json` | Issue #535 written as a specification, in the PO's shape (EARS-style criteria, after Kiro; design only). It specifies the issue and does not implement it. |
| `fixtures/issue-535-architecture.json` | The Architect's revision of La Fabrique's architecture for that specification: the covers of #535's requirements, the interfaces and the decision it adds. The product keeps #469's covers (`earlier_covers`). |
| `sandbox_mode.py` | `--container-mode` and the `sandbox` step: which sandbox the run's code actually ran in, from the daemon's replies (issue #701). |
| `spec_source.py` | What the `issue` step runs on: #535 by default, or a `--spec` file checked with the product's `spec_check`; the `measure` step. |
| `specs/folder-lint-specification.json` | The first manual iteration's finding (`docs/design/rsi-loop.md`, §7): the lint after an edit runs the edited file's folder's linter. EARS-style criteria and its `target_measure`. `folder-lint-architecture.json` beside it is the revision. |
| `measures/test_folder_lint.py` | That finding's target measure: one test per criterion, written by a person. It fails on `main` (2026-11-16) and should pass once the sprint's change is in. |

The retrospective of the first run is
[`docs/retrospectives/2026-11-self-hosting.md`](../../docs/retrospectives/2026-11-self-hosting.md).

## The records and the documents

`docs/architecture/specification.json` and `architecture.json` are La
Fabrique's own specification and architecture, in the shapes the PO and the
Architect write for any company. `docs/ARCHITECTURE.md` is their rendering by
`arch_check.as_markdown`, and `docs/adr/0012-…` to `0018-…` are the rendering
of the architecture's decisions by `adr_docs.adr_documents`, numbered from the
record's `adr_first_number` (ADRs 0010 and 0011 predate the record). Edit the
JSON, never the rendered files:

```bash
python3 scripts/self_host/render_docs.py          # what differs; exit 1 if anything does
python3 scripts/self_host/render_docs.py --write  # rewrite the rendered files
```

`backend/tests/test_self_hosting.py` fails when they drift, and also posts the
record through the API in-process and checks it renders the same files.

## The harness

```bash
python3 scripts/self_host/self_host.py --api <backend URL> --checkout <path> [--steps …]
```

| Step | What it does |
|---|---|
| `checkout` | Copies the files git tracks (or would track) in this repository to `--checkout` and commits them there as `main` of a fresh repository. Never the live checkout and never a `git worktree` of it: the Git workflow makes a branch per task, and a worktree shares the live repository's branches. |
| `company` | `POST /v1/init_company` "La Fabrique" with the snapshot as its project, the Git workflow on `main`, the roles of the loop (PO, Architect, Developer, QA, ScrumMaster, TechWriter); the stack profile (`--profile`, `folders` by default) names La Fabrique's suites per folder as CI runs them (`backend/` pytest through Poetry, `frontend/` tsc, lint and vitest, `llm-proxy/` pytest, `vscode-daemon/` its own suite, `vscode-daemon/tests`; it ran the backend's whole suite again, twice per delivery that touched both), each with its install, and three commands every delivery runs (`scripts/check_architecture.py`, `check_frontend_architecture.py`, `check_file_sizes.py`); a delivery runs the suites of the folders the daemon saw it write in, every one for a file outside them (self-hosting retrospective, F5). `--profile sprint-tests` (the e2e's, whose sandbox has none of these toolchains) names one root command instead (`--test-command`). Either way the root's install, lint, start and e2e are off; the sandbox is `--sandbox` (`container` by default). No foundation sprint is planned: the daemon observes the snapshot to be a repository with code, so `init_company` skips it (retrospective F8, fixed) and the response says `foundation_sprint.reason: repository_with_code`; the harness's own sprint comes from the specification (step `issue`). |
| `records` | Posts and approves La Fabrique's specification and architecture; reads `GET /v1/architectures/{id}/document` and `/adrs?first_number=12` and compares them with the snapshot's files; writes the document through the daemon and checks git sees no change. |
| `issue` | Posts and approves the #535 specification (with `--spec FILE`, that file instead, see below); opens the next version of La Fabrique's architecture for it (`POST /v1/companies/{id}/architecture/revisions`, which keeps each module's #469 covers), writes the fixture's change into it and approves it, so both specifications share one architecture record; asks the PO for a plan (`POST /v1/plan_sprint`: the acceptance tests first) and starts the sprint on it. |
| `wait` | Polls until the sprint completes (`--timeout`), and reports its `integration_state`. |
| `sandbox` | Reads the sandbox the daemon reported in its reply to each test run of the sprint (`GET /v1/agent_runs?sprint_id=…`, then each run's verifications, `evidence.test_run.sandbox`) and labels the run with it, apart from what was asked (below). |
| `review` | Waits while the sprint's `integration_state` reads `integrating` (completed is not finished before that), then lists the task branches, the tasks waiting for a person and the sprint's integration; `--accept TEXT` accepts the tasks the risk tier holds for a person (never one blocked for triage), `--merge` records an approving review of the integration (its text the `--accept` comment, else a default; reported under `integration_review`, refused like any approval by the person who started the sprint) and then merges the integration into `main` locally (refused while its suite fails, and, when the company requires an approval, without one). |
| `pack` | Compiles the documentation pack (#530) in English and in Spanish and reports what each could not say; `--prose` asks the TechWriter for the charter's background. |
| `measure` | Asked by name only, never part of `all`, and only with `--spec`. Runs the file's `target_measure` (this repository's copy) with pytest on the `backend/` of `--build` (the snapshot by default), in the workspace daemon's container sandbox (below), from the image `--grading-image` names (default `FABRIQUE_GRADING_IMAGE`). The report keeps the exit status, `result` (`pass`, `fail` or `unmeasured`, with `reason`), `passed`, `isolation`, the sandbox applied and the sha256 of the output, computed by the harness. The exit status is 3 when the measure fails and 4 when it is unmeasured. No API call. |

### Container mode (`--container-mode`, issue #701)

The first run used the host (retrospective F8), and its report could not have
said otherwise: `company.sandbox` is the mode the harness asked the company
for, configuration rather than what ran. `--container-mode` runs the harness
the way the e2e's `e2e (container)` job does (`E2E_SANDBOX=container
scripts/e2e/run.sh`): the company's sandbox `container`, `--profile
sprint-tests` with the sandbox image's own `python` (`python -m pytest -q -p
no:cacheprovider tests`; `--profile` and `--test-command` still override), and
the `sandbox` step after `wait`. The stack's side is the owner's, as in the
e2e: `DELIVERY_SANDBOX=container` and `DELIVERY_SANDBOX_IMAGE` on the backend,
and the image on the daemon's computer (the e2e builds `fabrique-e2e-sandbox:1`
with `printf 'FROM python:3.12-slim\nRUN pip install --no-cache-dir pytest\n'
| docker build -t fabrique-e2e-sandbox:1 -`).

The report's `sandbox` keeps `asked` (the company's setting) and `observed`
(from the daemon's replies, `observed_by: daemon`) in separate fields:
`container`, `host`, `bwrap`, `mixed` when replies disagree, a mode with
`(not isolated)` when the reply said so, or `not observed` when no reply
carried a sandbox (an older daemon, or no test ran). `container_mode` is true
only when every reply reported an isolated container; `modes` counts the
replies per mode and `images` lists the content IDs the daemon observed. When
`observed` differs from `asked`, `note` says the run is labelled with the
observed mode, not the asked one. A `--container-mode` run that is not
container mode exits with status 5 and adds the reason to `notes`. The
daemon's announced abilities (`workspace.status`) are a claim about what it
can honour and never label a run.

```bash
python3 scripts/self_host/self_host.py --api http://localhost:8000 \
  --checkout ~/src/la-fabrique-self --container-mode
```

### A finding's specification (`--spec`)

Without `--spec`, the `issue` step runs on issue #535 as before, which is what
the e2e run reads. With `--spec FILE`, the harness reads a specification in
the PO's shape plus `target_measure`, the repository path of the check that
fails today and should pass after the sprint (`docs/design/rsi-loop.md`,
REQ-2). It checks the file with the product's own `spec_check`, imported
from this repository, before any call. A blocking finding, a missing or
absent `target_measure`, or a missing revision makes the harness exit with
status 2, and nothing is posted. Warnings are reported under
`issue.warnings`. The revision is `--revision FILE`; by default it is
`<name>-architecture.json` beside `<name>-specification.json`. The sprint is
named after the specification's title, and `target_measure` is never posted.

```bash
# The target measure on today's main, in the sandbox (it fails: exit status 3)
python3 scripts/self_host/self_host.py --checkout /tmp/rsi/B --steps checkout,measure \
  --spec scripts/self_host/specs/folder-lint-specification.json \
  --grading-image fabrique-grading:backend
# The same on this machine's Python, for local development only (says so)
python3 scripts/self_host/self_host.py --checkout /tmp/rsi/B --steps measure \
  --spec scripts/self_host/specs/folder-lint-specification.json --in-process-measure \
  --python "$(cd backend && poetry env info -p)/bin/python"
# The sprint on it, against a real stack
python3 scripts/self_host/self_host.py --api http://localhost:8000 --checkout /tmp/rsi/B \
  --steps issue,wait,review --spec scripts/self_host/specs/folder-lint-specification.json
```

### The measure runs in the sandbox (PR 12)

The target measure imports the build's code, which is the party being
measured, so it runs in the workspace daemon's container sandbox
(`vscode-daemon/sandbox.py`, through `scripts/bench/sandbox_grading.py`;
threat model `docs/design/SANDBOX_GRADING_THREAT_MODEL.md`), never in the
harness's Python: network off, the build read-only at `/candidate` (the
measure runs from `/candidate/backend`), the measure's folder from this
repository read-only at `/graders`, the root file system read-only, a fresh
scratch and `/tmp` the only writable places, 2 CPUs, 2048 MB, 256 processes
and `MEASURE_TIMEOUT_SECONDS`, no variable of the harness's environment. The
image must already be on the machine (it is never pulled while grading) with
the backend's dependencies and pytest; `docs/OPERATIONS.md` (Grading in the
sandbox) builds one. The candidate's `conftest.py`, ini files,
`sitecustomize.py`, a `pytest.py` shadow and `PYTHONPATH`/`PYTEST_ADDOPTS`
reach nothing (`-I`, `--noconftest`, `-c /dev/null`, plugins not
auto-loaded, a clean environment). The verdict is the runtime's exit status
and pytest's JUnit report printed by the harness's own wrapper after every
other process was killed: `pass` needs exit 0 and a report where every test
passed; a test that failed or could not be collected is `fail`; anything
else (no runtime, no image, a timeout, a kill by the memory or process
limit, a report missing or disagreeing with the exit status) is
`unmeasured`, never passed. `--in-process-measure` runs it on `--python`
instead, as before: for local development only; the report says
`isolation: in_process` with a warning, and `fitness.decide` refuses such a
result (`not_sandboxed`).

The company id, the sprint id and the record ids are kept in
`<checkout>.self_host.json` (`--state`); specifications and architecture
versions are found again by title (a version superseded by the #535 one
included, which `records` reads but no longer writes). Each run prints a JSON report (`--report` also writes
it). With authentication on, set `FABRIQUE_API_TOKEN` to an API token of an
Owner or Manager (Settings, API tokens); it is sent as a bearer token.

## In the e2e run (fake model)

Step 9d of `scripts/e2e/run.sh` (`scripts/e2e/self_host_step.sh`) runs every
step against the e2e stack with the fake model, which knows the #535
specification (`SELF_HOST_SPEC`, `SELF_HOST_PLAN` in `scripts/e2e/fake_llm.py`).
The checks are in `docs/TESTING.md`.

## Against a real stack (real models)

Out of scope for the e2e; this is how an owner runs it.

1. Start the stack (`docker compose up -d`) and the workspace daemon on the
   computer that will hold the snapshot (`./scripts/start-daemon.sh`). The
   harness's `checkout` step runs where the daemon runs: the path is a path on
   that computer.
2. In the web app, connect the providers for the PO, the Architect, the
   Developer, QA and the TechWriter (Settings, Models) and set a budget.
   Turn the sandbox on for the deployment (`DELIVERY_SANDBOX=container` and
   `DELIVERY_SANDBOX_IMAGE`, `docs/CONFIGURATION.md`) and build the image with
   what the folders' suites call: Python 3 with Poetry, Node with npm, and
   git (the file-size check lists tracked files). A command whose runtime is
   missing leaves the delivery unverifiable, never verified.
3. Create an API token and run the setup:

   ```bash
   export FABRIQUE_API_TOKEN=…            # from Settings, API tokens
   python3 scripts/self_host/self_host.py --api http://localhost:8000 \
     --checkout ~/src/la-fabrique-self --steps checkout,company,records
   ```

   The report must say `document_matches: true`, `adr_differences: []` and
   `written_unchanged_in_git: true`.
4. Specify a real issue: in the web app, open the company, Specification, and
   let the PO draft it from the issue's text (answer its questions; approve
   when the check passes), then let the Architect revise the architecture
   for it. To start from the fixture instead, run `--steps issue`: it posts
   the #535 specification and the fixture revision and plans the sprint.
5. Run the sprint, then read the evidence: `--steps wait,review`. Review the
   integration branch (`fabrique/sprint-<id>`) in the snapshot as you would a
   pull request, or in the web app (the sprint's Overview, Sprint integration:
   the diff the daemon reads, line comments and a review, recorded and
   audited); accept what the risk tier holds with `--accept "…"` and merge
   with `--merge`, or carry the blocked tasks into the next sprint.
6. `--steps pack` compiles the documentation pack; approve and write it from
   the web app (Documentation) once you have read it.
7. Bring what you keep back to this repository as an ordinary branch and pull
   request; the snapshot has no remote, so nothing is pushed for you.
