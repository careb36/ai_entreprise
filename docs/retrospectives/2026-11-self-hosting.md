# Retrospective: the factory on itself (2026-11, issue #329)

La Fabrique asks every project for a numbered specification, an architecture
with ADRs and quality standards, and checks the work against them. Until now
its own development went through none of that. This is the first run of La
Fabrique on La Fabrique, the way OpenHands develops OpenHands with OpenHands:
the product's own entry points, on its own repository, with the evidence
kept. It records what worked, what the factory could not do on itself, and
the issues that should follow.

## What was run

- **Where.** The e2e stack (`scripts/e2e/run.sh`, step 9d,
  `scripts/e2e/self_host_step.sh`): backend, workspace daemon, NATS with one
  user per component, local Postgres 16, the fake model
  (`scripts/e2e/fake_llm.py`), `E2E_SANDBOX=host`. No real model was called;
  how an owner runs it with one is in
  [`scripts/self_host/README.md`](../../scripts/self_host/README.md).
- **The company.** "La Fabrique" (company 3 of the run), its project a
  snapshot of this repository (1,147 files committed as `main` of a fresh
  repository, outside the checkout), the Git workflow on `main`, the
  delivery tests named in the stack profile, the sandbox set to the run's
  mode, the roles PO, Architect, Developer, QA, ScrumMaster and TechWriter.
- **The records.** La Fabrique's own specification and architecture
  (`docs/architecture/specification.json`, `architecture.json`, issue #469)
  posted and approved: specification 3, architecture 2.
- **The issue.** Issue #535 (saved views of a sprint's tasks, filtered on the
  evidence) written as a specification
  (`scripts/self_host/fixtures/issue-535-specification.json`: three
  requirements, five criteria in Given/When/Then form read as EARS
  event-driven requirements, as Kiro's `requirements.md` does), specification
  4; the architecture revised for it, architecture 3 (the `tasks` and
  `contracts` contexts cover it, two interfaces and ADR-7 added); the PO's
  plan; sprint 10, tasks 24 to 29. The issue was specified, not implemented.
- **The harness.** `scripts/self_host/self_host.py`, through the public API
  only, run twice: the second run made nothing new.

The JSON reports of each step are written to `$E2E_WORKDIR/self-host/` and
are what the e2e checks read. The ids below are those of the run on
2026-11-15 (database `factory_e2e_329`); two runs that day gave the same company,
sprint and task ids.

## What worked

1. **The records pass the product's own checks and round-trip unchanged.**
   The specification and the architecture were approved with no blocking
   finding (15 modules, 17 interfaces, 6 decisions). The stored architecture,
   read back through `GET /v1/architectures/2/document`, is byte for byte
   `docs/ARCHITECTURE.md`; through the new `GET /v1/architectures/2/adrs?first_number=12`
   it is byte for byte `docs/adr/0012-dependency-rule.md` to
   `0017-http-adapters.md`; and the daemon's write of the document into the
   snapshot left `git status` clean. `backend/tests/test_self_hosting.py`
   keeps the files and the record in step, in-process, the way
   `test_standards.py` keeps `docs/STANDARDS.md` in step.
2. **The specification check improved the specification.** The first draft
   of the #535 fixture said nothing about reliability or accessibility;
   `spec_check` warned on both quality areas, and the fixture now has a
   measurable target for each. The approved version has no finding at all.
3. **The architecture check took a revision of La Fabrique's own design.**
   The revision for #535 (new interfaces in `contracts` and `tasks`, ADR-7
   "Task filters are evaluated from the projections, over an allowlist")
   passed `arch_check` with no finding, the 14 contexts unchanged.
4. **Acceptance tests first.** The PO's plan put QA's three test-writing
   tasks first, each implementation waiting for its requirement's tests and
   the filtered list also for the filter, with nothing uncovered.
