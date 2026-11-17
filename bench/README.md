# Bench

Reference specifications run through the factory and graded by tests a
person wrote, never by the model under test (issue #316). The idea is taken
from SWE-bench Verified (a bench graded by human-written tests) and from
OpenHands' evaluation harness (every number stated beside the harness
version: here the scorecard's schema version, the factory's commit and the
models served).

## Layout

```
bench/
  specs/<name>/
    spec.json            the specification, as POST /v1/specifications takes it
    acceptance/test_*.py the acceptance tests a person wrote; they grade the result
    fake/plan.json       the fake producer's plan (CI only, see below)
    fake/files/...       the files the fake producer delivers
    wstg_expected.json   (optional) the OWASP WSTG tests the security test
                         plan must select for it, auto and manual (issue #539;
                         backend/tests/test_wstg_plan.py)
  scorecards/<stamp>.json, <stamp>.md   one per run kept as a reference (fake)
  scorecards/real/<stamp>.json, .md     the real-model references
  verifier/              the Verifier benchmark (see below and verifier/README.md)
```

- `scripts/bench/run.sh` drives each specification through the factory on
  the e2e's stack (Postgres, NATS, backend, workspace daemon) and writes a
  scorecard. The NATS broker must be the bench's alone (see below). By default the models are a fake (`scripts/bench/fake_llm.py`);
  `BENCH_LLM_URL` points it at a real LLM proxy instead.
- `scripts/bench/scorecard.py` grades a produced project (`grade`), collects
  a sprint's numbers (`collect`) and writes the scorecard (`write`).
- `scripts/bench/broker.py` refuses a broker the bench must not share.
- `scripts/bench/prompt_weight.py` weighs the prompts the backend sent, per
  section; `scripts/bench/readers.py` reads the run's Postgres (the audit
  log's turns, each run's stored evidence).
- `scripts/bench/budget.py` waits for each sprint within `BENCH_TIMEOUT` and
  the run's token budget (`BENCH_TOKEN_BUDGET`), and writes the card's
  run-level block.
- `scripts/bench/schema.py` is the scorecard's schema.
- `scripts/bench/compare.py` diffs a scorecard with the previous one and exits
  1 beyond a threshold; with several cards per side it reads repeated runs
  (`scripts/bench/repeated.py`, see "Repeated runs, medians and spread").
- `scripts/bench/graders.py` takes the graders from a commit for
  `run.sh --graders` (see "Graders from a commit").
- `.github/workflows/bench.yml` runs it on demand and nightly on the
  self-hosted runner, with the fake, and on real models behind a kill switch
  (see "The nightly real-model run").

## The Verifier benchmark

`bench/verifier/` (moved from `backend/eval/verificador/` by ADR 0015) is a
measurement of its own, apart from the reference specifications: deliveries
whose claims the evidence contradicts or cannot support, fed to the backend's
real `services/evidence/verification.verify_agent_run`, which must believe
none of them and still verify the honest controls. It needs no stack, only
the backend's interpreter:

```bash
cd backend && PYTHONPATH=.:app:../bench poetry run python -m verifier.run
```

`backend/tests/test_verificador_benchmark.py` runs it in the backend suite
(`conftest.py` appends `bench/` to `sys.path`), and `backend-tests.yml` runs
on a change under `bench/verifier/`. `verifier/README.md` lists the cases.

## What a run measures, per specification

