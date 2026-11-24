# Bench

The bench runs reference specifications through the factory and grades the
result with tests a person wrote, never with the model under test. Every
number is stated beside the scorecard's schema version, the factory's commit
and the models served. The idea comes from SWE-bench Verified and from
OpenHands' evaluation harness.

## Layout

```
bench/
  specs/<name>/
    spec.json            the specification, as POST /v1/specifications takes it
    acceptance/test_*.py the acceptance tests a person wrote; they grade the result
    fake/plan.json       the fake producer's plan (CI only)
    fake/files/...       the files the fake producer delivers
    seed/...             (optional) an existing repository to start from
    wstg_expected.json   (optional) the OWASP WSTG tests the security plan must select
  scorecards/            reference cards with the fake (<stamp>.json, <stamp>.md)
  scorecards/real/       reference cards with real models
  scorecards/humaneval/  scorecards/swebench/  scorecards/edit_formats/
  humaneval/fake/        the fake producer's answers for a few HumanEval problems
  verifier/              the Verifier benchmark
```

The scripts are in `scripts/bench/`. `run.sh` drives the run,
`scorecard.py` grades and writes the card, `compare.py` judges two cards,
and `schema.py` is the card's schema. `.github/workflows/bench.yml` runs the
bench on demand and nightly on the self-hosted runner.

## Running it

```bash
docker compose up -d postgres           # or any Postgres
BENCH_PYTHON="$(cd backend && poetry env info --path)/bin/python" \
BENCH_POSTGRES_PASSWORD=... scripts/bench/run.sh                 # every spec
scripts/bench/run.sh bench/specs/02-lib-roman --out /tmp/bench   # one spec
python3 scripts/bench/compare.py /tmp/bench/<stamp>.json --dir bench/scorecards
```

The stack is the e2e's: Postgres, NATS, the backend and the workspace
daemon. By default the models are a fake (`scripts/bench/fake_llm.py`) that
answers each specification from its `fake/` folder. With the fake, the run
fails unless every specification completes and every criterion passes; this
checks the circuit. With a real proxy, the run exits 0 once the card is
written, and `compare.py` judges the numbers.

The bench needs a NATS broker of its own. The daemon's request subjects are
not namespaced, so on a shared broker another stack's daemon could take the
bench's work. With `nats-server` installed, the runner starts one on a free
port. Otherwise set `BENCH_NATS_URL`. The default port (4222) and a broker
where a daemon already answers are refused.

`scripts/bench/run.sh --help` lists every variable. The main ones:

| Variable | Default | What it does |
|---|---|---|
| `BENCH_POSTGRES_HOST` / `_PORT` / `_USER` | `127.0.0.1` / `5432` / `factory` | Postgres server (falls back to the `E2E_` variables) |
| `BENCH_POSTGRES_PASSWORD` | `$POSTGRES_PASSWORD` | Postgres password |
| `BENCH_POSTGRES_DB` | `factory_bench` | Dropped and recreated on every run |
| `BENCH_NATS_URL` | empty | A broker of the bench's own, with JetStream; empty starts `nats-server` |
| `BENCH_NATS_SERVER` | `nats-server` on `PATH` | The binary to start |
| `BENCH_BACKEND_PORT` / `BENCH_LLM_PORT` | `18100` / `15200` | Local ports |
| `BENCH_PYTHON` | `python` | The backend's interpreter |
| `BENCH_WORKDIR` | a `mktemp` folder | Logs and produced projects |
| `BENCH_TIMEOUT` | `600` | Seconds per sprint; `0` means no limit and needs `BENCH_TOKEN_BUDGET` |
| `BENCH_TOKEN_BUDGET` | empty (no budget) | Reported tokens for the whole run |
| `BENCH_LLM_URL` | empty (the fake) | A real LLM proxy |
| `BENCH_RESEARCH_TOOLS` | `false` with the fake, else `true` | Research tools for the agents |
| `BENCH_SANDBOX` | `host` with the fake, else `container` | Where agents' code and delivery tests run |
| `BENCH_SANDBOX_IMAGE` | `fabrique-e2e-sandbox:1` | The container sandbox's image, built when missing |
| `BENCH_GRADING_IMAGE` | `$FABRIQUE_GRADING_IMAGE` | Image the acceptance tests run in |
| `BENCH_GRADE_IN_PROCESS` | unset | `1` grades on the runner's Python (local development) |
| `BENCH_POLICY` | `$AGENT_MODEL_POLICY`, else `default` | The routing policy's label on the card |
| `BENCH_SECTIONS_OFF` | empty | Prompt sections to turn off, passed as `PROMPT_SECTIONS_OFF` |
| `BENCH_EDIT_FORMAT` | empty (`auto`) | Edit format for every model: `auto`, `whole` or `search_replace` |