5. **The Git workflow on a real repository.** Each of the six tasks was
   delivered on its own branch (`fabrique/task-24` … `29`) in a worktree
   under `.git/fabrique-worktrees/`, never in the snapshot's checkout; the
   sprint's integration merged the done branches into `fabrique/sprint-10`
   and ran the suite there. A person's local merge of that integration into
   `main` is refused while its suite fails ("The suite failed on the
   integrated branch: fix it, or merge it yourself"): what reaches `main` is
   what a person merged, on a passing suite.
6. **The documentation pack on itself.** The #530 pack compiled for La
   Fabrique in English (pack 1) and Spanish (pack 2): 13 documents each,
   3 C4 diagrams (context, the 15 modules as containers, components), no
   blocking lint finding, 7 sections left for a person in each, as designed.
7. **The history.** `app/history_check.py` found no illegal history in the
   run, the self-hosting company's tasks included.

## What the factory could not do on itself

### F1. Under the Git workflow, a task's branch is refuted for the acceptance tests of other requirements

The Verifier protects QA's acceptance tests: a delivery that changes one is
refuted and the test is restored from QA's delivery. With the Git workflow,
an implementation task's branch starts from `main` plus its own
dependencies, so it does not hold the tests QA wrote on sibling branches for
*other* requirements; the Verifier reads them as edited, refutes the
delivery and restores them into the branch. Three of the run's implementation
deliveries were refuted that way (verifications 30, 31 and 36, of tasks 27,
29 and 28; the e2e prints the count). Worse, once a
sibling's implementation is done, its restored tests must pass, and they
cannot on a branch without that implementation: "Add filtered task list"
(task 28, run 22) failed `tests/test_req_3.py` with `ModuleNotFoundError:
No module named 'src.add_saved_views'` (verifications 37 and 38) and was
blocked for triage, and the
integration's suite then failed on the missing task. In the default e2e
project (no Git workflow, one working tree) this never shows.

### F2. A `verified` task event does not name its verification

`TaskEvent.verification_id` is never set on `verified` events: 0 of 22 in a
run of `main`'s e2e, 0 of 37 in this run and 0 of 15 in its self-hosting
sprint. The pack's
traceability matrix therefore reads "Latest verification: none recorded" for
every task although each run has its verifications, and every reader that
joins a task to its evidence through the event (`project_docs/collect.py`)
sees nothing. The deliver stage passes the run id, not the verification id
(`services/loop/worker/deliver.py`).

### F3. An architecture belongs to one specification, not to the company

La Fabrique has one architecture and many specifications (#469's, now
#535's). In the product, an architecture is drafted for one specification
and a revision is a new version of *that* architecture, so a revision for a
new specification can only be a copy: the harness merged the #535 change into
a copy of `architecture.json` (`fixtures/issue-535-architecture.json`) and
posted it as a second architecture, its covers rewritten by hand. The
company now has two diverging architectures of the same backend. The same
assumption limits the pack: it documents the latest approved specification
only, so La Fabrique's own #469 specification is absent from it.

*Done (follow-up 3).* The architecture is now the company's, versioned
(`services/planning/arch_versions.py`, migration `0038`): each approved
revision is a new version numbered per company, approving one supersedes the
company's other approved version, and the earlier versions stay readable
(`GET /v1/companies/{id}/architecture`, `…/versions`, `…/versions/{n}`). A
module's `covers` name the requirements of the specification the version was
drafted for, and `earlier_covers` keep, by specification id, what it covers in
the earlier ones; the revision for a new specification
(`POST /v1/companies/{id}/architecture/revisions`, or a draft for a
specification the current version does not cover) moves the own covers there,
so no specification loses its covers. A specification names the version it
was planned against (`architecture_id`). `arch_check` and the coverage read
every specification a version covers, and the pack has a section per
approved specification in its scope statement, WBS, traceability matrix,
schedule and index. The harness's `issue` step posts the #535 revision as
version 2 of La Fabrique's one architecture: the fixture adds #535's covers
and no longer rewrites #469's, and the e2e step checks that both
specifications share the record.

### F4. ADRs were not a product output

The factory asked projects for ADRs but wrote decisions only inside
`docs/ARCHITECTURE.md`. This change adds `services/planning/adr_docs.py`
and `GET /v1/architectures/{id}/adrs` (one file per decision, numbered from a
repository's first free number, accepted on the decision's recorded date or
the architecture's approval), and decisions may carry a `date` and a `slug`.
`POST /v1/architectures/{id}/write` still writes only `docs/ARCHITECTURE.md`;
the ADR files are not yet written to a project's repository, and the
appendix ADR 0012 had (the contexts at adoption) had to move to
`docs/architecture/MIGRATION.md`, since a decision has no place for one.

### F5. La Fabrique's own suites cannot be its delivery tests

The stack detection finds no manifest at the repository's root (the backend
is a Poetry project under `backend/`, the frontend an npm project under
`frontend/`, the LLM proxy another Poetry project), and a company's profile
holds one test command for the whole repository. The run named
`python -m pytest … tests`, so a delivery was judged by the tests the sprint
wrote, not by the backend's suite, the frontend's lint and tests, the
architecture checks or the file-size ratchet that CI runs. A real-model run
needs a profile per folder before its verdicts mean anything for La Fabrique.

*Done (follow-up 5):* the stack profile resolves per folder
(`services/workspace/stack_roots.py`): `backend/`, `frontend/` and
`llm-proxy/` are found with their own install and test commands, a company
names or overrides folders and adds commands every delivery runs, a delivery
runs the suites of the folders the daemon saw it write in, and the Verifier's
`required_commands` check records and judges each run. The harness's
`company` step sets La Fabrique's suites per folder and its architecture and
file-size checks (`scripts/self_host/README.md`).

### F6. A "tests failed" comment quotes the warnings, not the failures

The comment on the blocked task and its block reason quote the end of the
output, which was pytest-asyncio's deprecation warning on stderr; the failing
tests, which the evidence records (`failing_tests`, the per-criterion
results), are not in the text a person reads first.

### F7. No review object for the sprint's request

The sprint's integration is the one request a person should read, but a
person's review exists only per run (`/v1/agent_runs/{id}/reviews`) or on a
forge after `integration/publish`. A local snapshot has no remote, so the
person's reading of the integration left no record beyond the merge (or its
refusal).

*Done* (follow-up 7): the integration is now reviewed as a run is, with
line comments anchored to the diff the daemon reads and a review recorded and
audited with its actor, published or not; a company may require an approving
review before the local merge (`services/release/integration_review.py`,
[release](../contexts/release.md#a-persons-review-of-the-integration)).

### F8. Smaller frictions

- `init_company` with `start_now: false` still plans a foundation sprint
  (sprint 9), which an existing project such as La Fabrique does not need;
  it stays planned forever.
- The sprint reads "completed" before its integration job has finished: a
  reader must wait for the integration (the harness now does; its first run
  read `running` and the merge was refused for "no commit").
- The standards catalogue is the same for every company, so "importing the
  standards" had nothing to import; La Fabrique's own rules beyond the
  catalogue (the dependency rule, HTTP adapters, the file-size ratchet, i18n
  in both languages) are not expressible as a company's standards.
- The wiki (#445, PR #547, not merged) was not exercised.
- Outside self-hosting: on the machine of this run, JetStream refused the
  full-log object store ("insufficient storage resources available", the
  disk 88% full), so step 9's full-log check failed in both runs while every
  check of step 9d passed; a run of `main` on the same machine earlier that
  day passed it.
- The sandbox was the run's `host` mode; a container run of the self-hosting
  company was not attempted here.
- With the fake model the Developer's deliveries are placeholders
  (`src/add_task_filter.py` returns a string); the run proves the plumbing,
  not that the factory can write La Fabrique's code.

## Follow-up issues (drafts, not opened)

1. **Under the Git workflow, guard and run only the acceptance tests a task's branch can hold.**
   *Problem:* F1. *Proposal:* the acceptance-test guard and the "done
   tasks' tests must pass" rule read the task's branch: a test of a
   requirement whose implementation is not merged into the branch is neither
   "edited" nor expected to pass there; the integration branch is where every
   requirement's tests must pass. *Measure:* the self-hosting e2e step closes
   its sprint with six tasks done and an integration whose suite passes; no
   delivery is refuted for a test it never touched.
2. **Record the verification on the `verified` task event.**
   *Problem:* F2. *Proposal:* the deliver stage writes `verification_id`
   with `run_id`; the history check refuses a `verified` event without one.
   *Measure:* the pack's traceability matrix names a verification for every
   delivered task in the e2e.
3. **A company architecture that specifications revise.** *Done* (see F3).
   *Problem:* F3. *Proposal:* the architecture becomes the company's, versioned;
   a specification names the version it was planned against, and the
   Architect's revision for a new specification is a new version of the
   company's architecture; the pack documents every approved specification.
   *Measure:* La Fabrique's #469 and #535 specifications share one
   architecture record in the self-hosting run.
4. **Write the ADR files with the architecture.**
   *Problem:* F4. *Proposal:* `POST /v1/architectures/{id}/write` also writes
   `docs/adr/NNNN-<slug>.md` (first free number read from the repository), each
   file's sha256 kept as an observation; a superseded decision says so.
5. **A stack profile per folder.**
   *Problem:* F5. *Proposal:* the profile resolves per root (`backend/`
   Poetry, `frontend/` npm, `llm-proxy/` Poetry) and a delivery runs the
   suites of the folders it touched; a company may add commands that run on
   every delivery (La Fabrique's architecture checks).
   *Done:* `Company.stack_profile` takes `folders` and `every_delivery`
   (no migration: a JSON column, and a profile in the old shape is the
   root's alone); folders are detected up to two deep when the root has
   neither a stack nor a test command of its own. The files are the daemon's
   report of what it wrote (`workspace_writes`); a file in no folder runs the
   root's suite, or every folder's when the root has none. Each run is kept
   in `evidence["command_runs"]` with its folder, exit status and the sha256
   the backend computed; a failure refutes, a runtime that is not installed
   leaves the delivery unverifiable. A sprint's integration runs every
   folder's suite. Not done: the lint after an edit still reads the root's
   profile, not the edited file's folder.
6. **Quote the failing tests in a "tests failed" comment.**
   *Problem:* F6. *Proposal:* the comment and the block reason lead with
   `failing_tests` and the per-criterion results, then the output's tail.
7. **A person's review of a sprint's integration.** *Done.*
   *Problem:* F7. *Proposal:* reviews and line comments on the integration as
   on a run, recorded and audited, whether or not it is published to a forge.
   *Built:* `services/release/integration_review.py`, `routers/integration_reviews.py`
   and the daemon's read-only `workspace.git.diff` (`vscode-daemon/git_diff.py`):
   comments anchored to the daemon's diff with the file's hash, reviews with
   the commit and the diff's hash, audited with the account; an Owner's
   setting, off by default, makes the local merge wait for an approval of the
   integration's commit, and the sprint's starter does not give it
   (segregation of duties); `self_host.py --merge` records an approving
   review first. *Measure:* the e2e review step's report carries
   `integration_review` beside `merged`.
8. **No foundation sprint for an existing repository.**
   *Problem:* F8. *Proposal:* `init_company` on a folder the daemon observes to
   be a repository with code plans no foundation sprint unless asked.

## Taken from

- **OpenHands** (develops OpenHands with OpenHands): the product run on its
  own repository through a user's entry points; design only, no code.
- **Kiro by Amazon** (spec-driven development): requirements with EARS
  acceptance criteria before design and tasks, in the #535 fixture; design
  only, no code.
- **Michael Nygard's ADR template** (Status, Context, Decision,
  Consequences) for the ADR files.
