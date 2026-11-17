# The self-improvement loop: a design

> **Status: proposed, 2026-11-16. Not built.** A design for the owner to
> review. It describes how La Fabrique runs on its own repository, measures
> the result, and keeps a change to itself only when the measurements say it
> is better. Every rule of [`docs/POSITIONING.md`](../POSITIONING.md) applies
> unchanged. In particular, the factory never grades its own work: what decides is
> observations made by the platform, CI and tests a person wrote. The
> self-hosting run it builds on is
> [`docs/retrospectives/2026-11-self-hosting.md`](../retrospectives/2026-11-self-hosting.md).
> Paths marked **(missing)** do not exist yet. Every other path was read on
> `main` on the date above.

## Summary

The loop has six steps: **act, measure, learn, decide, apply, keep or revert**.
Most of the parts exist already. The self-hosting harness runs a sprint on a snapshot of
this repository (`scripts/self_host/self_host.py`). The bench grades the
factory's output with tests a person wrote (`bench/`, `scripts/bench/`). The
Verifier judges every delivery from the daemon's observations
(`services/evidence/verification.py`). Procedures are already kept or
reverted by board numbers (`services/learning/procedures.py`). A sprint's
integration is reviewed and merged by a person
(`services/release/integration_review.py`, `local_merge.py`).

What is missing is the part that makes the loop safe to close:

1. **Protected paths.** A list of files the factory's own sprints cannot
   change without a person's approval: the graders, the evidence, CI, the
   positioning and the list itself. The list is enforced from the
   daemon's diff and from git, never from what the agent declares.
2. **Graders pinned to the baseline.** A candidate is graded by `main`'s
   copy of the bench and of its checks, never by its own copy.
3. **A fitness function written as code.** A pure decision over
   observations (bench scorecards repeated K times, CI check-runs read by
   commit, the Verifier bench, the finding's own measure) that answers
   `keep`, `revert` or `unmeasured`, with its reasons.
4. **A cycle record.** Each cycle's observations, decision and budget,
   audited, so a person can replay why a change was kept.
5. **Limits.** A budget per cycle, one finding per cycle, a stop after N
   cycles without a kept change, and a kill switch a person holds.

A person merges every kept change into `main` until the owner decides
otherwise (§10). The first iteration is run by hand (§7), using the open
item of the retrospective's F5 follow-up (the lint after an edit reads the
root's profile), before anything is automated.

## 0. Terms

- **Baseline (B).** The commit of `main` the cycle starts from.
- **Candidate (C).** The head commit of the pull request that carries the
  cycle's change, made by the factory's sprint and brought back from the
  snapshot.
- **Grader.** Anything whose output decides keep or revert: the bench's
  specifications and acceptance tests, the scorecard and compare scripts,
  the Verifier and its bench, the CI workflows, the self-hosting checks, and
  the fitness function itself.
- **Finding.** A defect or gap the platform observed, with a **target
  measure**: a deterministic check that fails on B and should pass once the
  finding is fixed (the "*Measure:*" line of each follow-up in the
  retrospective).
- **Observation / claim.** As in the positioning: an observation is made by
  the platform (the daemon, the backend, CI, the bench runner); a claim is
  whatever a model or a producer says, including a pull request's body.

## 1. The cycle

```
          ┌──────────────────────────────────────────────────────────────┐
          │                                                              │
  finding ─► decide ─► spec ─► act (sprint on the snapshot) ─► apply ─► PR (C)
     ▲                                  │                       │        │
     │                                  ▼                       ▼        ▼
   learn ◄── retrospective ◄── Verifier evidence      SoD, risk tier   measure
     ▲                                                integration      (B vs C,
     │                                                review           graders
     └──────────── cycle record ◄── keep (person merges) / revert ◄── from B)
```

| Step | What exists today | What is missing |
|---|---|---|
| **Act**: a sprint on the factory's own repository | `scripts/self_host/self_host.py` (steps `checkout`, `company`, `records`, `issue`, `wait`, `review`, `pack`); per-folder suites and every-delivery checks (`services/workspace/stack_roots.py`, `services/loop/delivery_suites.py`); no foundation sprint for an existing repository (`services/loop/foundation_setup.py`) | The `issue` step takes `--spec <file>` (PR 6, `scripts/self_host/spec_source.py`: checked by `spec_check`, a target measure required, run by the `measure` step). The snapshot has no remote, and bringing the result back is a manual step (README, step 7) |
| **Measure** | The bench: `scripts/bench/run.sh`, `scorecard.py` (`grade`, `collect`), `compare.py` (`DEFAULTS`: rate 0.05, rework 0.25, tokens 0.5), `budget.py`, schema v3; the Verifier bench (`bench/verifier/`, `python -m verifier.run`); the prompt weight (`services/loop/worker/prompt_budget.py` → `prompt_sections`); the cost (`services/models/run_cost.py`); the claim gap (`services/tasks/progress_snapshot.claim_gap`) | Graders pinned to B (`run.sh --graders <sha>` **(missing)**); repeated runs with medians and spread (**missing**); a wall-clock threshold in `compare.py` (**missing**); CI conclusions read by commit from GitHub's check-runs (PR 8: `scripts/rsi/cycle.py` reads them by sha through `scripts/rsi/cycle_github.py`; inside the product `evidence["ci"]` is still only the daemon's exit status, `services/workspace/workspace_daemon.py`); a real-model reference card (`bench/scorecards/real/` holds only `.gitkeep`) |
| **Learn** | `services/learning/retrospective.build` (board counts, contradictions, notes); procedures proposed and measured (`procedures.propose`, `evaluate`); retrospectives written by a person (`docs/retrospectives/`) | Findings as records with a target measure (`Finding`, **missing**). Today a finding exists only as prose |
| **Decide** | At autonomy 3 a closed sprint gets a follow-up planned by a model (`services/loop/worker/close.py` `_plan_follow_up`, `services/planning/sprint_planner.plan_self_improvement_sprint`, `AUTO_SELF_IMPROVEMENT_ENABLED`, off by default since decision 8 in §10) | Finding → specification → sprint, chosen by a deterministic rule or by a person, never by the planner's free text. For the self-hosting company the model-planned follow-up is off whatever the setting says (REQ-21, PR 3: `close._complete_sprint_and_plan_follow_up` answers `protected_paths_enabled` and audits `follow_up_sprint_skipped`) |
| **Apply** | The Verifier (`verify_agent_run`), the risk tier (`services/evidence/risk.classify`; `.github/workflows/` already raises it to high), segregation of duties (`services/platform/sod.enforce`), the integration review and its merge gate (`integration_review.py`, `local_merge.integration_merge_spec`, `companies.integration_review_required`), the history check (`backend/app/history_check.py`) | A protected-paths check (§3); a hold no Manager rule can accept; the same check on the pull request in CI |
| **Keep or revert** | Procedures: kept or reverted against a baseline of two sprints with tolerances (`REWORK_TOLERANCE` 0.25, `BLOCKED_TOLERANCE` 0.10, `GAP_TOLERANCE` 0.10); `unmeasured` is never `kept` | The same discipline for code changes: `services/evidence/fitness.py` **(missing)**, the cycle record (PR 8: written by `scripts/rsi/cycle.py`, validated by `scripts/rsi/cycle_record.py`; in the backend, `management/rsi_cycle.py` and the `rsi_cycles` table, PR 10), and a watch after the merge that opens a revert pull request **(missing)** |