With a real proxy, the sandbox defaults to `container`, so a real model's
code never runs unisolated on the machine.

## What a scorecard holds

One card per run, as `<stamp>.json` and `<stamp>.md`. Per specification:

| Number | Source |
|---|---|
| Criteria passed / total | The acceptance tests, run on the produced folder. A test named `test_req_1_2_...` decides REQ-1.2. A criterion passes when it has tests and all of them pass. |
| Verified criteria | The factory's own coverage, shown beside the grade, never instead of it. |
| Tasks done at the first attempt | Done with no rework request, no person's send-back and at most one delivery. |
| Rework per task, blocked share | The board. |
| Wall-clock | From the sprint's start to its close. |
| Status | `completed`, `timeout`, `budget_exceeded` or `error`. A missing observation is never a zero. |
| Tokens | Turns whose usage the provider reported. Other turns are counted apart, never estimated. |
| Models | The model served per role. |
| Routing | Tasks by difficulty band and served model. Cost is left `null`, never 0. |
| Prompt weight | Estimated tokens (characters / 4) per prompt section the backend sent. |

The grade runs in the daemon's container sandbox: no network, project and
tests read-only. pytest starts isolated, so files in the project cannot
replace it. When the sandbox cannot grade, every criterion is `unmeasured`.

To keep a run as the new reference, commit its `<stamp>.json` and
`<stamp>.md` into `bench/scorecards/` (or `scorecards/real/`). A run that
adds specifications becomes the reference in the change that adds them.

## How compare.py judges

`compare.py` diffs a card with the previous one in `--dir`, or with
`--previous`. No previous card is not a failure. A regression, in the totals
or in any specification both cards hold:

| Metric | Flag | Default | Regression when |
|---|---|---|---|
| Criteria passed share | `--rate` | 0.05 | falls by more (absolute) |
| First-attempt share | `--rate` | 0.05 | falls by more (absolute) |
| Blocked share | `--rate` | 0.05 | rises by more (absolute) |
| Rework per task | `--rework` | 0.25 | rises by more (absolute) |
| Reported tokens (totals only) | `--tokens` | 0.5 | grows by more (relative) |
| Mean prompt tokens per task | `--prompt-tokens` | off | grows by more (relative) |
| Wall-clock | `--time` | off; `--time` alone means 0.25 | grows by more (relative) |
| Completion | | | a spec that completed before does not now |

Use `--time` only for runs on the same machine. Put it after the cards, or
write `--time=0.4`. When the two runs ran different specifications, the
totals are shown, not judged. A run the token budget cut is judged only on
the specifications both cards ran in full. The tables by difficulty, by
prompt section, and the HumanEval, SWE-bench, ablation and edit-format
blocks are shown, never judged.

**Repeated runs.** Real models are noisy, so a candidate C is compared with
a baseline B over K runs each, made alternately. Pass every card of C and
each card of B with its own `--previous`:

```bash
python3 scripts/bench/compare.py /tmp/rsi/C-real-{1,2,3}/*.json \
  --previous /tmp/rsi/B-real-1/<stamp>.json --previous /tmp/rsi/B-real-2/<stamp>.json \
  --previous /tmp/rsi/B-real-3/<stamp>.json --time
```

The i-th card of B pairs with the i-th of C. A pair whose served models
differ is void. Numbers count only from completed runs. Each metric is its
median, and a regression must exceed both the threshold and B's spread
(max − min). With fewer than K counted runs, a metric is `unmeasured`.

