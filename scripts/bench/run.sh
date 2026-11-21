#!/usr/bin/env bash
# The bench: drive each reference specification through the factory against
# a real stack and record a scorecard graded by tests a person wrote.
#
#   scripts/bench/run.sh [--out DIR] [--graders SHA] [SPEC_DIR...]
#   scripts/bench/run.sh --humaneval[=IDS] [--out DIR]
#   BENCH_GRADE_IN_PROCESS=1 scripts/bench/run.sh --swebench[=IDS] [--out DIR]
#
# --humaneval runs HumanEval problems instead of the reference specifications
# (scripts/bench/humaneval.py): the dataset downloaded from openai/human-eval
# at a pinned commit and refused unless its sha256 is the pinned one, each
# problem posted as a one-requirement specification starting from its prompt
# (seed/solution.py), and graded with HumanEval's own tests in the same
# sandbox, never shown to the factory. IDS: default (ten fixed problems; with
# the fake, the ones it has answers for), all, or ids (0,2,HumanEval/7). The
# card (in bench/scorecards/humaneval unless --out) carries the humaneval
# block: pass@1, each problem's outcome, tokens. Never with SPEC_DIR or
# --graders, and never part of the default run.
#
# --swebench runs pinned SWE-bench Lite instances (scripts/bench/swebench.py):
# each row fetched at run time and refused unless it hashes to its pin, the
# repository cloned at the base commit as the seed, the problem statement as
# the specification, and the instance's FAIL_TO_PASS and PASS_TO_PASS tests
# run by the scorer after the test patch (scripts/bench/swebench_grade.py),
# in a virtual environment on this machine: it needs BENCH_GRADE_IN_PROCESS=1.
# IDS: default (the five pinned) or pinned ids, comma-separated. The card (in
# bench/scorecards/swebench unless --out) carries the swebench block. Never
# with SPEC_DIR, --graders or --humaneval, and never part of the default run.
#
# Without SPEC_DIR, every bench/specs/*/ holding a spec.json. With --graders,
# the specifications, their acceptance tests, the fake's answers and the
# grader (bench/ and scripts/bench/) are read from that commit's objects into
# BENCH_WORKDIR/graders (scripts/bench/graders.py), never from this checkout:
# a SPEC_DIR names a specification of that commit by its folder's name, the
# acceptance tests run with that commit's scorecard.py grade, and the card
# records the commit, git's tree id of each path and the sha256 of the files
# taken (graders). The stack, the collection of the numbers and the card are
# this checkout's (REQ-4, docs/design/rsi-loop.md). The stack is the
# e2e's (scripts/e2e/run.sh): a Postgres you provide, a NATS broker of the
# bench's own, the backend, the workspace daemon and, by default, a fake LLM proxy
# (scripts/bench/fake_llm.py) that answers each spec from its fake/ folder.
# Per specification:
#
#   1. a company (autonomy 2, Sprint 0 left planned so it costs nothing) with
#      a PO, a Developer, QA and a ScrumMaster, writing to its own folder:
#      empty but for a README, or, when the spec has a seed/ folder, that
#      existing repository, copied and committed as the base before the sprint
#      (scripts/bench/seed.py), with the Git workflow off so the deliveries
#      land in the folder the grade reads;
#   2. the specification posted as data (POST /v1/specifications, the
#      spec.json) and approved, which the check must allow;
#   3. the PO plans the sprint from it (QA's acceptance-test tasks first),
#      the sprint starts and runs until it closes, BENCH_TIMEOUT passes or
#      the run passes BENCH_TOKEN_BUDGET, when it is stopped
#      (POST /v1/sprints/{id}/stop) before the grade (scripts/bench/budget.py);
#   4. the acceptance tests in <spec>/acceptance/, written by a person and
#      never shown to the agents, run on the produced folder; each criterion
#      passes or fails from their result (scripts/bench/scorecard.py grade);
#   5. the numbers: criteria passed / total, tasks done at the first attempt
#      (done with no rework request, no send-back on the board and at most one
#      delivery), rework requests per task, blocked share,
#      wall-clock from the sprint's start to its close, tokens from the
#      audit log's turns whose usage the provider reported, and the weight of
#      the prompts the backend sent (estimated tokens per section, from each
#      task's latest run's stored evidence).
#
# The scorecard lands in DIR (default bench/scorecards) as <stamp>.json and
# <stamp>.md; scripts/bench/compare.py diffs it with the previous one.
#
# Environment (defaults in brackets; the Postgres variables fall back to the
# e2e's E2E_ ones: the bench uses a database of its own on the same server):
#   BENCH_POSTGRES_HOST [127.0.0.1]  BENCH_POSTGRES_PORT [5432]
#   BENCH_POSTGRES_USER [factory]    BENCH_POSTGRES_PASSWORD [$POSTGRES_PASSWORD]
#   BENCH_POSTGRES_DB [factory_bench]  (dropped and recreated on every run)
#   BENCH_NATS_URL [empty]           a NATS broker of the bench's own, with
#                                    JetStream. Never a shared one: the daemon's
#                                    request subjects are not namespaced and all
#                                    daemons share one queue group, so another
#                                    stack's daemon could take the bench's work.
#                                    The default port (4222) and a broker where a
#                                    daemon already answers are refused
#                                    (scripts/bench/broker.py). Empty: the bench
#                                    starts its own nats-server
#                                    (BENCH_NATS_SERVER, or nats-server on PATH).
#   BENCH_BACKEND_PORT [18100]       BENCH_LLM_PORT [15200]
#   BENCH_PYTHON [python]            the backend's interpreter (poetry env)
#   BENCH_WORKDIR [mktemp]           logs and the produced projects
#   BENCH_TIMEOUT [600]              seconds to wait for each sprint to close;
#                                    0: no wall-clock limit, the sprint ends when
#                                    it closes or the run passes its token budget
#                                    (BENCH_TOKEN_BUDGET is then required): a slow
#                                    model is measured, never cut (issue #766)
#   BENCH_TOKEN_BUDGET [empty]       the run's budget in reported tokens, summed
#                                    over every spec (scripts/bench/budget.py).
#                                    Past it, the running sprint is stopped, the
#                                    remaining specs are skipped (listed on the
#                                    card as skipped: budget, never graded) and
#                                    the card says budget_exceeded. Turns whose
#                                    usage was not reported cannot be counted;
#                                    the card counts them apart. Empty: no budget.
#   BENCH_LLM_URL [empty]            a real LLM proxy (llm-proxy/) instead of
#                                    the fake; it spends tokens, so CI sets it
#                                    only behind a flag. Empty: the fake.
#   BENCH_RESEARCH_TOOLS [false with the fake, true otherwise]
#   BENCH_SANDBOX [host with the fake, container with BENCH_LLM_URL]  where the
#                                    agents' code and the delivery's tests run
#                                    (DELIVERY_SANDBOX): a real model's code never
#                                    runs on this machine unisolated (issue #766)
#   BENCH_SANDBOX_IMAGE [fabrique-e2e-sandbox:1]  the container sandbox's image,
#                                    built here when missing (Python and pytest,
#                                    as the e2e's)
#   AGENT_MODEL_POLICY [empty]       the routing policy the backend runs under
#                                    (services/models/model_routing.py), passed through
#   BENCH_POLICY [default, or the policy itself]  the policy's label on the
#                                    scorecard, so compare.py can tell two runs apart
#   BENCH_GRADING_IMAGE [$FABRIQUE_GRADING_IMAGE]  the image the acceptance
#                                    tests run in: the daemon's container sandbox
#                                    (scripts/bench/sandbox_grading.py), no network,
#                                    the project read-only, limits. It must be on
#                                    the machine (never pulled while grading) with
#                                    Python and pytest; docs/OPERATIONS.md. Without
#                                    a sandbox every criterion is missing
#                                    (unmeasured), never passed.
#   BENCH_SECTIONS_OFF [empty]       optional Developer prompt sections to turn
#                                    off for the whole run (memory,lessons):
#                                    passed to the backend as PROMPT_SECTIONS_OFF
#                                    and recorded on the card (sections_off). An
#                                    unknown or required name stops the run (exit
#                                    2). scripts/bench/ablate.sh sets it, one
#                                    section per run. Empty: nothing is off,
#                                    whatever PROMPT_SECTIONS_OFF says outside.
#   BENCH_EDIT_FORMAT [empty]        the edit format every model is asked for
#                                    (auto, whole or search_replace; the backend's
#                                    services/models/edit_format.py): passed to the
#                                    backend as EDIT_FORMAT_BY_MODEL {"*": format}
#                                    and recorded on the card (edit_format). An
#                                    unknown one stops the run (exit 2).
#                                    scripts/bench/edit_formats.sh sets it, one
#                                    format per run. Empty: auto, whatever
#                                    EDIT_FORMAT_BY_MODEL says outside.
#   BENCH_HUMANEVAL_CACHE [empty]    a folder that keeps the checked HumanEval
#                                    dataset between runs; checked again on use
#   BENCH_HUMANEVAL_URL [empty]      a mirror of the dataset; the sha256 pin stays
#   BENCH_SWEBENCH_INSTANCES [empty] a local JSON-lines file of SWE-bench rows,
#                                    checked against the pins like a download
#   BENCH_SWEBENCH_REPO_URL [empty]  where to clone from, {repo} for owner/name
#   BENCH_GRADE_IN_PROCESS [empty]   1: grade on BENCH_PYTHON itself, the
#                                    project's code in the runner's Python, for
#                                    local development only; the card's criteria
#                                    say isolation in_process. Never for a cycle.
#
# A grade or a collection that fails stops the run (BENCH FAILED): a spec is
# never left out of the scorecard, and nothing of an earlier run in the same
# BENCH_WORKDIR stands in for it.
#
# With the fake, every criterion must pass (the circuit check) and the exit
# status says so, unless the token budget cut the run (exit 0, not judged). With a real proxy the run exits 0 once the scorecard is
# written, whatever the models scored: compare.py judges the numbers.
set -u
REPO=$(cd "$(dirname "$0")/../.." && pwd)
OUT_DIR=""
GRADERS_REV=""
HUMANEVAL=""
HE_IDS=default
SWEBENCH=""
SWE_IDS=default
SPECS=()
while [ $# -gt 0 ]; do
  case "$1" in
    --out=*) OUT_DIR="${1#--out=}"; shift ;;
    --out) [ $# -ge 2 ] || { echo "--out requires a value" >&2; exit 2; }; OUT_DIR="$2"; shift 2 ;;
    --humaneval) HUMANEVAL=1; shift ;;
    --humaneval=*) HUMANEVAL=1; HE_IDS="${1#--humaneval=}"; shift ;;
    --swebench) SWEBENCH=1; shift ;;
    --swebench=*) SWEBENCH=1; SWE_IDS="${1#--swebench=}"; shift ;;
    --graders=*) GRADERS_REV="${1#--graders=}"; [ -n "$GRADERS_REV" ] || { echo "--graders requires a commit" >&2; exit 2; }; shift ;;
    --graders) [ $# -ge 2 ] && [ -n "$2" ] || { echo "--graders requires a commit" >&2; exit 2; }; GRADERS_REV="$2"; shift 2 ;;
    -h|--help) sed -n '2,/^set -u/p' "$0" | sed '$d'; exit 0 ;;
    *) SPECS+=("$(cd "$1" 2>/dev/null && pwd || echo "$1")"); shift ;;
  esac
