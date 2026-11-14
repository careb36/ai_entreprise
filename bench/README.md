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
  scorecards/<stamp>.json, <stamp>.md   one per run kept as a reference
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
- `scripts/bench/schema.py` is the scorecard's schema.
- `scripts/bench/compare.py` diffs a scorecard with the previous one and exits
  1 beyond a threshold.
- `.github/workflows/bench.yml` runs it on demand and nightly on the
  self-hosted runner, with the fake.

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
| Status | `completed`, `timeout`, or `error` when a step failed or a number could not be read: a missing observation is never a green zero. |
| Tokens | The audit log's task-worker turns whose usage the provider reported (`tokens: reported`); turns without a reported count are counted apart, never estimated. |
| Models | The model served per role, from the same turns. |
| By difficulty and model (`routing`) | Each task under the difficulty it was routed by (`services/models/model_routing.estimate_difficulty` of its planned `covers`, no rework) and the model the proxy reported for its last delivery: tasks, done, done at the first attempt, rework per task, blocked share and the task's reported tokens (its other turns counted apart), with the policy's band. The backend's `services/models/routing_record.py` makes the split, the same one the Sprints page shows. |
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

## Comparing two routing policies

Run the bench once per `AGENT_MODEL_POLICY` (it reaches the backend the run
starts), each with its own label and output folder, then compare the two
cards:

```bash
AGENT_MODEL_POLICY='' BENCH_POLICY=role-defaults \
  scripts/bench/run.sh --out /tmp/bench-a
AGENT_MODEL_POLICY='{"bands": {"1-2": "<cheaper model>", "3-5": "<stronger model>"}}' \
  BENCH_POLICY=by-difficulty scripts/bench/run.sh --out /tmp/bench-b
python3 scripts/bench/compare.py /tmp/bench-b/<stamp>.json --previous /tmp/bench-a/<stamp>.json
```

The diff judges the totals and each specification as for any two runs, and
adds a table by difficulty: per band, tasks, the share done at the first
attempt, rework per task, the blocked share, the reported tokens and the
models that delivered, before → after. That table is shown, never judged: a
policy is meant to move work between models, and whether it pays is read
from first attempts and tokens per band. With the fake models every policy
gets the same answers, so only a real proxy (`BENCH_LLM_URL`) tells two
policies apart; the real-model run is not wired into CI yet. Schema v2 and
v3 cards carry the split; a v1 card (the references kept before it) is
still read and compared, without a table by difficulty.

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
into `bench/scorecards/` in a pull request.

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
   (`tests/test_bench_scorecard.py` and `tests/test_bench_prompt_weight.py`
   check the new specification too: the spec check, a test per criterion,
   the fake's plan and files, unique titles, and that the fake's files pass
   every criterion while an empty project passes none).