Exit status: 0 no regression, 1 regression, 2 a card does not match the
schema, 3 nothing regressed but a judged metric is unmeasured.

**Paired comparison.** `repeated.py` only says whether a change is worse by
more than the baseline's spread. `paired.py` compares the same items on both
sides and tests whether the difference could be chance, so it can also say a
change is better:

```bash
python3 scripts/bench/paired.py /tmp/rsi/C-real-{1,2,3}/*.json \
  --previous /tmp/rsi/B-real-1/<stamp>.json --previous /tmp/rsi/B-real-2/<stamp>.json \
  --previous /tmp/rsi/B-real-3/<stamp>.json
```

Each criterion of a spec both cards completed is a paired pass/fail, judged
with McNemar's test (exact binomial up to 25 discordant pairs). First-attempt
share, blocked share, rework per task and reported tokens are paired per
spec and judged with the Wilcoxon signed-rank test (exact up to 20 non-zero
differences), with a seeded 95% bootstrap interval of the mean change. A
metric is `better` or `worse` only when p < `--alpha` (0.05); otherwise
`no demonstrable difference`. One pair never reaches a verdict: five fixed
criteria and none broken is p = 0.0625. Pairs are void only when a role both
sides served used different models; a role one side alone called (the
ScrumMaster, when a task was blocked) is an outcome. Exit status: 1 when a
metric is significantly worse, 2 when a card does not match the schema. The
method is MLflow's paired comparison of evaluation runs (Apache-2.0), written
here with the standard library.

## Graders from a commit

`run.sh --graders <sha>` grades with the tests of that commit, not the
checkout's. `scripts/bench/graders.py` reads `bench/` and `scripts/bench/`
from git's objects, never the working tree. The card records the sha, the
tree ids and the sha256 of the files taken. The stack and the card writer
stay the checkout's.

```bash
scripts/bench/run.sh --graders "$(git rev-parse origin/main)" --out /tmp/rsi/C-fake
```

## Experiments

These compare two runs. With the fake models every arm gets the same
answers, so a fake run only checks the plumbing. Real numbers need
`BENCH_LLM_URL`.

- **Routing policies.** Run once per `AGENT_MODEL_POLICY`, each with its own
  `BENCH_POLICY` label and `--out`, then compare the two cards.
- **Prompt sections.** `scripts/bench/ablate.sh each` (or named sections)
  runs a baseline, then one run per section with that section off. The
  result is the baseline's card with an `ablation` block, in
  `bench/scorecards/ablation/`. Required sections stop it with exit 2.
- **Edit formats.** `BENCH_MODELS=a,b scripts/bench/edit_formats.sh each`
  runs once per model and format. Each row is `pass`, `fail`, `unusable`
  (no well-formed delivery) or `unmeasured`. The format used is observed,
  not claimed.
- **Project wiki.** Run with `WIKI_ENABLED=false` and `true`, then compare
  with `--prompt-tokens`. It needs specifications that run more than one
  sprint.

## The nightly real-model run

The `bench-real` job of `.github/workflows/bench.yml` runs the bench on real
models, nightly and on demand (`models: real`). It spends tokens, so it is
off until the owner turns it on.

- **Turning it on.** Set the repository variable `BENCH_REAL_MODELS` to
  `true`. Give the job the provider keys as repository secrets, under the
  names the LLM proxy reads. Only `DEEPSEEK_API_KEY` is required: without it
  the job fails by name. `QWEN_API_KEY` is optional and reaches the proxy
  only when its secret is set. Or set the variable `BENCH_LLM_URL` to a
  proxy already running on the runner.
- **Budget.** The repository variable `BENCH_TOKEN_BUDGET` (5000000 when
  unset: the six specs' DeepSeek arm used 2.79M on 2026-11-22, and a tight
  cap cuts the run and wastes what it spent) caps reported tokens for the run. Past it, the running sprint is
  stopped, the rest are skipped (`skipped: budget`, never graded) and the
  card says `budget_exceeded`. It counts only reported usage, so keep a
  spending limit at the provider too. `BENCH_TIMEOUT` is 1200 s per sprint
  there, and the job's limit is 120 minutes.