done
if [ -n "$HUMANEVAL" ]; then
  # A run of its own, refused before anything starts when mixed.
  [ ${#SPECS[@]} -eq 0 ] || { echo "--humaneval runs HumanEval problems only: no SPEC_DIR" >&2; exit 2; }
  [ -z "$GRADERS_REV" ] || { echo "--humaneval is not taken with --graders: HumanEval's tests come from the pinned dataset" >&2; exit 2; }
  [[ "$HE_IDS" =~ ^(default|all|(HumanEval/)?[0-9]{1,4}(,(HumanEval/)?[0-9]{1,4})*)$ ]] \
    || { echo "--humaneval=$HE_IDS: default, all, or task ids such as 0,2,HumanEval/7" >&2; exit 2; }
  OUT_DIR=${OUT_DIR:-$REPO/bench/scorecards/humaneval}
fi
if [ -n "$SWEBENCH" ]; then
  # A run of its own, refused before anything starts when mixed.
  [ -z "$HUMANEVAL" ] || { echo "--swebench is not taken with --humaneval" >&2; exit 2; }
  [ ${#SPECS[@]} -eq 0 ] || { echo "--swebench runs SWE-bench instances only: no SPEC_DIR" >&2; exit 2; }
  [ -z "$GRADERS_REV" ] || { echo "--swebench is not taken with --graders: its tests come from the pinned instances" >&2; exit 2; }
  [[ "$SWE_IDS" =~ ^(default|[A-Za-z0-9_.-]+__[A-Za-z0-9_.-]+-[0-9]+(,[A-Za-z0-9_.-]+__[A-Za-z0-9_.-]+-[0-9]+)*)$ ]] \
    || { echo "--swebench=$SWE_IDS: default, or pinned instance ids such as pallets__flask-4992" >&2; exit 2; }
  [ "${BENCH_GRADE_IN_PROCESS:-}" = "1" ] \
    || { echo "--swebench grades in a virtual environment on this machine, the delivered code with its files and network: set BENCH_GRADE_IN_PROCESS=1 to accept that" >&2; exit 2; }
  OUT_DIR=${OUT_DIR:-$REPO/bench/scorecards/swebench}
fi
OUT_DIR=${OUT_DIR:-$REPO/bench/scorecards}
case "$OUT_DIR" in /*) ;; *) OUT_DIR="$PWD/$OUT_DIR" ;; esac

PG_HOST=${BENCH_POSTGRES_HOST:-${E2E_POSTGRES_HOST:-127.0.0.1}}
PG_PORT=${BENCH_POSTGRES_PORT:-${E2E_POSTGRES_PORT:-5432}}
PG_USER=${BENCH_POSTGRES_USER:-${E2E_POSTGRES_USER:-factory}}
PG_PASSWORD=${BENCH_POSTGRES_PASSWORD:-${E2E_POSTGRES_PASSWORD:-${POSTGRES_PASSWORD:-}}}
PG_DB=${BENCH_POSTGRES_DB:-factory_bench}
NATS_URL=${BENCH_NATS_URL:-}
NATS_SERVER=${BENCH_NATS_SERVER:-$(command -v nats-server || true)}
# Names its JetStream streams apart, on top of the broker of its own.
NATS_NAMESPACE=${BENCH_NATS_NAMESPACE:-bench}
BACKEND_PORT=${BENCH_BACKEND_PORT:-18100}
LLM_PORT=${BENCH_LLM_PORT:-15200}
PY=${BENCH_PYTHON:-${E2E_PYTHON:-python}}
TIMEOUT=${BENCH_TIMEOUT:-600}
POLICY_LABEL=${BENCH_POLICY:-${AGENT_MODEL_POLICY:-default}}
LLM_URL=${BENCH_LLM_URL:-}
SECTIONS_OFF=${BENCH_SECTIONS_OFF:-}
if [ -n "$SECTIONS_OFF" ]; then
  SECTIONS_OFF=$("$PY" "$REPO/scripts/bench/ablation.py" parse --names "$SECTIONS_OFF") \
    || { echo "BENCH_SECTIONS_OFF names a section the backend would not turn off"; exit 2; }
  echo "prompt sections off: $SECTIONS_OFF"
fi
EDIT_FORMAT=${BENCH_EDIT_FORMAT:-}
EDIT_FORMAT_MAP=""
if [ -n "$EDIT_FORMAT" ]; then
  EDIT_FORMAT=$("$PY" "$REPO/scripts/bench/edit_format_card.py" parse --formats "$EDIT_FORMAT") \
    && [ "$(echo "$EDIT_FORMAT" | wc -l)" -eq 1 ] && [ "$EDIT_FORMAT" != each ] \
    || { echo "BENCH_EDIT_FORMAT names no single edit format the backend knows"; exit 2; }
  EDIT_FORMAT_MAP="{\"*\": \"$EDIT_FORMAT\"}"
  echo "edit format: $EDIT_FORMAT"
fi
BUDGET=${BENCH_TOKEN_BUDGET:-}
case "$BUDGET" in ''|*[!0-9]*|0) [ -z "$BUDGET" ] || { echo "BENCH_TOKEN_BUDGET must be a positive integer of tokens, or empty"; exit 2; } ;; esac
case "$TIMEOUT" in ''|*[!0-9]*) echo "BENCH_TIMEOUT must be a whole number of seconds (0: no wall-clock limit)"; exit 2 ;; esac
# Without a wall-clock limit the token budget is the only stop.
[ "$TIMEOUT" != 0 ] || [ -n "$BUDGET" ] || { echo "BENCH_TIMEOUT=0 needs BENCH_TOKEN_BUDGET: the run must have a stop"; exit 2; }
WORK=${BENCH_WORKDIR:-$(mktemp -d -t fabrique-bench-XXXXXX)}
mkdir -p "$WORK/projects" "$WORK/audit" "$WORK/entries"
rm -f "$WORK"/entries/*.json

# The graders: this checkout's own, or those of --graders' commit, taken from
# its objects (never this tree) with their record for the card.
SPECS_ROOT="$REPO/bench/specs"
GRADER="$REPO/scripts/bench/scorecard.py"
GRADERS_FLAG=()
# The daemon's sandbox the grade loads on this machine: this checkout's, or,
# with --graders, the copy taken from that commit's objects and hash-checked
# by the grader before it imports it (never the checkout's, which may be the
# candidate's; review of PR #634).
DAEMON_DIR="$REPO/vscode-daemon"
DAEMON_SHA256=""
if [ -n "$GRADERS_REV" ]; then
  rm -f "$WORK/graders.json"
  "$PY" "$REPO/scripts/bench/graders.py" checkout --repo "$REPO" --rev "$GRADERS_REV" \
    --dest "$WORK/graders" --out "$WORK/graders.json" > /dev/null || { echo "--graders $GRADERS_REV: the graders could not be taken"; exit 2; }
  SPECS_ROOT="$WORK/graders/bench/specs"
  GRADER="$WORK/graders/scripts/bench/scorecard.py"
  GRADERS_FLAG=(--graders "$WORK/graders.json")
  "$PY" "$REPO/scripts/bench/graders.py" daemon --repo "$REPO" --rev "$GRADERS_REV" \
    --dest "$WORK/graders-daemon" --out "$WORK/graders-daemon.json" > /dev/null \
    || { echo "--graders $GRADERS_REV: the daemon's sandbox could not be taken"; exit 2; }
  DAEMON_DIR="$WORK/graders-daemon"
  DAEMON_SHA256=$("$PY" -c 'import json, sys; print(json.load(open(sys.argv[1]))["sha256"])' "$WORK/graders-daemon.json")
  echo "graders: $("$PY" -c 'import json, sys; d = json.load(open(sys.argv[1])); print(d["sha"], "sha256", d["sha256"])' "$WORK/graders.json")"
  for i in "${!SPECS[@]}"; do SPECS[i]="$SPECS_ROOT/$(basename "${SPECS[i]}")"; done
fi
HE_CARD=()
if [ -n "$HUMANEVAL" ]; then
  # The dataset fetched and checked against its pin, each problem written as
  # a specification folder (spec.json, seed/, acceptance/, fake/ when the
  # fake has an answer) under BENCH_WORKDIR, never in the repository.
  HE_FLAGS=(--dest "$WORK/humaneval" --ids "$HE_IDS")
  [ -n "$LLM_URL" ] || HE_FLAGS+=(--fake)
  [ -z "${BENCH_HUMANEVAL_CACHE:-}" ] || HE_FLAGS+=(--cache "$BENCH_HUMANEVAL_CACHE")
  [ -z "${BENCH_HUMANEVAL_URL:-}" ] || HE_FLAGS+=(--url "$BENCH_HUMANEVAL_URL")
  rm -f "$WORK/humaneval/problems.json"
  HE_SPECS=$("$PY" "$REPO/scripts/bench/humaneval.py" prepare "${HE_FLAGS[@]}") \
    || { echo "HumanEval: the dataset could not be prepared"; exit 2; }
  mapfile -t SPECS <<< "$HE_SPECS"
  SPECS_ROOT="$WORK/humaneval/specs"
  HE_CARD=(--humaneval "$WORK/humaneval/problems.json")
  echo "HumanEval: ${#SPECS[@]} problem(s), dataset sha256 checked against its pin"
fi
SWE_CARD=()
if [ -n "$SWEBENCH" ]; then
  # Each pinned instance fetched and checked, its repository written as the
  # seed of a specification folder under BENCH_WORKDIR, never the repository's.
  SWE_FLAGS=(--dest "$WORK/swebench" --ids "$SWE_IDS")
  [ -n "$LLM_URL" ] || SWE_FLAGS+=(--fake)
  [ -z "${BENCH_SWEBENCH_INSTANCES:-}" ] || SWE_FLAGS+=(--instances "$BENCH_SWEBENCH_INSTANCES")
  [ -z "${BENCH_SWEBENCH_REPO_URL:-}" ] || SWE_FLAGS+=(--repo-url "$BENCH_SWEBENCH_REPO_URL")
  rm -f "$WORK/swebench/instances.json"
  SWE_SPECS=$("$PY" "$REPO/scripts/bench/swebench.py" prepare "${SWE_FLAGS[@]}") \
    || { echo "SWE-bench: the instances could not be prepared"; exit 2; }
  mapfile -t SPECS <<< "$SWE_SPECS"
  SPECS_ROOT="$WORK/swebench/specs"
  SWE_CARD=(--swebench "$WORK/swebench/instances.json")
  echo "SWE-bench Lite: ${#SPECS[@]} instance(s), each checked against its pinned sha256"
fi
if [ ${#SPECS[@]} -eq 0 ]; then
  for d in "$SPECS_ROOT"/*/; do [ -f "$d/spec.json" ] && SPECS+=("${d%/}"); done
fi
[ ${#SPECS[@]} -gt 0 ] || { echo "no reference specification found" >&2; exit 2; }
for d in "${SPECS[@]}"; do
  [ -f "$d/spec.json" ] && [ -d "$d/acceptance" ] || { echo "$d needs a spec.json and an acceptance/ folder" >&2; exit 2; }
done
# The grade: in the daemon's sandbox, unless BENCH_GRADE_IN_PROCESS=1.
GRADING_IMAGE=${BENCH_GRADING_IMAGE:-${FABRIQUE_GRADING_IMAGE:-}}
if [ "${BENCH_GRADE_IN_PROCESS:-}" = "1" ]; then
  GRADE_FLAGS=(--in-process)
  echo "grading in-process (BENCH_GRADE_IN_PROCESS=1): the projects' code runs in $PY; local development only, never a cycle's measurement"
else
  GRADE_FLAGS=(--image "$GRADING_IMAGE")
  echo "grading in the sandbox: image ${GRADING_IMAGE:-none named (every grade will be unmeasured)}"
fi
API=http://127.0.0.1:$BACKEND_PORT
# Where the agents' code runs: the fake's answers are the bench's own files, a
# real model's are not (issue #766), so a real proxy defaults to the container.
SANDBOX=${BENCH_SANDBOX:-$([ -n "${BENCH_LLM_URL:-}" ] && echo container || echo host)}
SANDBOX_IMAGE=${BENCH_SANDBOX_IMAGE:-fabrique-e2e-sandbox:1}
case "$SANDBOX" in
  host) TEST_COMMAND="$PY -m pytest -q -p no:cacheprovider" ;;
  container)
    docker image inspect "$SANDBOX_IMAGE" >/dev/null 2>&1 \
      || printf 'FROM python:3.12-slim\nRUN pip install --no-cache-dir pytest\n' \
        | docker build -q -t "$SANDBOX_IMAGE" - >/dev/null \
      || { echo "could not build the sandbox image $SANDBOX_IMAGE"; exit 2; }
    TEST_COMMAND="python -m pytest -q -p no:cacheprovider" ;;
  *) echo "BENCH_SANDBOX is host or container, not $SANDBOX"; exit 2 ;;
esac
echo "the agents' code runs in the $SANDBOX sandbox"
cleanup() { kill ${BPID:-} ${DPID:-} ${LPID:-} ${NPID:-} 2>/dev/null; wait 2>/dev/null; }
trap cleanup EXIT
fail() { echo "BENCH FAILED: $*"; exit 1; }
free_port() { "$PY" -c 'import socket; s = socket.socket(); s.bind(("127.0.0.1", 0)); print(s.getsockname()[1]); s.close()'; }

port_busy() { (echo > "/dev/tcp/127.0.0.1/$1") 2>/dev/null; }
# Healthy, or degraded only because the fake's run chose the host sandbox
# (``/health`` says so, issue #766); anything else degraded stops the bench.
healthy() { curl -sf -m 2 "$API/health" | SANDBOX="$SANDBOX" "$PY" -c '
import json, os, sys
h = json.load(sys.stdin)
v = h.get("verification") or {}
others = [k for k, s in h.items() if isinstance(s, dict) and s.get("status") == "degraded" and k != "verification"]
host_only = os.environ["SANDBOX"] == "host" and v.get("sandbox_default") == "host" and len(v.get("reasons") or []) == 1
sys.exit(0 if h.get("status") == "healthy" or (h.get("status") == "degraded" and host_only and not others) else 1)' 2>/dev/null; }
if port_busy "$BACKEND_PORT"; then
  echo "port $BACKEND_PORT is already in use: set BENCH_BACKEND_PORT to a free port."; exit 2
fi

# A fresh database, as in the e2e: the migrations create its schema.
"$PY" - "$PG_HOST" "$PG_PORT" "$PG_USER" "$PG_PASSWORD" "$PG_DB" <<'EOF' || { echo "could not recreate the database"; exit 2; }
import sys
import psycopg2
host, port, user, password, db = sys.argv[1:]
conn = psycopg2.connect(host=host, port=port, user=user, password=password, dbname="postgres")
conn.autocommit = True
with conn.cursor() as cur:
    cur.execute(f'DROP DATABASE IF EXISTS "{db}"')
    cur.execute(f"CREATE DATABASE \"{db}\" ENCODING 'UTF8' TEMPLATE template0 LC_COLLATE 'C' LC_CTYPE 'C'")
conn.close()
EOF

if [ -z "$LLM_URL" ]; then
  if port_busy "$LLM_PORT"; then echo "port $LLM_PORT is already in use: set BENCH_LLM_PORT."; exit 2; fi
  FAKE_LLM_PORT=$LLM_PORT FAKE_LLM_PROMPTS="$WORK/fake_llm.prompts.log" BENCH_SPECS="$SPECS_ROOT" \
    "$PY" "$REPO/scripts/bench/fake_llm.py" > "$WORK/llm.log" 2>&1 & LPID=$!
  LLM_URL=http://127.0.0.1:$LLM_PORT
  LLM_LABEL=fake
  RESEARCH=${BENCH_RESEARCH_TOOLS:-false}
else
  LLM_LABEL=$LLM_URL
  RESEARCH=${BENCH_RESEARCH_TOOLS:-true}
fi
if [ -z "$NATS_URL" ]; then
  [ -n "$NATS_SERVER" ] || { echo "no broker for the bench: set BENCH_NATS_URL to a NATS of its own (JetStream on, not port 4222), or install nats-server"; exit 2; }
  NATS_PORT=$(free_port)
  "$NATS_SERVER" -js -a 127.0.0.1 -p "$NATS_PORT" -sd "$WORK/nats" > "$WORK/nats.log" 2>&1 & NPID=$!
  NATS_URL=nats://127.0.0.1:$NATS_PORT
  for _ in $(seq 1 30); do port_busy "$NATS_PORT" && break; sleep 1; done
  port_busy "$NATS_PORT" || { echo "nats-server did not come up; see $WORK/nats.log"; exit 2; }
fi
# Before the bench's own daemon joins: nobody else may answer on this broker.
"$PY" "$REPO/scripts/bench/broker.py" check "$NATS_URL" || exit 2
NATS_URL=$NATS_URL NATS_NAMESPACE=$NATS_NAMESPACE WORKSPACE_ROOT="$WORK/projects" FABRIQUE_SNAPSHOT_STATE="$WORK/snapshots" \
  "$PY" "$REPO/vscode-daemon/daemon.py" > "$WORK/daemon.log" 2>&1 & DPID=$!
for _ in $(seq 1 30); do grep -q "Daemon ready" "$WORK/daemon.log" 2>/dev/null && break; sleep 1; done
grep -q "Daemon ready" "$WORK/daemon.log" || { echo "daemon did not come up; see $WORK/daemon.log"; exit 2; }
PGENV=(POSTGRES_HOST="$PG_HOST" POSTGRES_PORT="$PG_PORT" POSTGRES_USER="$PG_USER" POSTGRES_PASSWORD="$PG_PASSWORD" POSTGRES_DB="$PG_DB")
(cd "$REPO/backend" && env "${PGENV[@]}" ALEMBIC_QUIET=1 "$PY" -m alembic upgrade head) > "$WORK/alembic.log" 2>&1 \
  || { echo "the migrations did not apply; see $WORK/alembic.log"; cat "$WORK/alembic.log"; exit 2; }
(
  cd "$REPO/backend" && exec env "${PGENV[@]}" \
    NATS_URL="$NATS_URL" NATS_NAMESPACE="$NATS_NAMESPACE" LLM_PROXY_URL="$LLM_URL" NEO4J_URI=bolt://127.0.0.1:1 \
    AUTH_REQUIRED=false JWT_SECRET_KEY=bench-secret-bench-secret-bench-secret-bench \
    SUPPORT_AGENT_ENABLED=false REVIEWER_AGENT_ENABLED=false \
    DELIVERY_TESTS_ENABLED=true DELIVERY_TEST_COMMAND="$TEST_COMMAND" DELIVERY_SANDBOX="$SANDBOX" DELIVERY_SANDBOX_IMAGE="$SANDBOX_IMAGE" \
    RESEARCH_TOOLS_ENABLED="$RESEARCH" AUTO_SELF_IMPROVEMENT_ENABLED=false AGENT_GRAPH_ENABLED=false \
    AGENT_MODEL_POLICY="${AGENT_MODEL_POLICY:-}" PROMPT_SECTIONS_OFF="$SECTIONS_OFF" EDIT_FORMAT_BY_MODEL="$EDIT_FORMAT_MAP" AUDIT_LOG_PATH="$WORK/audit" PYTHONPATH=.:app "$PY" -m uvicorn app.main:app --host 127.0.0.1 --port "$BACKEND_PORT"
) > "$WORK/backend.log" 2>&1 & BPID=$!
for _ in $(seq 1 60); do healthy && break; sleep 1; done
healthy || { echo "backend did not come up; see $WORK/backend.log"; exit 2; }
echo "stack up: $API, models from $LLM_LABEL (logs in $WORK)"

api() { curl -s -m 120 "$@"; }
post() { api -X POST "$API$1" -H 'Content-Type: application/json' -d "$2"; }
json() { "$PY" -c "import sys, json
try:
    d = json.load(sys.stdin)
except ValueError:
    d = {}
print($1)"; }

run_spec() {  # run_spec <spec dir>: writes $WORK/entries/<name>.json
  local dir=$1 name status=error error="" cid=0 spec_id=0 sid=0 wall started
  name=$(basename "$dir")
  local project="$WORK/projects/$name" company body spec approved plan="" new seeded git_flag=""
  # Nothing of an earlier run in the same BENCH_WORKDIR can stand in for this one.
  rm -f "$WORK/$name.grade.json" "$WORK/entries/$name.json"
  echo "== $name"
  # An empty folder holding a README, or, with <spec>/seed/, the seed committed
  # as the base of a repository (scripts/bench/seed.py).
  seeded=$("$PY" "$REPO/scripts/bench/seed.py" --spec "$dir" --project "$project") \
    || fail "the project of $name could not be prepared"
  if [ "$(echo "$seeded" | json "d.get('seeded') is True")" = True ]; then
    echo "   seed: $(echo "$seeded" | json "d.get('files'), 'files, base', d.get('base')")"
    # Deliveries land in the folder the grade reads, not on task branches.
    git_flag='"git_workflow": false,'
  fi
  company=$(post /v1/init_company "{\"company_name\": \"Bench $name\", \"project_path\": \"$project\", $git_flag
    \"autonomy_level\": 2, \"start_now\": false, \"default_scope_size\": \"M\",
    \"agents\": [{\"role\": \"PO\", \"name\": \"Pat\"}, {\"role\": \"Developer\", \"name\": \"Dev\"},
                 {\"role\": \"QA\", \"name\": \"Quinn\"}, {\"role\": \"ScrumMaster\", \"name\": \"Sam\"}]}")
  cid=$(echo "$company" | json "d.get('company_id') or 0")
  [ "$cid" != 0 ] || error="init_company failed: ${company:0:300}"
  if [ -z "$error" ] && [ "$SANDBOX" = host ]; then
    # The fake's host run, said in the company's own record (issue #766).
    ack=$(post "/v1/companies/$cid/acknowledgements" '{"kind": "host_sandbox", "days": 1,
      "reason": "The bench with the fake LLM: its answers are the bench'"'"'s own files, run on the host."}')
    [ "$(echo "$ack" | json "'host_sandbox' not in (d.get('missing') or ['?'])")" = True ] || error="the host_sandbox acknowledgement was refused: ${ack:0:300}"
  fi
  if [ -z "$error" ]; then
    body=$("$PY" -c 'import json, sys; d = json.load(open(sys.argv[1])); d["company_id"] = int(sys.argv[2]); print(json.dumps(d))' "$dir/spec.json" "$cid")
    spec=$(post /v1/specifications "$body")
    spec_id=$(echo "$spec" | json "d.get('id') or 0")
    [ "$spec_id" != 0 ] || error="the specification was refused: ${spec:0:300}"
  fi
  if [ -z "$error" ]; then
    approved=$(api -X POST "$API/v1/specifications/$spec_id/approve")
    [ "$(echo "$approved" | json "d.get('status', '')")" = approved ] || error="approval refused: ${approved:0:300}"
  fi
  if [ -z "$error" ]; then
    plan=$(post /v1/plan_sprint "{\"company_id\": $cid, \"scope_size\": \"M\", \"specification_id\": $spec_id}")
    echo "   plan: $(echo "$plan" | json "len(d.get('tasks') or []), 'tasks; uncovered:', d.get('uncovered')")"
    [ "$(echo "$plan" | json "len(d.get('tasks') or [])")" -gt 0 ] || error="the PO planned no task: ${plan:0:300}"
  fi
  started=$SECONDS
  if [ -z "$error" ]; then
    new=$(post /v1/start_sprint "{\"company_id\": $cid, \"sprint_name\": \"Bench $name\", \"start_date\": \"$(date -u +%F)\",
      \"scope_size\": \"M\", \"specification_id\": $spec_id, \"backlog_items\": $(echo "$plan" | json "json.dumps(d['tasks'])")}")
    sid=$(echo "$new" | json "d.get('sprint_id') or 0")
    [ "$sid" != 0 ] || error="start_sprint failed: ${new:0:300}"
  fi
  if [ -z "$error" ]; then
    # Until it closes, BENCH_TIMEOUT passes or the run passes its token budget;
    # in the last two it is stopped, so nothing the agents still do lands
    # between the grade and the numbers.
    local outcome
    outcome=$("$PY" "$REPO/scripts/bench/budget.py" wait --api "$API" --company "$cid" --sprint "$sid" \
      --timeout "$([ "$TIMEOUT" = 0 ] && echo inf || echo $(( TIMEOUT - (SECONDS - started) )))" --budget "$BUDGET" \
      --pg "$PG_HOST" "$PG_PORT" "$PG_USER" "$PG_PASSWORD" "$PG_DB" | tail -1)
    status=$(echo "$outcome" | json "d.get('status') or 'error'")
    error=$(echo "$outcome" | json "d.get('error') or ('the wait for the sprint failed' if not d else '')")
  fi
  SPEC_STATUS=$status
  wall=$((SECONDS - started))
  echo "   sprint: $status in ${wall}s${error:+ ($error)}"
  if [ -f "$dir/acceptance/instance.json" ]; then
    # A SWE-bench instance: its own tests, run by the scorer after the test patch.
    "$PY" "$REPO/scripts/bench/swebench_grade.py" grade --spec "$dir" --project "$project" --work "$WORK" \
      --out "$WORK/$name.grade.json" || fail "the grade of $name did not run"
  else
    FABRIQUE_DAEMON_DIR="$DAEMON_DIR" FABRIQUE_DAEMON_SHA256="$DAEMON_SHA256" "$PY" "$GRADER" grade --spec "$dir" --project "$project" --python "$PY" \
      "${GRADE_FLAGS[@]}" --out "$WORK/$name.grade.json" || fail "the grade of $name did not run"
  fi
  unmeasured=$(json "d.get('unmeasured') or ''" < "$WORK/$name.grade.json")
  [ -z "$unmeasured" ] || echo "   grade unmeasured: $unmeasured"
  echo "   criteria passed: $(json "d.get('passed'), 'of', d.get('total')" < "$WORK/$name.grade.json")"
  "$PY" "$REPO/scripts/bench/scorecard.py" collect --api "$API" --name "$name" --status "$status" \
    --company "$cid" --sprint "$sid" --spec-id "$spec_id" --grade "$WORK/$name.grade.json" --wall "$wall" \
    --pg "$PG_HOST" "$PG_PORT" "$PG_USER" "$PG_PASSWORD" "$PG_DB" --error "$error" --out "$WORK/entries/$name.json" \
    || fail "the numbers of $name could not be collected"
}

CUT=""
SKIPPED=()
for dir in "${SPECS[@]}"; do
  if [ -z "$CUT" ] && [ -n "$BUDGET" ]; then
    # A budget passed while the previous sprint closed cuts the run here.
    spent=$("$PY" "$REPO/scripts/bench/budget.py" check --budget "$BUDGET" \
      --pg "$PG_HOST" "$PG_PORT" "$PG_USER" "$PG_PASSWORD" "$PG_DB")
    case $? in
      0) ;;
      3) CUT=1; echo "token budget $BUDGET exceeded ($spent): the remaining specifications are skipped" ;;
      *) fail "the token budget could not be read" ;;
    esac
  fi
  if [ -n "$CUT" ]; then
    SKIPPED+=("$(basename "$dir")"); echo "== $(basename "$dir"): skipped (budget)"; continue
  fi
  run_spec "$dir"
  if [ "$SPEC_STATUS" = budget_exceeded ]; then
    CUT=1; echo "token budget $BUDGET exceeded: the remaining specifications are skipped"
  fi
done
RAN=$(( ${#SPECS[@]} - ${#SKIPPED[@]} ))
SKIP_FLAGS=()
for name in ${SKIPPED[@]+"${SKIPPED[@]}"}; do SKIP_FLAGS+=(--skipped "$name"); done
ENTRIES=("$WORK"/entries/*.json)
[ -f "${ENTRIES[0]}" ] && [ ${#ENTRIES[@]} -eq "$RAN" ] \
  || fail "$RAN specification(s) run, but $( [ -f "${ENTRIES[0]}" ] && echo ${#ENTRIES[@]} || echo 0) entr(y/ies) written"

CARD=$(cd "$REPO" && "$PY" scripts/bench/scorecard.py write --out-dir "$OUT_DIR" --llm "$LLM_LABEL" --policy "$POLICY_LABEL" \
  --budget "$BUDGET" --sections-off "$SECTIONS_OFF" --edit-format "$EDIT_FORMAT" ${CUT:+--cut} ${SKIP_FLAGS[@]+"${SKIP_FLAGS[@]}"} ${GRADERS_FLAG[@]+"${GRADERS_FLAG[@]}"} ${HE_CARD[@]+"${HE_CARD[@]}"} ${SWE_CARD[@]+"${SWE_CARD[@]}"} "${ENTRIES[@]}") \
  || fail "the scorecard could not be written"
echo "scorecard: $CARD"
cat "${CARD%.json}.md"
echo "backend errors (Neo4j aside): $(grep '"level": "ERROR"' "$WORK/backend.log" | grep -vc Neo4j)"
if [ -n "$CUT" ]; then
  echo "BENCH CUT (token budget $BUDGET exceeded; ${#SKIPPED[@]} specification(s) skipped, the run not judged)"
elif [ "$LLM_LABEL" = fake ] && [ -n "$HUMANEVAL" ]; then
  # The fake answers every problem it was given: anything less is a broken circuit.
  if "$PY" -c 'import json, sys; h = json.load(open(sys.argv[1]))["humaneval"]; sys.exit(not (h["passed"] == h["graded"] == len(h["subset"]) > 0))' "$CARD"; then
    echo "BENCH PASSED (fake LLM: every HumanEval problem passed its own tests)"
  else
    echo "BENCH FAILED (fake LLM: the HumanEval circuit did not run green; logs in $WORK)"; exit 1
  fi
elif [ "$LLM_LABEL" = fake ] && [ -n "$SWEBENCH" ]; then
  # The fake delivers each reference patch: every instance measured must resolve.
  if "$PY" -c 'import json, sys; s = json.load(open(sys.argv[1]))["swebench"]; print(s["unmeasured"], "unmeasured"); sys.exit(not (s["resolved"] == s["measured"] > 0))' "$CARD"; then
    echo "BENCH PASSED (fake LLM: every SWE-bench instance measured was resolved by its own tests)"
  else
    echo "BENCH FAILED (fake LLM: the SWE-bench circuit did not run green; logs in $WORK)"; exit 1
  fi
elif [ "$LLM_LABEL" = fake ]; then
  # The fake gets every spec right: anything less is a broken circuit.
  if "$PY" -c 'import json, sys; t = json.load(open(sys.argv[1]))["totals"]; sys.exit(not (t["specs_completed"] == t["specs"] and t["criteria_passed"] == t["criteria_total"] > 0))' "$CARD"; then
    echo "BENCH PASSED (fake LLM: every spec completed, every criterion passed)"
  else
    echo "BENCH FAILED (fake LLM: the circuit did not run green; logs in $WORK)"; exit 1
  fi
fi
exit 0
