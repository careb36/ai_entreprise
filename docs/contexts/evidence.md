# The evidence context

Judging a run from what was observed: the Verifier, the risk tier, the
standards, acceptance tests and the fitness decision of the self-improvement
loop. Read this before changing `backend/app/services/evidence/`; each
module's docstring has the detail.

- **Folder:** `backend/app/services/evidence/`
- **May import:** [contracts](contracts.md), [platform](platform.md). Nothing else: it reads the daemon's reply as data and never calls the daemon, the loop, the tools or a producer.
- **Interface:** `acceptance`, `agent_run_service`, `app_check`, `browser_smoke`, `command_runs`, `company_standards_check`, `console_run`, `context_read`, `criteria_tests`, `delivery_gate`, `fitness`, `manifests`, `protected_check`, `review_changes`, `risk`, `signatures`, `sources`, `standards`, `touched`, `verification`, `wstg_tests`.
- **Modules:** `acceptance`, `agent_run_service`, `app_check`, `browser_smoke`, `claimed_paths`, `command_runs`, `company_standards_check`, `console_run`, `context_read`, `criteria_tests`, `delivery_gate`, `fitness`, `fitness_checks`, `grounding`, `imports`, `manifests`, `protected_check`, `required_checks`, `review_changes`, `risk`, `secops_checks`, `security_probe`, `signatures`, `sources`, `standards`, `test_baseline`, `touched`, `unwritten_code`, `verification`, `wstg_tests`.

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

### Protected paths

`protected_check.py`: only for a company whose Owner turned on
`protected_paths_enabled`. Observed and declared paths are matched against the
running backend's list (`protected_list.json`, generated by
`scripts/protected_paths_data.py`). A match gives the tier `high` (area
`grader`) so a person decides at every autonomy level. Without the list it
fails closed: the check is unverifiable and the tier `high`. Design:
[`rsi-loop.md`](../design/rsi-loop.md) §3.3.

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