| Number | Where it comes from |
|---|---|
| Criteria passed / total | The acceptance tests in `acceptance/`, run by the bench on the produced folder: an observation. A test named `test_req_1_2_...` decides REQ-1.2; a criterion passes when it has tests and all of them pass. pytest starts isolated (`python -I`) and reaches the project only at the end of `sys.path`, so a `pytest.py`, `sitecustomize.py` or `conftest.py` in the project cannot replace it; its JUnit report goes to a folder the grader makes with a random name, read only once pytest has finished (exit 0 or 1). The tests still import the project, so its code runs inside the grader: until the grade runs in the sandbox (#323), the bench trusts it not to tamper with pytest from within. |
| The factory's own verified criteria | The specification's coverage (`verified`), shown beside the grade, never instead of it. |
| Tasks done at the first attempt | Done, with no rework request on the board (a quality gate's, the Verifier's or QA's `NEEDS WORK:`, read by `services/tasks/board.py`), no person's send-back and at most one delivery (`AgentRun`) recorded. The comments decide: a delivery the gate sent back before it was ingested, or one recognised as a repeat, adds no run but leaves its request. |
| Rework requests per task, blocked share | The board: `board.rework_request_count` per task, as the sprint list counts them. |
| Wall-clock | From `start_sprint` to the sprint's close, measured by the runner. A sprint still open at `BENCH_TIMEOUT` is stopped first (`POST /v1/sprints/{id}/stop`), and graded as it stands (`timeout`). |
| Status | `completed`, `timeout`, `budget_exceeded` (the run passed its token budget while this sprint ran, and it was stopped), or `error` when a step failed or a number could not be read: a missing observation is never a green zero. |
| Tokens | The audit log's task-worker turns whose usage the provider reported (`tokens: reported`); turns without a reported count are counted apart, never estimated. |
| Models | The model served per role, from the same turns. |
| By difficulty and model (`routing`) | Each task under the difficulty its first attempt was routed by (the worker's first `estimated` event, `services/models/difficulty.py`: criteria, the architecture's modules and files, dependencies, attempt and rework; read by `readers.difficulty_estimates`; a task without one is recomputed from its planned `covers`) and the model the proxy reported for its last delivery: tasks, done, done at the first attempt, rework per task, blocked share and the task's reported tokens (its other turns counted apart), with the policy's band and the estimate's own band (`difficulty_band`: `low`, `medium`, `high`). The cost in cents (`cost_cents`) is left unknown (`null`, counted in `cost_unknown`), never 0: the bench prices no turn. The backend's `services/models/routing_record.py` makes the split, the same one the Sprints page shows. |
| Policy | The routing policy's label on the card (`policy`): `BENCH_POLICY`, else the `AGENT_MODEL_POLICY` the run passed to the backend, else `default`. |
| Prompt weight (`prompt`) | Each task's latest task-worker run's `prompt_sections`, read from its stored verification evidence (#443, `services/loop/worker/prompt_budget.py`): the backend's own measure of the Developer prompt it sent. Per spec and in total: the prompts measured, the estimated tokens in all and per task (the system prompt apart), per section the tokens sent and their share, the prompts with a section cut and which sections, and the prompts that carried `related_code` (#438). Estimated tokens are characters / 4, never the provider's count (`tokens` above). A run with no measure (a backend from before #443) is counted apart (`prompt_sections_missing`), never as a prompt of zero tokens. |

## Running it

```bash
docker compose up -d postgres           # or any Postgres
BENCH_PYTHON="$(cd backend && poetry env info --path)/bin/python" \
BENCH_POSTGRES_PASSWORD=... scripts/bench/run.sh                 # every spec
scripts/bench/run.sh bench/specs/02-lib-roman --out /tmp/bench   # one spec
python3 scripts/bench/compare.py /tmp/bench/<stamp>.json --dir bench/scorecards
```

The bench needs a NATS broker of its own. The daemon's request subjects
(`workspace.generate`, `workspace.execute`, ...) are not namespaced and every
daemon joins the same queue group, so on a shared broker another stack's
daemon could take the bench's deliveries and test runs. With `nats-server`
installed (or `BENCH_NATS_SERVER` naming it) the runner starts one on a free
port; otherwise set `BENCH_NATS_URL` to a broker with JetStream that nothing
else uses. The default port (4222) and a broker where a daemon already
answers are refused.

`scripts/bench/run.sh --help` lists every variable. With the fake, the run
fails unless every specification completes and every criterion passes: that
is the circuit check. With a real proxy it exits 0 once the scorecard is
written, and `compare.py` judges the numbers (`--rate`, `--rework`,
`--tokens` set the thresholds).

Wall-clock is shown and not judged unless `--time` is passed: alone it
judges a growth of more than 25% (0.25, the owner's wall-clock threshold
in `docs/design/rsi-loop.md` §10, decision 4), and `--time 0.4` sets
another, in the totals and per specification. Wall-clock depends on the
machine, so use `--time` only for runs made on the same one. Put `--time`
after the cards, or write `--time=0.4`, so the flag does not take a card's
path as its value.

## Graders from a commit

`scripts/bench/run.sh --graders <sha>` grades with the tests of that commit,
not with the checkout's own (REQ-4 of `docs/design/rsi-loop.md`). Before the
stack starts, `scripts/bench/graders.py` reads `bench/` (the specifications,
their acceptance tests, the fake's answers) and `scripts/bench/` (the
grader) from the commit's objects (`git ls-tree`, `git cat-file`) into
`BENCH_WORKDIR/graders`. The working tree is never read, so a test edited,
added or removed in the checkout does not reach the grade. A symbolic link
or a submodule under those paths is refused. Then:

- the specifications run are that commit's: without `SPEC_DIR`, every one
  it holds; a `SPEC_DIR` names one by its folder's name, and a name the
  commit does not hold stops the run;
- the fake producer answers from that commit's `fake/` folders;
- the acceptance tests run with that commit's `scorecard.py grade`;
- the card carries `graders`: the full `sha`, git's tree id of `bench` and
  of `scripts/bench` at that commit (`trees`, the same values `git rev-parse
  <commit>:bench` gives, so another commit's copy can be checked against
  them), and the `sha256` of the files taken, computed by the runner from
  their bytes. The card's Markdown and `compare.py`'s header name the
  graders' commit; a card without the block was graded by the build's own
  copy.

The stack, the collection of the numbers and the writing of the card stay
the checkout's: they read the factory under test, not the exam. The block is
optional and additive in schema v3; older cards are read as before.

```bash
B=$(git rev-parse origin/main)
scripts/bench/run.sh --graders "$B" --out /tmp/rsi/C-fake
```

## Repeated runs, medians and spread

One run per side is noisy with real models, so a candidate (C) is compared
with a baseline (B) over K runs each, made alternately (B, C, B, C, B, C;
`docs/design/rsi-loop.md` §2.5). Give `compare.py` every card of C and each
card of B with its own `--previous`:

```bash
python3 scripts/bench/compare.py /tmp/rsi/C-real-{1,2,3}/*.json \
  --previous /tmp/rsi/B-real-1/<stamp>.json --previous /tmp/rsi/B-real-2/<stamp>.json \
  --previous /tmp/rsi/B-real-3/<stamp>.json --time
```

More than one card on either side, or `--runs K`, reads repeated runs
(`scripts/bench/repeated.py`):

- **Pairs.** Each side is put in the order of its stamps, and the i-th card
  of B is paired with the i-th of C. A pair whose models served per role
  differ (the card's `models`, over every specification) is **void**:
  neither card is counted (REQ-7). A card without a partner is not counted.
- **Complete runs.** A specification's numbers count only from runs where
  it `completed`, and the totals only from runs where every specification
  completed and the budget did not cut the run. A `timeout`, `error` or
  `budget_exceeded` leaves its numbers out; they are never read as zero.
- **K.** `--runs K`, by default the number of cards on the larger side.
  With fewer than K complete, counted runs on either side, a metric is
  `unmeasured`: the table shows `unmeasured` and the diff lists it under
  "Unmeasured (never a pass)" (REQ-16).
- **Medians and spread.** Each metric is read as its median over the runs,
  and its spread is the range (max − min) over B's runs. C regresses on
  criteria passed, first attempt or blocked share when it is worse than B's
  median by more than `max(--rate, spread_B)`, on rework per task by more
  than `max(--rework, spread_B)`, and on reported tokens (totals only),
  wall-clock (`--time`) or prompt tokens (`--prompt-tokens`) when it grew by
  more than `max(threshold, spread_B / median_B)`. So a change cannot be
  called worse by less than the baseline's own variation.
- **Completion.** A specification that completed in fewer counted pairs on
  C than on B is a regression (pairs where the budget stopped it on either
  side are left out).

The exit status is 1 on a regression, 2 when a card does not match the
schema (or no `--previous` was given), 3 when nothing regressed but a judged
metric is unmeasured (not a pass), and 0 otherwise. The tables by
difficulty and by prompt section are shown only for two single cards. One
card per side keeps the behaviour above (the nightly comparison with a
reference); with `--runs 1` it is read as a pair, void if its models differ.

## Comparing two routing policies

Run the bench once per `AGENT_MODEL_POLICY` (it reaches the backend the run
starts), each with its own label and output folder, then compare the two
cards:

```bash
AGENT_MODEL_POLICY='' BENCH_POLICY=role-defaults \
  scripts/bench/run.sh --out /tmp/bench-a
AGENT_MODEL_POLICY='{"bands": {"low": "<cheaper model>", "medium": "<cheaper model>", "high": "<stronger model>"}}' \
  BENCH_POLICY=by-difficulty scripts/bench/run.sh --out /tmp/bench-b
python3 scripts/bench/compare.py /tmp/bench-b/<stamp>.json --previous /tmp/bench-a/<stamp>.json
```

The diff judges the totals and each specification as for any two runs, and
adds a table by difficulty: per score, tasks, the share done at the first
attempt, rework per task, the blocked share, the reported tokens and the
models that delivered, before → after; then the same numbers, with done and
the cost when known, by difficulty band (`low`, `medium`, `high`) and by
served model (`scripts/bench/routing_split.py`). These tables are shown, never judged: a
policy is meant to move work between models, and whether it pays is read
from first attempts and tokens per band. With the fake models every policy
gets the same answers, so only a real proxy (`BENCH_LLM_URL`) tells two
policies apart; the nightly real-model run uses the default policy. With the
fake, a run per policy still checks the circuit end to end: the backend reads
the policy, stores each task's estimate and routing (`estimated` events, the
run's evidence), and the two cards compare band by band and model by model
(the fake answers `fake-model` whatever was requested, so the served model is
the same on both sides). A policy can also be set for one company on the
Models page instead of `AGENT_MODEL_POLICY`; the bench creates its own
companies, so it uses the environment variable. Schema v2 and v3 cards carry
the split; a v1 card (the references kept before it) is still read and
compared, without a table by difficulty, and a card without the band and
cost fields places its rows by score.

## Reading the prompt weight

Schema v3 cards add the weight of the prompts the backend sent. The card's
Markdown has a "Prompt weight" section: one line per spec and for the total
(prompts measured, estimated tokens per task, the system prompt apart, the
prompts with sections cut and which, the prompts with related code, the runs
without a measure), then the sections by weight with their share. A section
the budget cut was not sent: it is counted under the cut ones, not in the
weight. `compare.py` adds a table of the sections' estimated tokens and share,
before → after, with the mean per task, the cut prompts and the related-code
prompts. Like the table by difficulty it is shown, never judged: a change to
the prompt is meant to move its weight. Only `--prompt-tokens` (off by
default; relative, 0.2 = +20%) judges the mean estimated tokens per task, in
the totals and per spec, and only when both cards measured prompts. Version
1 and 2 cards are still read and compared, without the weight.

To keep a run as the new reference, copy its `<stamp>.json` and `<stamp>.md`
into `bench/scorecards/` in a pull request. A run that adds specifications
moves the totals (more tasks, more tokens), so it is kept as the reference
in the pull request that adds them: `20261114T201804Z` is the first v3 card,
the five specifications with the fake.

## The project wiki against vector memory alone

The project wiki (issue #445, `docs/design/PROJECT_WIKI.md`) is measured by
the prompt tokens and the rework per task, with the wiki on and off. Every
prompt measure already carries its sections; the card's prompt object also
counts `wiki_prompts` (prompts that sent a `wiki` section) and
`memory_prompts` (prompts that sent the vector memory, the wiki's fallback),
and `compare.py` shows both, before → after, beside the per-section table and
rework per task (shown, never judged; `--prompt-tokens` judges the mean).
Both counts are optional: an older card reads as zero.

```bash
WIKI_ENABLED=false BENCH_POLICY=wiki-off scripts/bench/run.sh --out /tmp/bench-off
WIKI_ENABLED=true  BENCH_POLICY=wiki-on  scripts/bench/run.sh --out /tmp/bench-on
python3 scripts/bench/compare.py /tmp/bench-on/<stamp>.json --previous /tmp/bench-off/<stamp>.json --prompt-tokens 0.2
```

The wiki is compiled when a sprint closes and read by the next sprint's
tasks, so the comparison needs specifications that run more than one sprint
(or a second pass over the same company). With the fake models the compiled
pages are whatever the fake answers (usually refused for want of known
citations), so a fake run only checks that the counts flow; the real
measure is the nightly real-model run (`BENCH_LLM_URL`) made twice, with the
flag off and on, compared as above: lower `tokens_mean` with the same or
lower `rework_per_task` is the result the issue asks for.

## The nightly real-model run

`.github/workflows/bench.yml` has a second job, `bench-real`, apart from the
fake one: the same bench on real models, nightly (with the fake job) and on
demand (Run workflow, `models: real`; `fake`, the default, runs only the fake
job). It spends tokens, so it is off until the owner turns it on.

**Turning it on.** Set the repository variable `BENCH_REAL_MODELS` to `true`
(Settings, Secrets and variables, Actions, Variables). Unset, or any other
value, means off: the job is skipped and nothing is spent. Then give the job
the provider keys as repository secrets, under the names the LLM proxy reads
(`llm-proxy/app.py`, `docker-compose.yml`): `GEMINI_API_KEY` and
`DEEPSEEK_API_KEY` are required, `QWEN_API_KEY` and `NEBIUS_API_KEY` are
passed when set. A required secret that is missing fails the job with its
name, before anything starts. The job builds the proxy from `llm-proxy/`
(its Dockerfile, as `docker compose` does), passes the keys to the container
from the step's environment and waits for its `/health`. To use a proxy that
already runs on the runner instead, set the repository variable
`BENCH_LLM_URL` to its address: when set, it is preferred and no key is
needed by the job.

**The budget.** The run stops at a budget of reported tokens, summed over
every specification: the repository variable `BENCH_TOKEN_BUDGET`, 300000
when unset (`run.sh` takes it as `BENCH_TOKEN_BUDGET`; empty there means no
budget). While each sprint runs, `scripts/bench/budget.py` reads what the
whole run has spent so far from the audit log, with the same reader as the
scorecard (only turns whose usage the provider reported). When the total
passes the budget, the running sprint is stopped (`POST
/v1/sprints/{id}/stop`, as at `BENCH_TIMEOUT`) and graded as it stands
with the status `budget_exceeded`, the remaining specifications are skipped,
and the card is written with a run-level block (`run`): the status
`budget_exceeded`, the budget, the tokens spent, and the specifications that
did not run, listed as `skipped: budget`, never graded as zero. A turn whose
usage the provider did not report has no count, so it cannot be budgeted:
the card counts those turns apart and says so. The budget caps what the
provider reported, not what it billed: keep a spending limit at the provider
too. `BENCH_TIMEOUT` (a repository variable, 1200 seconds when unset) bounds
each sprint, and the job's `timeout-minutes` (120) the whole run. Stopping
it altogether is in `docs/OPERATIONS.md` (the kill switch).

**The cards.** The real-model card is compared with the newest card in
`bench/scorecards/real/`, never with the fake ones in `bench/scorecards/`.
With none there yet, the job writes its card, says there is no reference and
passes. A card that the budget cut is not judged in its totals, which cover
fewer specifications: `compare.py` says the run was cut, shows its numbers,
lists what it skipped and judges only the specifications both cards ran in
full. On a nightly regression the job opens an issue titled "Bench
regression (real models) <date>" with the diff. The card and the logs are
kept as the artifact `bench-real`.

**Keeping a reference.** A card becomes the reference only when a person
commits it: download the `bench-real` artifact of a run worth keeping (a
complete run, not one the budget cut) and copy its `<stamp>.json` and
`<stamp>.md` into `bench/scorecards/real/` in a pull request. Nothing in the
workflow commits a card.

## The watch after a merge

A merged self-improvement cycle stays measured
(`docs/design/rsi-loop.md` §1.6, §7 step 8; REQ-19). `scripts/rsi/watch.py`
takes a cycle record and the nightly real-model scorecards made on `main`
after the cycle's merge, and while the merged change has fewer than two such
nights it compares each with the cycle's own baseline B — the medians and
spread the record carries — with the same rule `scripts/bench/repeated.py`
and `services/evidence/fitness.py` implement (the watch imports them, it
does not copy them): a regression must exceed both the threshold of decision
4 (rate 0.05, rework 0.25, tokens 0.15 and wall-clock 0.25 relative,
`fitness.THRESHOLDS`) and B's own spread. The wall-clock is judged: the
nightly runs on the same self-hosted runner.

```bash
python3 scripts/rsi/watch.py --cycle rsi/cycles/<cycle id>.json \
  --cards bench/scorecards/real --cards /tmp/rsi/tonight/<stamp>.json --repo .
```

**The cycle record** (schema and validator: `scripts/rsi/cycle_record.py`,
the contract PR 8's `scripts/rsi/cycle.py` conforms to) carries the cycle's
id, the sha merged into `main` and when, the graders' sha, the models B was
served per role, and B's medians and spread per metric (in the totals, and
per specification for the ones B measured in full). A person commits the
record under `rsi/cycles/<cycle id>.json`. A record that does not validate
is refused: the watch answers `unmeasured` (exit 2), never `ok`.

**Which cards count.** A card counts only when its served model is real
(`llm` is not `fake`), its stamp is strictly after the merge, its `commit`
is a full sha that exists in `--repo`, has the merged sha as an ancestor and
is reachable from the trusted tip of main (`--main-ref`, default
`origin/main`) — verified with `git merge-base --is-ancestor`, never on the
card's word, so a run on a feature branch descended from the merge does not
count — and it carries no
`graders` block or one whose `sha` is the record's `graders_sha` (a card
graded by another commit's graders does not count). Everything else is
excluded and named in the output. A counted card that measures nothing still
makes its night `unmeasured`, never `ok`: a card served by different models
than B's (a void pair, REQ-7), a card that names no served model at all, a
run the token budget cut, or a metric B or the card did not measure are
unmeasured (the tokens are unmeasured when a turn did not report usage, or
none did: `scripts/rsi/decide.py`'s rule, imported); a specification B measured in full that did not complete is a
regression. The cards of one UTC day are one night.

**The decision.** Per night: `ok`, `regressed` or `unmeasured`, with the
reasons and the numbers. The window closes on the second `ok` night: later
nights are excluded and named, and do not reopen a closed watch. Overall:
`regressed` when a night in the window regressed, `ok` when two nights were
watched with no regression — the cycle closes — and `unmeasured` otherwise. Exit status 0 ok, 1 regressed, 2 unmeasured or
bad input; the JSON also carries the sha256 of every file read, computed by
the script.

**The revert pull request.** On a regression, `scripts/rsi/revert.py`
reverts the merged sha (with `-m 1` when it is a merge commit) on a branch
`rsi/revert-<cycle id>` cut from `origin/main` in a worktree of its own, so
one pull request reverts exactly one cycle however many regressed, pushes it and opens a pull request naming the
cycle, with the nights, the numbers and the reasons, through the gh API with
the workflow's token. **It never merges anything: a person reviews and
merges the revert** (`docs/OPERATIONS.md`). A revert that does not apply
cleanly is aborted and left to a person (exit 2, nothing pushed); a revert
pull request already open for the cycle is not opened twice, and when gh
cannot say whether one is open nothing is opened.

**The nightly.** The `watch` job of `.github/workflows/bench.yml` runs
after `bench-real` whenever that job ran on `main` (a `workflow_dispatch`
from a branch runs neither the watch nor the revert): it takes tonight's card from the
`bench-real` artifact plus the committed references in
`bench/scorecards/real/`, watches every record under `rsi/cycles/`, and puts
each night's decision in the job summary. A cycle that regressed goes to the
`revert` job, the workflow's only writer (`contents: write` for the branch
push, `pull-requests: write` for the pull request).

## The reference specifications

| Specification | Shape | Criteria |
|---|---|---|
| `01-cli-wordcount` | A command-line tool (`python -m wordcount`) over a library function | 4 |
| `02-lib-roman` | A pure library with a round-trip property | 5 |
| `03-http-api` | A JSON HTTP API in memory, a WSGI callable (`notesapi.app.make_app`); the tests call it with a constructed environ, no socket | 7 |
| `04-data-job` | A CSV batch job (`python -m salesjob input.csv`): sums per region in exact decimals, JSON or CSV out, bad rows reported on stderr by line, exit codes 0, 1 and 2 | 7 |
| `05-lib-config` | A parser with validation errors (`confcheck`): an INI-style file read with the offending line named, then checked against a typed schema, every problem reported in one call | 7 |

Every project is standard library only: the grade needs no network and no
package. A specification against an existing repository (the produced
project starting from a checkout rather than a folder holding a README) is
next: `run.sh` starts every company in an empty folder today, so that needs
runner support before such a specification can be added.

## Adding a reference specification

1. Create `bench/specs/<nn>-<name>/spec.json` with `title`, `summary`,
   `requirements` (each with a `title`, a `description` and `criteria` of
   `given` / `when` / `then`), `quality` (every target with a number) and
   `constraints`. The ids are not written: the backend numbers requirements
   and criteria in order (REQ-1, REQ-1.1, ...). Name the package, its files
   and its functions in the description or the constraints, since the
   acceptance tests import them. The specification must pass the check
   (`services/planning/spec_check.check`) so it can be approved; a backend test makes
   sure it does.
2. Write `acceptance/test_*.py` by hand, at least one test per criterion,
   named `test_req_<n>_<m>_<what>`. Import the code under test inside each
   test, so a missing module fails that test and not the file. The project
   is on `PYTHONPATH`; run a command with `sys.executable`. These tests are
   never shown to the agents and never generated by a model.
3. Write the fake producer's answer, so CI can run the circuit without a
   model: `fake/plan.json` (`sprint_name`, `goal`, `tasks` with `title`,
   `description`, `assignee`, `covers`, `depends_on` by title and the `files`
   each delivers, and `acceptance`: per requirement, the test file QA's
   test-writing task delivers, `tests/test_req_<n>.py`), and each of those
   files under `fake/files/`. Task titles and requirement titles must be
   unique across the bench. The fake's tests are QA's, not the grading ones:
   QA's files import inside each test, since they land before the code, and
   a task's own unit tests import only what that task or an earlier one
   delivers.
4. Run `scripts/bench/run.sh bench/specs/<nn>-<name>` and the backend suite
   (`tests/test_bench_specs.py`, `tests/test_bench_grading.py` and
   `tests/test_bench_prompt_weight.py`
   check the new specification too: the spec check, a test per criterion,
   the fake's plan and files, unique titles, and that the fake's files pass
   every criterion while an empty project passes none).
