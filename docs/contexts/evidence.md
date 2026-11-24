# The evidence context

Judging a run from what was observed: the Verifier, the risk tier, the
standards, acceptance tests and the fitness decision of the self-improvement
loop. Read this before changing `backend/app/services/evidence/`; each
module's docstring has the detail.

- **Folder:** `backend/app/services/evidence/`
- **May import:** [contracts](contracts.md), [platform](platform.md). Nothing else: it reads the daemon's reply as data and never calls the daemon, the loop, the tools or a producer.
- **Interface:** `acceptance`, `agent_run_service`, `app_check`, `browser_smoke`, `changelog_diff`, `command_runs`, `company_standards_check`, `console_run`, `context_read`, `criteria_tests`, `delivery_gate`, `doc_build`, `doc_snippets`, `fitness`, `manifests`, `protected_check`, `review_changes`, `risk`, `signatures`, `sources`, `standards`, `touched`, `verification`, `wstg_tests`.
- **Modules:** `acceptance`, `agent_run_service`, `app_check`, `browser_smoke`, `changelog_diff`, `claimed_paths`, `command_runs`, `company_standards_check`, `console_run`, `context_read`, `criteria_tests`, `delivery_gate`, `doc_build`, `doc_links`, `doc_snippets`, `fitness`, `fitness_checks`, `grounding`, `imports`, `manifests`, `markdown_refs`, `metric_matches`, `protected_check`, `required_checks`, `review_changes`, `risk`, `secops_checks`, `security_probe`, `signatures`, `sources`, `standards`, `test_baseline`, `test_collection`, `tests_removed`, `touched`, `unwritten_code`, `verification`, `wstg_tests`.

## How it works

The loop keeps the daemon's replies (tests, install, application check,
probes) on the run's evidence. The Verifier runs one check per observation,
each `pass`, `refuted` or `unverifiable`, and aggregates a verdict; the gate
reads that record to let the task reach QA or send it back with a reason.
Verification never touches the network: registry and signature lookups are
observations recorded at delivery.

## Rules

- A claim and an observation never share a field. What a producer says (its
  claims, declared files, summaries, a diff it sent) is kept apart and labelled
  as a claim.
- An agent's report of its own test run is a claim. Only the daemon's output,
  the platform's `tests_run` or an independent CI conclusion counts as a test
  observation.
- Hashes and signatures are never accepted from the party being recorded. A
  sha256 is computed by the backend over the bytes it received, or read back
  from disk by the daemon; never taken from an agent's or a producer's report.
  A log's hash is the backend's, over the bytes it read back from the store.
- A declared path may add to a classification or a match, never remove one.
- A check that could not run is `unverifiable`, never `pass`. A missing
  required check is added as `unverifiable` with the reason.
- The evidence imports only contracts and platform (`scripts/check_architecture.py`).

## Components

### The Verifier and the gate

- `verification.py`: the Verifier's checks and verdict.
- `required_checks.py`: what a delivery must show, decided before reading what
  it showed. A code change requires a test observation; `verdict_policy` on the
  evidence lets the verdict be recomputed from the row.
- `risk.py`: `classify` gives `low`, `medium` or `high` from paths alone; the
  tier is the highest any path reaches, so adding files never lowers it.
- `delivery_gate.py`: `gate` says why the latest delivery must not reach QA,
  read from the Verifier's record, never from the agent.
- `claimed_paths.py`: the producer's declared paths, read the same way at
  delivery and at a later re-match.

### What a failed test run says

`verification.check_tests_run` with `criteria_tests.failure_summary`: when
tests fail, the observation the task goes back with lists the failing tests
(at most 20, then `(and N more)`), the criteria results when recorded, and the
end of the output. Every part comes from the daemon's reply. A run that named
no test keeps the old wording `<command> exited <code>: <tail>`.

A test command that found no tests is not a failed test run
(`test_collection.py`): from the daemon's exit status and output, pytest's
exit 5 (no tests collected), its exit 4 when a path it was given does not
exist (`file or directory not found`) or the command line was refused, and
jest's `No tests found` or vitest's `No test files found`, make `tests_run`
`unverifiable` with `cause: "no_tests"` and the reason "the test command found
no tests at <path>" (the command's paths, under the suite's folder for a
folder's suite, which `required_commands` marks unverifiable too). Exit 4 for
a `conftest.py` that does not import stays the code's. The gate returns it
first, as `TESTS_NOT_FOUND`, one of `PLATFORM_PROBLEMS`: never a code rework
(`CODE_PROBLEMS`). The loop holds the task for a person
(`loop/platform_hold.py`). In the self-hosting owner action's run 2
(2026-11-24) the root command looked in `tests` while the plan's tests were in
`backend/tests`, and every delivery was sent back as "tests failed".