### 1.1 Act

A cycle acts on one specification. The harness takes a fresh snapshot of B
(`step_checkout`: tracked files committed as `main` of a new repository,
never a worktree of the live checkout). It then uses the "La Fabrique"
company (`step_company`, the folders' suites and `EVERY_DELIVERY`: the three
`scripts/check_*.py`). It posts the cycle's specification, lets the Architect
revise the company's architecture and the PO plan the sprint (`step_issue`),
and waits (`step_wait`).
Real models are used (README, "Against a real stack"). The fake model proves
only the plumbing (retrospective, F8, last item).

### 1.2 Measure

Measurement is done twice, once on B and once on C, by the same runner and
the same graders taken from B (§2.3). Nothing the sprint's agents say is
read. The Verifier's verdicts on the sprint's deliveries are recorded in the
cycle record, but they describe the *act*, not the candidate's fitness. A
change is judged by what C does when it runs as the factory.

### 1.3 Learn

The retrospective of the cycle's sprint (`retrospective.build`) and the
cycle's own measurements produce findings. A finding is kept with the
observations that support it, such as verification ids, run ids, an e2e
report key or a scorecard. Its target measure is deterministic. The target
measure is a grader, so it is written or approved by a person and lands in
a protected path before a cycle uses it (REQ-12). Otherwise the factory
would write its own exam.

### 1.4 Decide

The next finding comes from the open findings that have a target measure
and no recorded revert of the same specification (the same rule as
`propose(skip_failed=True)` for procedures). Until the owner decides
otherwise, a person picks it. A model may draft the specification from the
finding. The specification passes `spec_check` and is approved by a person,
as any specification is. The planner's free-text follow-up
(`_plan_follow_up`) is not used for this company: with
`protected_paths_enabled` it does not run, whatever
`AUTO_SELF_IMPROVEMENT_ENABLED` says (REQ-21).

### 1.5 Apply

Every delivery goes through the existing chain: the Verifier, the risk tier,
the gate before QA, QA, and the hold at autonomy 1 or 2. The sprint's
integration is reviewed by a person who did not start the sprint
(`sod.enforce`, subject `sprint_integration_review`) and merged into the
snapshot's `main` only on a passing suite. The merged change is then brought
back as a branch and a pull request against B. That pull request is where CI
runs (§2.1, O7).

### 1.6 Keep or revert

`fitness.decide(B observations, C observations)` answers `keep`, `revert`
or `unmeasured` (§2.6). `keep` makes the pull request eligible. A person
merges it, pinned to C's sha. `revert` and `unmeasured` close it, with the
numbers recorded on the finding. After a merge, the next nightly real-model
runs on `main` are compared with the cycle's own B numbers. A regression
beyond the thresholds opens a revert pull request, which a person merges
(REQ-19). This mirrors how a procedure is measured after adoption.

## 2. The fitness function

### 2.1 What counts

| Id | Observation | Made by | Read from | Never from |
|---|---|---|---|---|
| O1 | Criteria passed / total per bench specification | `scripts/bench/scorecard.py grade`: B's acceptance tests run by the bench runner on the produced folder, in an isolated pytest (`python -I`, JUnit to a random folder) | The scorecard | The factory's own `verified` coverage, which is shown beside it and never instead of it |
| O2 | Done at the first attempt, rework per task, blocked share | The board (`services/tasks/board.py`, `rework_request_count`), read by `scorecard.py collect` | The scorecard | A reviewer's `APPROVED:` |
| O3 | Tokens reported by the provider, and cost in cents when priced | The audit log's turns (`orchestration_metrics.record_agent_turn`), priced by `run_cost` / `model_prices` | The scorecard; a cycle total by the runner | An estimate (counted apart, never summed) |
| O4 | Wall-clock per specification | The bench runner, from `start_sprint` to close | The scorecard | — |
| O5 | Prompt weight (mean estimated tokens per task, sections cut) | The backend's `prompt_sections` | The scorecard (schema v3) | — |
| O6 | Verifier bench: every planted case disbelieved, every honest control verified | `bench/verifier/run.py` on C's Verifier, with B's cases | Its exit status and output, hashed by the runner | — |
| O7 | CI on C: the conclusion of every check-run on C's head sha, and the number of tests collected per suite | GitHub Actions | `GET /repos/<owner>/<repo>/commits/<sha>/check-runs`, read by sha by the runner (`scripts/rsi/cycle.py`, every page; a run whose `head_sha` is not the side's is refused); the tests collected per suite by the runner's own `pytest --collect-only` on each side's backend (the JUnit artifacts are not read yet) | The pull request's body or any comment (PR #33 is why) |
| O8 | The finding's target measure | A person-written check run by the runner (for example a field of the self-hosting harness's JSON report, or a test) | Its exit status / the report | The sprint's own tests |
| O9 | The act's sprint: claim gap, rework, blocked share, verdicts | `procedures.board_metrics`, `progress_snapshot.claim_gap`, the Verifier | The backend | — |

