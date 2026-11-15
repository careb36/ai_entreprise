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
| `fixtures/issue-535-architecture.json` | The Architect's revision of La Fabrique's architecture for that specification. |

The retrospective of the first run is
[`docs/retrospectives/2026-11-self-hosting.md`](../../docs/retrospectives/2026-11-self-hosting.md).

## The records and the documents

`docs/architecture/specification.json` and `architecture.json` are La
Fabrique's own specification and architecture, in the shapes the PO and the
Architect write for any company. `docs/ARCHITECTURE.md` is their rendering by
`arch_check.as_markdown`, and `docs/adr/0012-…` to `0017-…` are the rendering
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
| `company` | `POST /v1/init_company` "La Fabrique" with the snapshot as its project, the Git workflow on `main`, the roles of the loop (PO, Architect, Developer, QA, ScrumMaster, TechWriter); the stack profile (`--profile`, `folders` by default) names La Fabrique's suites per folder as CI runs them (`backend/` pytest through Poetry, `frontend/` tsc, lint and vitest, `llm-proxy/` pytest, `vscode-daemon/` the backend's suite, where its tests live), each with its install, and three commands every delivery runs (`scripts/check_architecture.py`, `check_frontend_architecture.py`, `check_file_sizes.py`); a delivery runs the suites of the folders the daemon saw it write in, every one for a file outside them (self-hosting retrospective, F5). `--profile sprint-tests` (the e2e's, whose sandbox has none of these toolchains) names one root command instead (`--test-command`). Either way the root's install, lint, start and e2e are off; the sandbox is `--sandbox` (`container` by default). |
| `records` | Posts and approves La Fabrique's specification and architecture; reads `GET /v1/architectures/{id}/document` and `/adrs?first_number=12` and compares them with the snapshot's files; writes the document through the daemon and checks git sees no change. |
| `issue` | Posts and approves the #535 specification, posts and approves the revised architecture, asks the PO for a plan (`POST /v1/plan_sprint`: the acceptance tests first) and starts the sprint on it. |
| `wait` | Polls until the sprint completes (`--timeout`). |
| `review` | Lists the task branches, the tasks waiting for a person and the sprint's integration; `--accept TEXT` accepts the tasks the risk tier holds for a person (never one blocked for triage), `--merge` records an approving review of the integration (its text the `--accept` comment, else a default; reported under `integration_review`, refused like any approval by the person who started the sprint) and then merges the integration into `main` locally (refused while its suite fails, and, when the company requires an approval, without one). |
| `pack` | Compiles the documentation pack (#530) in English and in Spanish and reports what each could not say; `--prose` asks the TechWriter for the charter's background. |

The company id, the sprint id and the record ids are kept in
`<checkout>.self_host.json` (`--state`); specifications and architectures are
found again by title. Each run prints a JSON report (`--report` also writes
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