- **Cards.** The card is compared with the newest one in
  `bench/scorecards/real/`. On a regression the job opens an issue with the
  diff. The card and logs are kept as the `bench-real` artifact. A card
  becomes the reference only when a person commits it.

## The watch after a merge

`scripts/rsi/watch.py` keeps a merged self-improvement cycle measured. For
two nights after the merge, it compares each nightly real-model card with
the cycle's baseline B, using the same rule as repeated runs: a regression
must exceed both the threshold (rate 0.05, rework 0.25, tokens 0.15,
wall-clock 0.25) and B's spread.

```bash
python3 scripts/rsi/watch.py --cycle rsi/cycles/<cycle id>.json \
  --cards bench/scorecards/real --cards /tmp/rsi/tonight/<stamp>.json --repo .
```

- A person commits the cycle record under `rsi/cycles/`. A record that does
  not validate gives `unmeasured`.
- A card counts only if it used real models, was made after the merge, and
  its commit descends from the merged sha and is reachable from
  `origin/main` (checked with git, not read from the card).
- Each night is `ok`, `regressed` or `unmeasured`. Two `ok` nights close the
  watch. Exit status: 0 ok, 1 regressed, 2 unmeasured or bad input.
- On a regression, the `revert` job (`scripts/rsi/revert.py`) opens a pull
  request from `rsi/revert-<cycle id>`. Nothing merges it automatically; a
  person decides (`docs/OPERATIONS.md`).

## The reference specifications

| Specification | Shape | Criteria |
|---|---|---|
| `01-cli-wordcount` | A command-line tool over a library function | 4 |
| `02-lib-roman` | A pure library with a round-trip property | 5 |
| `03-http-api` | A JSON HTTP API in memory, as a WSGI callable | 7 |
| `04-data-job` | A CSV batch job with exact decimals and exit codes | 7 |
| `05-lib-config` | An INI-style parser checked against a typed schema | 7 |
| `06-repo-shopcart` | A change to an existing repository (from `seed/`) | 7 |

Every project uses the standard library only, so grading needs no network.

## Specifications against an existing repository

A specification with a `seed/` folder starts from that repository.
`scripts/bench/seed.py` copies it and commits it as the base, with a fixed
author and date, so the same seed always gives the same sha. The company
gets no foundation sprint and runs with `git_workflow: false`, so
deliveries land in the folder the grade reads. A symbolic link or a `.git`
entry under `seed/` stops the run. The seed alone must pass no criterion.

## HumanEval through the factory

```bash
scripts/bench/run.sh --humaneval                  # ten fixed problems
scripts/bench/run.sh --humaneval=0,2,HumanEval/7  # named problems
scripts/bench/run.sh --humaneval=all              # all 164
```

The dataset is downloaded at run time from `openai/human-eval` at a pinned
commit and refused unless its sha256 matches the pin. Nothing of it is
committed (MIT licence). Each problem becomes a one-requirement
specification seeded with its prompt and graded by HumanEval's own `check`,
which the factory never sees. The card's `humaneval` block holds pass@1 and
each problem's outcome. `BENCH_HUMANEVAL_CACHE` keeps the checked file and
`BENCH_HUMANEVAL_URL` names a mirror. Not part of the default run.

## SWE-bench Lite through the factory

```bash
BENCH_GRADE_IN_PROCESS=1 scripts/bench/run.sh --swebench                       # the five pinned instances
BENCH_GRADE_IN_PROCESS=1 scripts/bench/run.sh --swebench=pallets__flask-4992   # named instances
```

Five pinned instances (`pallets__flask-4992`, `pallets__flask-5063`,
`psf__requests-3362`, `pytest-dev__pytest-11143`,
`pytest-dev__pytest-11148`) from `princeton-nlp/SWE-bench_Lite` at a pinned
revision.

- **Licence.** The dataset declares none, and each instance's text and
  tests belong to its upstream repository. So the bench commits only the
  instance ids, row indices, revision and a sha256 per row.