Without Git the tasks share one folder, so `test_baseline.explain` excuses a
failing test that already failed in the folder's latest run for another task
(`evidence["test_baseline"]`, recorded by `loop/folder_baseline.py`) when it is
in a file this delivery did not write. `tests_run` then passes and names that
run. A test file the delivery wrote, a cut list of failures or a baseline
without test names excuses nothing.

In QA's acceptance-test task, `unwritten_code.explain` also excuses a test
that names no criterion when it is in a file the delivery wrote, every
criterion the task covers is still unimplemented, and the runner's summary
line (`test_run.failure_reasons`) says the import of the code failed
(`ModuleNotFoundError`, `ImportError: cannot import name`). The
implementation task is judged on the same tests with no such excuse.

### Acceptance tests per criterion

- `criteria_tests.py`: the runner's per-test lines read back into criterion
  ids (`req_1_2` speaks for `REQ-1.2`); the `criteria_tests` and
  `acceptance_tests_untouched` checks.
- `acceptance.py`: plans QA's test-writing tasks. On a task branch, the tests
  judged are those of the task's lineage; the sprint's other test files are
  guarded on disk by the loop.
- `browser_smoke.py`: one Playwright smoke test per visible criterion, when
  the project serves pages.

### Commands, application and security checks

- `command_runs.py`: the `required_commands` check. A non-zero exit or a
  timeout refutes; a command that could not run (126/127, not found, a policy
  refusal, no exit status) is unverifiable. An every-delivery check whose own
  file the delivery changed (the daemon's report of its writes) refutes
  whatever it exited: a check the delivery rewrote is not evidence about it.
  Before a delivery, the same rule keeps `workspace__run_tests` from running a
  check script the task changed (`services/tools/check_scripts.py`).
- `app_check.py`: the `dependencies_installed`, `application_started` and
  `e2e_run` checks, from the daemon's install and application run.
- `security_probe.py`: the running application's answers to the daemon's probe
  as OWASP WSTG items. A credentialed CORS answer to any origin, a stack trace,
  a served git HEAD or `.env` refutes; missing headers are findings; what
  plain http cannot show is unverifiable.
- `wstg_tests.py`: tests named after a WSTG test, recorded `pass`/`fail` per
  test. The check always passes; `tests_run` already judges failures.
- `secops_checks.py`: dependency audit, secret scan and SAST, each able to
  block.

### Standards

- `standards.py`: the fixed catalogue (`docs/STANDARDS.md`) that no model,
  request or procedure edits; a procedure adds to the standards, never relaxes
  them. `check_delivery` refutes a secret-looking value; a missing test, a
  `TODO` or an unpinned dependency is a finding.
- `company_standards_check.py`: a company's own standards as pure functions of
  observations. Declared paths never satisfy `then`. A file not read back, a
  spent byte budget, missing RE2 or a run outside `container`/`bwrap` is
  `unverifiable`. Design: [`COMPANY_STANDARDS.md`](../archive/design/COMPANY_STANDARDS.md).

### Grounding

`grounding.py`, `manifests.py`, `imports.py`: the `grounded` check. A package
no registry has, or an exact version it lacks, refutes; registry answers are
observations recorded at delivery. `signatures.py` compares calls in delivered
Python files with project signatures; a mismatch is a finding, never a
refutation.

### Documentation links and cited paths

`doc_links.py` (parsing in `markdown_refs.py`): on a delivery that changes
Markdown under a docs folder (a path component `docs` or `doc`) or a README,
two checks. `docs_links`: every relative link and reference definition
resolves to a file or folder of the delivered tree, and its `#anchor` to a
heading (GitHub's slugs) or explicit anchor of the target. `docs_code_paths`:
every inline code span that names a path (a known file suffix, or a trailing
`/`) names one the tree has, from the root, from the document's folder or as
the tail of a listed path. The tree is the daemon's listing of the folder the
delivery was written into (`project_files`); a document's text counts only
when the daemon read the same bytes back (the edit tool's `tool_edit_files`,
or an answer's file whose sha256, computed here, equals the daemon's
read-back sha256 in `workspace_writes`). A broken link, or a missing path
with a `/`, refutes with the broken ones listed, and the gate sends the
delivery back (`broken documentation links`, `documentation cites missing
paths`). A cut or missing listing, a target in a folder the listing skips or
outside the project, an anchor into a document not read back, or a document
whose text was not read back is `unverifiable`. A bare file name not found is
a finding. Fenced code and absolute URLs are not followed. The risk tier is
unchanged (prose is `low`) and the checks are not required checks.

Two more checks read what the loop observed for a delivery that writes
documentation (`loop/docs_checks.py`):

- `doc_build.py`, `docs_build`: the stack profile's `docs_build` command
  (detected from MkDocs, Sphinx or Docusaurus configuration, or the
  company's) run by the daemon in the company's sandbox, kept as
  `evidence["docs_build"]` with the exit status, the first error lines and
  the sha256 the backend computed over the output. Exit 0 passes; another
  exit refutes, and the gate sends the delivery back (`documentation build
  failed`); a builder that is not installed, a run refused, timed out, without
  a sandbox or with tests off is `unverifiable`. No build command
  (`applicable: false`) gives no check at all, never a pass.
- `doc_snippets.py`, `docs_snippet_names`: the project names the delivered
  documents' fenced Python and JavaScript/TypeScript blocks import (`from pkg
  import name` with `pkg` in the listing or relative; `import { name } from
  './x'`) or call (`module.name(…)` after `import module`), parsed by
  `markdown_refs.fenced_blocks`. A submodule of the listing is found there;
  the others are looked up by the loop in the daemon's code graph
  (`workspace.related`, `lookup.kind: "definition"`), each answer kept with
  the files and sha256 read. A name with no definition is a finding, never a
  refutation (the parse reads functions, classes and methods, not a module's
  constants or a re-export); a name the daemon could not settle
  (unannounced, no answer, a partial reply) makes the check `unverifiable`.
  At most 30 names. Snippets are never run.

### Changelog fragments against the diff

`changelog_diff.py`: the `changelog_matches_diff` check, on a delivery the
daemon reports wrote a fragment (`changelog.d/<name>.md`, not its README).
In sprint 8 a fragment under `Added` claimed a flag and two functions the
base already had; the diff was a test file, a README and the fragment. The
fragment is the agent's claim; it is judged against `changelog_diff` on the
evidence, which the loop records (`loop/changelog_observe.py`): the paths the
delivery's commit changes against the merge base of the company's base (the
daemon's `workspace.git.diff`, prerequisites merged), the fragment at that
commit and the cited and changed files at the merge base
(`workspace.git.read_at`, sha256 computed by the backend), and the issue the
sprint's objective names. Rules: (a) a backticked repository path (a `/` or a
known suffix) is among the changed files, unless its sentence says `already`,
`existing`, `existed`, `unchanged`, `untouched`, `ya existía`, `ya existe` or
`sin cambios`; (b) under `Added`, a `--flag`, an identifier with `_` or in
camelCase, a `name()`, or a plain word defined at the base (`def`, `class`,
an assignment) in a cited or changed file refutes as already there; (c) the
file name's and the summary's issue is the sprint's when its objective names
exactly one (sprint 7 named a nonexistent #780), else a finding. A refuted
check sends the delivery back before QA (`changelog claims what the diff does
not show`). No observation, a cut listing, a fragment or base file not read is
`unverifiable`, and the `risk_tier` check rises to `high` (area `changelog`),
so a person decides. QA's review lists the check first and whole.

### Metrics against what the platform read

`metric_matches.py`: the `metric_matches` and `metric_threshold` checks
([`project-programs-and-screenshots.md`](../design/project-programs-and-screenshots.md),
slice 1). A metric the agent states (`claimed-metric:` lines, kept in the
run's claims as `metrics` by `loop/worker/record.py`) is a claim. A metric a
declared program printed and the backend parsed from bytes it hashed is an
observation: `observed_metrics` on the call's `program_run`
(`services/tools/program_runs.py`). The two never share a field.
`metric_matches` compares each claim with the latest observed metric of the
same name and split: a difference above the tolerance a person set in the
program's declaration refutes; no observed value is `unverifiable`; a match
passes only when the metric's provenance may support quality at the
delivery's risk tier (`contracts/program_metrics.supports_quality`: a
`committed_evaluator` metric at `low` and `medium`; `high` needs
`platform_scorer`, a later slice; `program_output` never), and is
`unverifiable` otherwise. `metric_threshold` judges each threshold a person
declared on a program that ran, with the same provenance rule; outside its
bounds refutes. A refutation of either sends the delivery back before QA
(`claimed metric refuted by the observed one`, `metric outside its declared
threshold`). No claim and no threshold give no check.

### Protected paths

`protected_check.py`: only for a company whose Owner turned on
`protected_paths_enabled`. Observed and declared paths are matched against the
running backend's list (`protected_list.json`, generated by
`scripts/protected_paths_data.py`). A delivery's deletion (status `deleted`)
counts as a change of its path. A match gives the tier `high` (area
`grader`) so a person decides at every autonomy level. Without the list it
fails closed: the check is unverifiable and the tier `high`. Design:
[`rsi-loop.md`](../design/rsi-loop.md) §3.3.

### Tests a delivery removed

`tests_removed.py`: the `tests_removed` check, for every company. For each
test file the daemon reports `written` or `deleted` (`test_*.py`,
`*_test.py`, `*.test.*`, `*.spec.*`), the test definitions on the removed and
added lines of the daemon's diff (Python `def test_…`, JavaScript
`it("…")`/`test("…")`) are summed over the whole delivery; a name removed more
often than it is added is lost. The diff is the daemon's observation of the
disk (`vscode-daemon/file_diff.py`; a deletion carries the diff of the bytes
it removed), never the producer's account. A lost name passes the check with
the names in `removed` and raises the `risk_tier` check to `high` (area
`tests`), so a person decides, as for a protected path: a test may be removed
on purpose. A test moved to another file of the same delivery is not lost. A
test file modified or deleted with no diff in the report is `unverifiable`.
A hollowed-out test that keeps its name is not caught. Design:
[`move-and-delete-files.md`](../design/move-and-delete-files.md).

### Keep, revert or unmeasured: the fitness decision

`fitness.py` (`decide`) and `fitness_checks.py` (its guards, internal):
whether a candidate build C of the factory is kept against its baseline B.
Design: [`rsi-loop.md`](../design/rsi-loop.md) §2.4–2.6.

- A pure function: no I/O, clock, model call or import of another context.
- It fails closed. Every malformed input is unmeasured; only an explicit
  `touched: []` means no protected path.
- Only observations with `produced_by: platform` and the side's own sha are
  accepted; anything else counts as missing. No hash is read from any input.
- **Revert** when C touches a protected path without a person's approval at
  C's sha naming every such path.
- Otherwise **unmeasured** when any guard is: an observation missing or
  refused, graders that differ, CI not concluded or B not green, fewer than
  three complete runs on either side for a metric.
- Otherwise **revert** when any guard regressed: a CI check failing on C or
  green on B and absent on C, fewer tests collected, a Verifier bench case B
  passes and C fails, the fake bench below 100%, or a median worse than B's by
  more than `max(threshold, spread_B)`.
- Otherwise **keep**. `scripts/rsi/decide.py` exits 0 keep, 1 revert, 2
  unmeasured.

### Reads of the record

- `touched.py`: which recorded runs wrote which files (`runs_touching`,
  `written_by_task`, `verified_writes`, `verified_writers`). Never a verdict.
- `review_changes.py`: a run's changes like a code review, from the daemon's
  diffs, a pull request or, labelled `artifact`, the producer's diff. Declared
  files come back apart as `declared_not_observed`.
- `agent_run_service.py`: stores an outside producer's run. The backend hashes
  the artifact itself and refuses one over 2 MiB.
- `context_read.py`, `sources.py`, `console_run.py`: the checks over what the
  Developer read, what research calls consulted, and a console producer's run.

## See also

- The Verifier, the gate and the risk tier at work: steps 4 to 6 of [the sprint loop](../SPRINT_LOOP.md).
- Settings: [configuration](../CONFIGURATION.md). Sandbox image and full logs: [workspace](workspace.md), [`EXECUTION_ENVIRONMENT.md`](../archive/design/EXECUTION_ENVIRONMENT.md).
- Reads and research: [tools](tools.md). Console producers: [producers](producers.md).
- Design rules: [the positioning](../POSITIONING.md). The Verifier benchmark: [testing](../TESTING.md).
- How the context was moved into its folder: [the migration record](../architecture/MIGRATION.md#step-5-evidence).