O1 to O8 decide. O9 is recorded and used for trends (§4: "no improvement")
but never to keep a candidate, because it measures the producer, not the
product.

### 2.2 What does not count

- Anything a model says, including the ScrumMaster's retrospective message,
  the Reviewer agent's suggestion, the pull request's description and an
  agent's report that "tests pass". These are claims (rules 1 and 2).
- A hash or a digest the candidate or the factory under test supplies. The
  runner computes the sha256 of every scorecard, report and log it keeps
  (rule 6).
- A model's judgement of whether the change helps (rule 5).

### 2.3 Baseline and graders

- **Paired, not historical.** B and C are measured in the same window, with
  the same model per role and the same routing policy. Provider behaviour
  drifts, so a card from last week is not a baseline. A committed reference
  card (`bench/scorecards/real/`) is used only to detect drift between
  cycles.
- **Graders from B.** The runner checks out B's `bench/`, `scripts/bench/`
  and the target measure into a directory of its own and grades C's output
  with them. C's copy of those paths must equal B's, because they are
  protected (§3). If they differ, the cycle is `unmeasured`, whatever the
  numbers say.
- **Same models.** If the models served per role (scorecard `models`)
  differ between a B run and a C run, that pair is void. The proxy's
  fallback tiers can cause this (ADR 0011, superseded; the proxy's tiers
  remain).

### 2.4 Regression

A candidate **regresses** when any of these holds, comparing the median over K runs
of C with the median over K runs of B, in the totals and per specification:

| Metric | Regression when C is worse by more than | Default |
|---|---|---|
| O1 criteria rate | `max(rate, spread_B)` | rate 0.05 (`compare.py`) |
| O2 first-attempt share | `max(rate, spread_B)` | 0.05 |
| O2 blocked share | `max(rate, spread_B)` | 0.05 |
| O2 rework per task | `max(rework, spread_B)` | 0.25 |
| O3 reported tokens (relative) | `max(tokens_rsi, spread_B)` | 0.15 for a cycle (the nightly keeps 0.5) |
| O4 wall-clock (relative) | `max(time, spread_B)` | 0.25 **(missing in `compare.py`)** |
| O5 mean prompt tokens per task (relative) | only when the specification targets the prompt | `--prompt-tokens` 0.2 |
| O6 Verifier bench | any case that B passes and C fails | — |
| O7 CI | any check-run on C concluded `failure`, `timed_out`, `cancelled`, `action_required` or `stale`; a check present on B's latest green commit and absent on C; fewer tests collected in any suite than on B | — |

`spread_B` is the range (max − min) of that metric over B's K runs. A
change cannot be called better or worse by less than the baseline's own
variation.

### 2.5 Noise

- **Repeat runs.** K = 3 per side by default, run alternately (B, C, B, C,
  B, C) on the same runner and stack, so time-of-day effects fall on both
  sides.
- **Medians and spread.** The decision reads medians. The spread widens
  each threshold (above).
- **Incomplete is not zero.** A run that ends `timeout`, `budget_exceeded` or
  `error` (`scripts/bench/budget.py`, the scorecard's status) leaves its
  metrics unmeasured. With fewer than K complete runs on either side, those
  metrics are `unmeasured`, and an unmeasured guard cannot keep a candidate
  (rule 7: unknown is not absent).
- **The fake is a circuit check.** With the fake model every policy gets the
  same answers (`bench/README.md`). The fake bench must pass 30/30 criteria
  on C (binary). It is not a fitness signal.
- **Deterministic graders run once.** O6, O7 and O8 are deterministic. They
  run once per side, and a flaky CI job is re-run by a person, never by the
  factory.

### 2.6 The decision

`services/evidence/fitness.decide` **(missing)**. A pure function over the
observations (it imports only contracts). It returns `{decision, reasons,
metrics}`:

| Condition | Decision |
|---|---|
| C touches a protected path without a person's recorded approval naming those paths at C's sha | `revert` (reason `protected_path`) |
| C's graders differ from B's, or a required observation is missing | `unmeasured` |
| O7: any CI failure, a missing check, fewer tests collected | `revert` |
| O6: the Verifier bench regresses | `revert` |
| Fake bench below 100% | `revert` |
| O8: the target measure does not pass on C, or already passed on B | `revert` (or `unmeasured` when it could not run) |
| O1–O4 (and O5 when targeted): any regression (§2.4) | `revert` |
| Otherwise | `keep` |

An improvement is defined by O8, the finding's own measure. The bench is a
non-inferiority guard. A change aimed at the bench's numbers themselves (a
prompt or routing change) names the metric it improves in its specification,
and O8 is then "that metric's median better than B's by more than
`max(threshold, spread_B)`".

## 3. Protected paths

### 3.1 The list