- **Fetch.** Each row is fetched at run time and refused unless it hashes
  to its pin. `BENCH_SWEBENCH_INSTANCES` names a local mirror file;
  `BENCH_SWEBENCH_REPO_URL` another clone source.
- **Seed and spec.** The repository at the base commit is the seed. The
  problem statement goes whole into `SWEBENCH_ISSUE.md`.
- **Grade.** `scripts/bench/swebench_grade.py` builds a virtual environment
  per repository and version. It first checks that the base reproduces the
  instance, then runs FAIL_TO_PASS and PASS_TO_PASS on the delivery. An
  instance whose environment cannot be built or does not reproduce is
  `unmeasured`, never a pass or a fail. A delivery that plants a
  `conftest.py` or similar fails.
- **Isolation.** The tests run on the machine in that environment, not in
  the container sandbox, so `BENCH_GRADE_IN_PROCESS=1` is required.
- **Card.** The `swebench` block holds the resolve rate over measured
  instances and each outcome. Not part of the default run.

## Adding a reference specification

1. Create `bench/specs/<nn>-<name>/spec.json` with `title`, `summary`,
   `requirements` (each with `title`, `description` and `given` / `when` /
   `then` criteria), `quality` (every target with a number) and
   `constraints`. Do not write ids; the backend numbers them. Name the
   package, files and functions, since the tests import them. The
   specification must pass `services/planning/spec_check.check`.
2. Write `acceptance/test_*.py` by hand: at least one test per criterion,
   named `test_req_<n>_<m>_<what>`. Import the code inside each test. Run
   commands with `sys.executable`. These tests are never shown to the agents
   and never generated by a model.
3. Write the fake producer's answer: `fake/plan.json` (`sprint_name`,
   `goal`, `tasks` with `title`, `description`, `assignee`, `covers`,
   `depends_on` by title and `files`, and `acceptance`: QA's test file per
   requirement) and the files under `fake/files/`. Task and requirement
   titles must be unique across the bench.
4. For an existing repository, add `seed/`: a small repository whose own
   tests pass, with no `.git` and no symbolic link. The fake's `fake/files/`
   must deliver the whole package.
5. Run `scripts/bench/run.sh bench/specs/<nn>-<name>` and the backend tests
   `tests/test_bench_specs.py`, `tests/test_bench_grading.py` and
   `tests/test_bench_prompt_weight.py`.

## Grading a documentation pack

`scripts/bench/docs_pack_grade.py` (issue #698) compares a project's
documentation pack with expectations a person wrote for that project's
reference specification, and writes a card of what held:

```bash
curl -s .../v1/documentation/<pack_id> -H "Authorization: Bearer ..." > pack.json
scripts/bench/docs_pack_grade.py pack.json bench/docs_packs/<nn>-<name>.json --out card.json
```

The expectations (`la-fabrique.docs-pack-expectations/1`, the format is in
the script's docstring) name the documents the pack must hold, headings and
texts in them, whether a document may list sections with no record, the
requirements the traceability matrix must list (and whether each has a task,
a verified delivery, an integration) and the result each named check must
compute. Traceability and checks are read from the data the matrix and the
checks document carry (`data.trace`, `data.checks`), not from the Markdown,
so a pack in either language grades the same; a pack compiled before that
data is `unmeasured`, never failed.

The expectations are a person's, written from the specification and what the
run should have produced, never from the pack being graded: expectations
copied from a pack measure nothing. None are committed yet; they go under
`bench/docs_packs/`, one file per reference specification. Exit 0 on a pass,
1 on a fail, 3 when something is unmeasured, 2 on a file it cannot read.

## The Verifier benchmark

`bench/verifier/` is a separate measurement. It feeds deliveries whose
claims the evidence contradicts or cannot support to the backend's real
`services/evidence/verification.verify_agent_run`, which must believe none
of them and still verify the honest controls. It needs no stack:

```bash
cd backend && PYTHONPATH=.:app:../bench poetry run python -m verifier.run
```

`backend/tests/test_verificador_benchmark.py` runs it in the backend suite.
`bench/verifier/README.md` lists the cases.