| Pattern | Why |
|---|---|
| `bench/` (specifications, acceptance tests, `verifier/` cases, scorecards) | The exam and its answers |
| `scripts/bench/` | The grader, the schema, the thresholds (`compare.py`), the budget |
| `scripts/self_host/`, `scripts/e2e/` | The harness and checks that hold target measures |
| `backend/app/services/evidence/` | The Verifier, the risk tier, the standards check, and `fitness.py` |
| `scripts/rsi/`, `rsi/cycles/` | The keep-or-revert decision, the watch after a merge, and the cycle records (B's medians and spread) it judges against |
| The tests whose contexts include `evidence`, read from `docs/architecture/contexts.json` (`tests.files`), plus `test_verificador_benchmark.py` and `test_bench_*.py` | The tests that hold the evidence to its behaviour. Derived from data, not a hand list |
| `.github/workflows/`, `.github/CODEOWNERS` | CI is the evidence of a run |
| `docs/POSITIONING.md` | The rules |
| `backend/app/services/contracts/protected_paths.py` **(missing)** | The list itself |
| `scripts/check_architecture.py`, `scripts/check_frontend_architecture.py`, `scripts/check_file_sizes.py`, `scripts/file_size_baseline.json`, `docs/architecture/contexts.json`, `docs/architecture/frontend.json` | Ratchets CI enforces. Loosening one is grading oneself |
| `backend/app/history_check.py`, `backend/app/services/tasks/board.py` | The history's legality and the rework count O2 reads (`board.py` is already a policy file in CODEOWNERS) |
| `backend/app/services/learning/procedures.py`, `backend/app/services/management/manager_policy.py`, `backend/app/services/platform/sod.py` | Keep/revert tolerances, `ALWAYS_ESCALATE`, segregation of duties |

Most of these are already in `.github/CODEOWNERS`. The new ones (`bench/`,
`scripts/bench/`, `scripts/self_host/`, `services/evidence/` beyond
`verification.py`, `procedures.py`, `manager_policy.py`, `sod.py`,
`history_check.py`) are added there in the same pull request as the list.
A test keeps the two in step (REQ-9), the way
`test_standards.py` keeps `docs/STANDARDS.md` in step with the catalogue.

### 3.2 Where the list lives

`backend/app/services/contracts/protected_paths.py` **(missing)**: a frozen
tuple of patterns and a pure `matches(paths) -> list[(path, pattern)]`. The
precedent is `services/contracts/standards_catalogue.py`. Contracts imports
nothing, so evidence, release and management may all read it. The list
in force is **the running backend's copy**, built from `main`. It is never
read from the company's project, because the snapshot under change is
exactly what the sprint's agents can write.

The list applies to a company an Owner marks as hosting the factory
(`companies.protected_paths_enabled`, migration `0041`, set by the harness's
`company` step). Turning it off is Owner-only, audited, and added to
`ALWAYS_ESCALATE` as `change_protected_paths`, so neither the Manager agent
nor an API token can do it.

### 3.3 How it is enforced

Every layer reads paths observed by the platform. A path the producer
declares can add to the match, never remove from it (the rule
`risk.classify` already follows for `declared_risk`).

1. **At delivery: the Verifier.** A new check, `protected_paths`, in
   `services/evidence/protected_check.py` (named apart from the contract,
   since the dependency check names edges by stem), called from
   `verify_agent_run`. It reads `evidence["workspace_writes"]` (the daemon's
   report of what it wrote and read back, status `written`) plus the
   declared paths. A match raises the risk tier to high with the area
   `grader` (`risk.classify` gains it), so invariant I2 of
   [`docs/WORKFLOWS.md`](../WORKFLOWS.md) applies: a person decides. The
   check's outcome is `pass` with the match in `observation`, not `refuted`:
   touching a grader is a reason for a person, not a fault.
2. **No rule accepts it.** `manager_rules.accept_high_risk` refuses an item
   whose latest verification has a `protected_paths` match. The hold names
   the paths. Segregation of duties already refuses the approval of the
   person who started the sprint.
3. **At the integration: the daemon's diff.** Commands run by tests can
   write files that `workspace_writes` never lists. The integration's
   `workspace.git.diff` (`vscode-daemon/git_diff.py`, read-only, between
   the merge base and the pinned commit) is therefore matched too.
   `local_merge.integration_merge_spec` refuses the merge
   (`services/release/protected_merge.py`, 409 naming the paths, audited
   `integration_merge_refused_protected_paths`) unless the latest decision
   on the integration is an `approve` of that commit, recorded by a
   signed-in Owner (never an API token or the Manager agent), whose
   `protected_paths_ack` (migration `0042`) names every matched path and
   whose `diff_sha256` is the one the backend computes now. Each file's
   path and a renamed file's old path are matched; a diff the daemon cut
   short is refused. The paths the tasks declared are never read. The check
   is kept on the integration (`details["protected_paths"]`), so the
   Manager agent's inbox shows it (`:protected_awaiting_ack`) and its
   `merge_local` rule waits for the acknowledgement.
4. **On the pull request: CI.** A workflow `protected-paths.yml`
   (`scripts/protected_paths_check.py`, run from the base commit) computes the diff from git (`base...head`), never from
   the body, and fails unless an approving review by a code owner exists
   on the head sha. The backstop is GitHub's "Require review from Code
   Owners" on `main` (an owner's setting, issue #182, still pending).
5. **At the decision.** `fitness.decide` re-matches the C diff and answers
   `revert` without the approval (§2.6).

Layers 1–3 hold while the backend is honest. Layer 4 holds if the backend
is the thing that was changed. Layer 5 holds if a person approves in a
hurry and the numbers are wrong.

### 3.4 The dependency rule

| Module | Context | Imports |
|---|---|---|
| `contracts/protected_paths.py` | contracts | nothing |
| `evidence/protected_paths.py` (the check), `evidence/fitness.py` (the decision) | evidence | contracts (and platform for logging) only, as ADR 0012 requires |
| The merge refusal in `release/protected_merge.py`, called from `release/local_merge.py` | release | evidence, contracts, platform |
| The hold in `management/manager_rules.py` | management | evidence |
| The cycle record and its orchestration, `management/rsi_cycle.py` (with `rsi_cycle_steps.py`, `rsi_cycle_view.py`, `rsi_cycle_import.py`; PR 10) | management | every context (it is the owner as a client) |
| Reading GitHub's check-runs | The runner script outside `services/`: `scripts/rsi/cycle_github.py`, one adapter function (`request`) the tests replace (PR 8) | — |

`fitness.py` sits in evidence on purpose. It judges from observations, it
must not depend on what produced them, and its folder is protected.

### 3.5 What exists today and is not enough

`backend/app/services/evidence/governance.py` (`classify_artifact`) already
matches paths against `SELF_IMPROVEMENT_PROTECTED_PATHS`. Nothing calls it.
Its settings are not defined in `backend/app/config.py` (it reads them with
`getattr(..., "")`), so the list is always empty. It also decides on the
*proposed* path before the write, which is a claim, and can redirect a write
into a sandbox folder. This design replaces it: its matcher may move into
`contracts/protected_paths.py`, and the module is deleted in the same pull
request (§8, PR 1).

## 4. Limits and stop conditions

| Limit | Default | Enforced by |
|---|---|---|
| Tokens per cycle (act + 2K bench runs) | Owner-set. The act's sprint is capped by the agents' budgets in the LLM proxy, the bench by `BENCH_TOKEN_BUDGET` per run | The proxy; `scripts/bench/budget.py`; the cycle sums reported tokens and stops at the cap with `budget_exceeded`, nothing kept |
| Findings per cycle | 1 | The cycle record has one `finding_id` |
| Size of a change | Files and lines in C's diff under an Owner-set cap (proposed 20 files, 600 changed lines) | Observed from the integration's diff. Over the cap, the cycle is `unmeasured` and goes to a person |
| Cycles without a kept change | Stop after N = 3 in a row | The cycle runner reads the cycle records |
| Reverts of one finding | 2, then the finding waits for a person | The finding's outcomes |
| Cycles per day | 1 | The runner's schedule |
| Wall-clock per cycle | Owner-set (proposed 6 hours) | The runner. Sprints are stopped with `POST /v1/sprints/{id}/stop` |
| Kill switch | `RSI_ENABLED` (deployment) and the company's flag | Checked at every step boundary. Stopping also stops the running sprint and bench |
| Merge into `main` | Always a person | `merge_self_change` in `ALWAYS_ESCALATE`; branch protection |

Every cycle is recorded, the stopped and failed ones too: B and C, the
finding, the specification, the sprint, the integration and its commit, the
pull request and C's sha, each scorecard with the sha256 the runner
computed, the CI check-run ids and conclusions, the decision, its reasons,
the tokens and who merged or closed. Each step is audited as
`rsi_cycle_<step>` in the company's audit chain.

## 5. Requirements

Numbered so tests and pull requests can name them. "The runner" is the
cycle's orchestration (script first, `management/rsi_cycle.py` later).

**Act**

- **REQ-1.** When a cycle starts, the runner shall record B as the sha of
  `main` it read and shall take the snapshot from B alone.
- **REQ-2.** When a cycle starts, the runner shall refuse to start unless a
  finding with a target measure was chosen and its specification is
  approved.
- **REQ-3.** While a cycle runs, the self-hosting company shall have
  `protected_paths_enabled` set; if it is not set, then the runner shall
  stop the cycle before the sprint starts.

**Measure**

- **REQ-4.** When the runner grades a factory build, it shall use the
  bench specifications, acceptance tests, scorecard scripts and target
  measure checked out from B, and shall record their tree hash.
- **REQ-5.** If C's tree differs from B's under any protected path and no
  person's approval of those paths at C's sha is recorded, then the
  decision shall be `revert` with the reason `protected_path`.
- **REQ-6.** When the runner measures a side, it shall run the real-model
  bench K times (default 3), alternating B and C, with the same models and
  routing policy.
- **REQ-7.** If the models served per role differ between a B run and a C
  run, then the runner shall mark that pair void and count neither side.
- **REQ-8.** When the runner reads CI for C, it shall read the conclusions
  of the check-runs on C's head sha from GitHub's API and shall not read the
  pull request's body or comments.

**Protected paths**

- **REQ-9.** The test suite shall fail when a pattern in
  `contracts/protected_paths.py` is not covered by a line of
  `.github/CODEOWNERS`.
- **REQ-10.** When a delivery's `workspace_writes` or declared paths match a
  protected pattern for a company with `protected_paths_enabled`, the
  Verifier shall raise the risk tier to high with the area `grader` and name
  each matched path.
- **REQ-11.** If a held delivery has a `protected_paths` match, then the
  Manager agent shall not accept it under any rule, `accept_high_risk`
  included.
- **REQ-12.** When a target measure or a bench specification is added or
  changed, the change shall reach `main` only through a pull request a
  person approved.
- **REQ-13.** If the daemon's diff of a sprint's integration matches a
  protected pattern, then the local merge shall be refused unless the
  latest approving review of that commit records the matched paths and the
  diff's sha256.
- **REQ-14.** When a pull request's git diff matches a protected pattern,
  the `protected-paths` CI check shall fail unless a code owner approved the
  head sha.

**Decide and keep**

- **REQ-15.** When both sides are measured, `fitness.decide` shall return
  `keep` only if every row of §2.6 above `Otherwise` is false.
- **REQ-16.** If fewer than K runs completed on either side for a metric,
  then that metric shall be `unmeasured`, and an unmeasured guard shall make
  the decision `unmeasured`.
- **REQ-17.** When the decision is `keep`, the runner shall leave the merge
  to a person and the merge shall be made at C's recorded sha. If the
  pull request's head moved, then the decision shall be void.
- **REQ-18.** When the decision is `revert` or `unmeasured`, the runner shall
  close the pull request unmerged and record the decision, its reasons and
  numbers on the finding.
- **REQ-19.** While a merged cycle's change has fewer than two nightly
  real-model runs on `main` after it, the runner shall compare each with
  the cycle's B medians; if one regresses (§2.4), then the runner shall open
  a revert pull request naming the cycle.
- **REQ-20.** If a specification's text is the same as one whose cycle was
  reverted, then the runner shall not start a cycle for it unless a person
  asks.
- **REQ-21.** While a company has `protected_paths_enabled`, the
  model-planned follow-up sprint (`worker/close._plan_follow_up`) shall not
  run for it.

**Limits**

- **REQ-22.** When a cycle's reported tokens pass its budget, the runner
  shall stop the running sprint or bench, record `budget_exceeded`, and
  keep nothing.
- **REQ-23.** When N consecutive cycles (default 3) end without `keep`, the
  runner shall stop starting cycles until a person restarts it.
- **REQ-24.** When `RSI_ENABLED` is false or a person stops the cycle, the
  runner shall stop at the next step boundary, stop the running sprint
  through `POST /v1/sprints/{id}/stop`, and record the stop.
- **REQ-25.** If C's diff is over the size cap, then the decision shall be
  `unmeasured` and the cycle shall wait for a person.
- **REQ-26.** When any cycle ends, the runner shall write a cycle record
  with every observation of §4 and the sha256 of every kept file, computed
  by the runner, and shall audit it.
- **REQ-27.** The runner shall not accept a hash, a digest or a test result
  from the factory build under test as an observation.

## 6. Threat model

Status: draft, per `.claude/skills/threat-model/SKILL.md` · SDL practice 3 ·
focus: the factory gaming its own measurements.

**What is built.** A runner that drives the factory on its own repository,
measures a candidate build against `main` and recommends keeping it. A
person merges. It changes a pull request, cycle records and, once merged,
the factory itself.

**Assets.** The graders and their integrity. The evidence (verdicts,
scorecards, check-runs). `main`. The model budget. The held-out bench (if
adopted, §10).

**Flow and boundaries.**

```
 Runner ──B6── GitHub (check-runs, PR, merge)        B7: CI on the self-hosted runner
   │
   ├── Backend(C) ──B2── daemon ──B3── snapshot / sandbox
   │      └──B4── proxy ── providers
   ├── bench runner (graders from B) ── reads produced folders (B5)
   └── cycle record (Postgres, audit chain)
 B12 (new): the runner ↔ the factory build under test. The build is the
 party being recorded; its output is untrusted, as any producer's (B5).
```

| ID | Boundary | STRIDE | Threat | L | I | Sev | Mitigation |
|---|---|---|---|---|---|---|---|
| G1 | B5 | T | The sprint edits a bench acceptance test, `compare.py`'s thresholds or a scorecard to pass | H | H | Critical | New: protected paths (§3, layers 1–5); graders from B (REQ-4); C's grader tree compared with B's (REQ-5) |
| G2 | B5 | T | The sprint loosens the Verifier, the risk tier or `fitness.py` so its next deliveries pass | M | H | Critical | New: `services/evidence/` protected; the Verifier bench's cases from B (O6). Existing: CODEOWNERS on `verification.py` |
| G3 | B5 | T | Code special-cases the bench (spec names, file names) and overfits | M | M | High | New: a held-out set kept outside the repository and the company's project, never in a prompt (§10); a finding when C's diff names a bench identifier. Residual |
| G4 | B12 | T | Code the factory produced for a bench spec tampers with pytest while being graded | M | H | High | Existing: isolated pytest, JUnit to a random folder (`bench/README.md`). New: grade in the sandbox (#323) before automation |
| G5 | B6 | S | A "CI is green" claim in the PR body or a bot comment | H | M | High | New: check-runs by sha only (REQ-8). Existing rule: never claim a green build |
| G6 | B6 | T | The PR head changes after measurement (TOCTOU) | M | H | High | New: decision pinned to C's sha; merge with the expected head sha (REQ-17) |
| G7 | B5 | T | Tests deleted or skipped so CI is green | M | H | High | New: tests collected per suite must not drop (O7); the `evidence` tests protected |
| G8 | B5 | E | The sprint edits `CODEOWNERS`, a workflow, `ALWAYS_ESCALATE`, SoD or the protected list | M | H | Critical | New: all protected; `change_protected_paths` and `merge_self_change` in `ALWAYS_ESCALATE`. Existing: CODEOWNERS, risk tier high on `.github/workflows/` |
| G9 | B12 | R | A cycle kept with no record of why, or a scorecard swapped afterwards | M | M | Medium | New: cycle record with sha256 computed by the runner, audited (REQ-26, REQ-27). Existing: audit chain and RFC 3161 anchors |
| G10 | B5 | T | A hash or a test result supplied by the build under test | M | H | High | New: REQ-27. Existing: rule 6; `command_runs` hashes computed by the backend |
| G11 | B4 | I | Held-out specs or graders leak into prompts (repository map, wiki, memory) | M | M | Medium | New: held-out set outside every path the daemon serves; protected paths excluded from the code graph and wiki sources for this company (open, §10) |
| G12 | B4 | D | A loop of cycles burns the budget | M | M | Medium | New: §4 limits, REQ-22 to REQ-25. Existing: proxy budgets, `BENCH_TOKEN_BUDGET`, the credit pause |
| G13 | — | E | A model decides keep or revert | L | H | High | New: `fitness.decide` is deterministic, no model call (rule 5) |
| G14 | B5 | T | Goodhart: tokens lowered by cutting prompt sections that matter | M | M | Medium | Existing: O1/O2 guards in the same decision; `prompt_sections` shows cuts |
| G15 | B7 | E | The candidate's workflow runs on the non-ephemeral self-hosted runner with the daemon (assume breach) | M | H | High | Existing: actions pinned, `permissions:` per workflow. New: workflow changes are protected, so a person reads them first. Residual: B7 known gap |

**Tests that prove the mitigations** (each a negative test): a delivery
writing `bench/specs/01-cli-wordcount/acceptance/test_x.py` gets tier high,
area `grader` (REQ-10). The Manager agent refuses to accept it (REQ-11). A
local merge of an integration whose daemon diff touches `scripts/bench/` is
a 409 without the acknowledgement (REQ-13). `fitness.decide` returns
`revert` for C whose `bench/` tree differs from B's (REQ-5), `unmeasured`
with two complete runs out of three (REQ-16), and `revert` for a check-run
`failure` (REQ-15). A CODEOWNERS line removed fails the suite (REQ-9).

**Residual risks.** Overfitting to a public bench (G3) is only reduced. A
person approving a protected change without reading it defeats layers 2–4.
Until #182 is on, layer 4 is advisory. The owner accepts these
before automation (§10).

**Open questions.** See §10.

## 7. The first manual iteration

The finding is **"the lint after an edit reads the root's profile"**, the
item follow-up 5 of the retrospective left open (F5: "Not done: the lint
after an edit still reads the root's profile, not the edited file's
folder"), still open on `main`. The delivery's suites resolve per folder
(`services/workspace/stack_roots.py`), but a folder's profile carries no
lint command (`FOLDER_COMMANDS` is `install` and `test`) and
`services/tools/edit_lint.after_edit` reads only the root's profile
(`load_profile` → `project_profile.resolved`). In La Fabrique, whose root
has no project, an edit of `backend/app/x.py` is told "the project names no
lint command" and is never linted. The design first named F4 (ADRs were not
a product output); PR #599 delivered it before this iteration ran.

It was chosen over the other open items because its target measure is
deterministic, needs no stack and fails on `main` today; the change is one
sprint's size (two requirements, five criteria, in `services/workspace/`,
`services/tools/` and their tests); and it touches no protected path (§3.1).
The others were set aside: follow-up 2's second half (the history check
refusing a `verified` event without its verification) changes
`backend/app/history_check.py`, a protected path; F8's "the sprint reads
completed before its integration" is a race whose measure would be flaky;
F8's company standards would change `services/evidence/`, protected and
larger than a sprint; the wiki (#445) and a container run are not changes
to the code. F1 to F4, F6, F7, F8's first item and F5's other parts are
done.

**Step 0. Preconditions.**
- A real stack per `scripts/self_host/README.md` ("Against a real stack"),
  the container sandbox, provider budgets set, an Owner API token.
- Two people, or the SoD policy's solo exception recorded
  (`services/platform/sod.py`, `solo_exception`):
  one starts the sprint, the other approves the integration.
- Decide K (3) and the bench token budget per run.

**Step 1. Write the grader first (person, protected).** Done in PR 6.
- `scripts/self_host/specs/folder-lint-specification.json`, with EARS
  criteria, for example: "When an edit of a file under a folder with a lint
  command is applied, the system shall ask the daemon to run that folder's
  command in the folder, narrowed to the file, and name the folder in the
  observation". It names its target measure (`target_measure`), and
  `folder-lint-architecture.json` beside it is the revision's change.
- The target measure, `scripts/self_host/measures/test_folder_lint.py`: one
  test per criterion over the seams the specification names
  (`stack_roots.validate_overrides`, `resolve_folders`,
  `edit_lint.load_profile`, `after_edit`), with no daemon, database or
  model. `self_host.py --steps measure --spec <file>` runs it with pytest on
  a build's `backend/`, hashes the output itself and exits 3 when it fails.
- Merge that pull request (reviewed by a person). Its merge commit is **B**.

**Step 2. Measure B.**
```bash
git rev-parse origin/main                                  # B
scripts/bench/run.sh --out /tmp/rsi/B-fake                 # must be 30/30 (circuit)
(cd backend && PYTHONPATH=.:app:../bench poetry run python -m verifier.run) > /tmp/rsi/B-verifier.txt
for i in 1 2 3; do BENCH_LLM_URL=<proxy> BENCH_POLICY=default \
  scripts/bench/run.sh --out /tmp/rsi/B-real-$i; done      # alternate with C later
gh api repos/<owner>/<repo>/commits/<B>/check-runs > /tmp/rsi/B-ci.json
python3 scripts/self_host/self_host.py --checkout <dir>/B --steps checkout,measure \
  --spec scripts/self_host/specs/folder-lint-specification.json \
  --python <the backend's Poetry python> --report /tmp/rsi/B-measure.json   # target: fails, exit 3
sha256sum /tmp/rsi/B-* /tmp/rsi/B-real-*/*.json > /tmp/rsi/B.sha256
```
Expected on B: fake 30/30 (as `bench/scorecards/20261114T201804Z`), the
Verifier bench passing, every check-run `success`, the target measure
failing. There is no real-model reference yet: B's three cards become the
first one, and are committed by a person to `bench/scorecards/real/` if
complete.

**Step 3. Act.** Company "La Fabrique": `--steps issue --spec
scripts/self_host/specs/folder-lint-specification.json` checks the file
with `spec_check`, posts and approves it and the revision, and the PO plans
and starts the sprint (person A); or post it in the web app and let the
Architect revise. Then `--steps wait,review`.

**Step 4. Apply.** For each task, read the latest verification
(`GET /v1/agent_runs/{id}`): `required_commands` runs, `risk_tier`, and no
protected path in `workspace_writes`. Read the integration's diff
(`GET /v1/sprints/{id}/integration/diff`) and check its paths against §3.1
by hand. Person B approves the integration review, then `--merge`.

**Step 5. Bring back.** From the snapshot, `git format-patch B..main`.
Apply it on a branch `rsi/cycle-1-folder-lint` cut from B, push, open the pull
request. Its head is **C**. Do not read the PR body as evidence.

**Step 6. Measure C**, with graders from B:
```bash
git worktree add /tmp/rsi/graders <B>
git diff <B> <C> -- bench scripts/bench scripts/self_host scripts/e2e   # must be empty
# deploy the backend from C; run the bench with /tmp/rsi/graders' run.sh and specs
```
Then repeat step 2's commands against C (fake, Verifier bench, three real
runs alternating with B's, check-runs on C, and the target measure from
B's copy on C: `python3 /tmp/rsi/graders/scripts/self_host/self_host.py
--checkout <dir>/B --build <C's checkout> --steps measure --spec
/tmp/rsi/graders/scripts/self_host/specs/folder-lint-specification.json`).

**Step 7. Decide** by §2.6, filling this table in the pull request and in
`/tmp/rsi/cycle-1.json`:

| Guard | B | C | Pass |
|---|---|---|---|
| Protected paths in C's diff | — | none | |
| Graders' tree equal | — | yes | |
| CI check-runs | all success | all success | |
| Tests collected per suite | n | ≥ n | |
| Verifier bench | pass | pass | |
| Fake bench | 30/30 | 30/30 | |
| Target measure (folder lint) | fail | pass | |
| Criteria rate, median (spread) | | | within max(0.05, spread) |
| First attempt / blocked / rework, medians | | | within thresholds |
| Tokens, median | | | within max(0.15, spread) |
| Wall-clock, median | | | within max(0.25, spread) |

The change is on the task worker's path (the lint after an edit), but only
for a repository with folders: the bench's specifications are single-root
projects, which the specification keeps as they are, so the bench rows
should move only within noise. If they move beyond it, that is a finding about the noise
model, recorded before the decision is read.
**Keep**: a person merges the pull request at C. **Revert**: close it and
write the numbers on the finding.

**Step 8. Watch.** The next two nightly real-model runs on `main` are
compared with B's medians (`compare.py --previous`). A regression opens a
revert pull request. Write a short retrospective of the iteration
(`docs/retrospectives/`). Its findings about the loop itself become the
first findings of cycle 2.

## 8. Order of work

Each item is one pull request into `main`. Each names the REQs it closes.

| PR | Delivers | REQs |
|---|---|---|
| 0 | This design, linked from the positioning's amendment | — |
| 1 | `contracts/protected_paths.py` (the list and `matches`), CODEOWNERS extended, the test keeping them in step; `governance.py` deleted | 9 |
| 2 | The Verifier's `protected_paths` check and the `grader` area in `risk.classify`; `companies.protected_paths_enabled` (migration, Owner-only, audited); `change_protected_paths` in `ALWAYS_ESCALATE`; `accept_high_risk` refuses a match | 3, 10, 11 |
| 3 | The integration merge refusal with `protected_paths_ack` on the review; `_plan_follow_up` off for such a company | 13, 21 |
| 4 | `protected-paths.yml`: the diff from git, a code owner's approval on the head sha (the owner turns on #182) | 12, 14 |
| 5 | Bench: `run.sh --graders <sha>`, `compare.py --time`, repeated cards read as medians with spread, void pairs on different models | 4, 6, 7, 16 |
| 6 | `self_host.py --spec <file>` for the `issue` step; the first finding's specification and target measure (person-written; the folder lint, §7, since F4 was delivered by PR #599) | 2 |
| 7 | `evidence/fitness.py` with the decision table and its negative tests; `scripts/rsi/decide.py` reading cards, check-runs and the target report | 5, 15, 27 |
| — | **The first manual iteration (§7)**, with its retrospective | — |
| 8 | `scripts/rsi/cycle.py`: the steps of §7 scripted, check-runs by sha, sha256 of every file kept, the cycle JSON; no merge (`scripts/rsi/README.md`) | 1, 8, 17, 18, 26 |
| 9 | `Finding` records in learning (from the retrospective, with the target measure and outcomes); same-text reverts skipped | 20 |
| 10 | `management/rsi_cycle.py`: the cycle record and audit in the backend, the inbox item, `RSI_ENABLED`, the budget, the stop after N cycles, the size cap, `merge_self_change` in `ALWAYS_ESCALATE` | 22–26 |
| 11 | The watch after a merge: nightly comparison with the cycle's B and the revert pull request | 19 |
| 12 | Grading in the sandbox (#323), then, if the owner agrees, the held-out set | G3, G4 |

Automation of the schedule comes only after at least three manual cycles
whose decisions a person agreed with.

## 9. What was taken, and from where

Design only, no code taken.

- **Kiro by Amazon (spec-driven).** Requirements in EARS form before design
  and tasks (§5). A finding becomes a specification with acceptance
  criteria before any code (§1.4). The target measure is written first, as
  Kiro's requirements precede its tasks.
- **GitHub Copilot coding agent.** CI as the evidence of a run: the
  candidate is judged by the check-runs on its pull request's head commit
  (O7, REQ-8), not by the agent's summary.
- **OpenAI Codex cloud.** Sandboxed runs with the commands and logs cited:
  the cycle record cites every command run with its exit status and the
  sha256 of its output computed by the runner (REQ-26), and the pull
  request lists them.
- **OpenHands.** Running the product on its own repository through a user's
  entry points (already the self-hosting harness), and the trajectory as an
  event stream: the cycle is a sequence of audited `rsi_cycle_<step>` events
  that can be replayed (§4).
- **Devin.** Session replay: a cycle can be read back step by step from its
  record. Parallel agents: the B and C bench runs are independent and could
  run side by side once the bench's broker isolation allows it
  (`scripts/bench/broker.py`).
- **Factory (Droids).** Execution local or cloud with compliance: the same
  cycle runs on the self-hosted runner or a person's machine. The compliance
  side is the segregation of duties and the audit chain already here, plus
  `merge_self_change` held for a person.
- **SWE-bench Verified and OpenHands' evaluation harness** (already the
  bench's sources): graded by person-written tests, every number beside the
  harness version. Pinning graders to B extends the same idea.

## 10. Decisions of the owner (2026-11-16)

The owner decided the questions this design left open:

1. **Who merges a kept change: in stages.** A person merges the first five
   kept changes, to check the decision against reality. After them, at
   autonomy 3, the factory merges a `keep` itself when its diff touches no
   protected path and every guard passed; a diff that touches a protected
   path is always merged by a person (`merge_self_change` stays in
   `ALWAYS_ESCALATE` for that case).
2. **The protected list stays wide**, as §3.1 lists it, additions included
   (`scripts/self_host/`, `scripts/e2e/`, the ratchets, `history_check.py`,
   `board.py`, `procedures.py`, `manager_policy.py`, `sod.py`): it costs
   only a person's approval when touched, and it also covers the rules that
   judge.
3. **The company hosting the factory is marked by an Owner's flag**,
   audited; the daemon does not infer it, so the factory cannot avoid being
   recognised.
4. **Thresholds and K as proposed**: tokens 0.15 and wall-clock 0.25 per
   cycle, K = 3 runs per side, stop after N = 3 cycles without a keep, a
   20-file / 600-line cap, 6 hours, 1 cycle a day. Reviewed after two weeks
   of data (two cycles a day is the first candidate change).
5. **A held-out bench: yes.** Its specifications live outside the
   repository the factory snapshots (a separate private repository, or a
   path never served to a sprint), written by a person or by an agent in a
   session the factory does not use; three specifications to start. The
   factory sees only their results.
6. **#182: on, before PR 4.** Without "Require review from Code Owners" the
   protected-path check would be advisory, and the automation of decision 1
   would not be safe.
7. **The budget per cycle is in reported tokens**, capped at twice what the
   first manual iteration's sprint costs (measured there); cost in cents
   replaces tokens once every served model is priced.
8. **`AUTO_SELF_IMPROVEMENT_ENABLED` is off by default.** The model-planned
   follow-up ran unguarded (`governance.py` protected nothing); a company
   turns it on deliberately, and it is retired once the finding-driven
   cycle replaces it.

The first manual iteration (§7) used finding F4, which #599 has since
delivered; the finding for it is chosen and written in PR 6, among the
retrospective's open findings with a measure that fails before and passes
after.
