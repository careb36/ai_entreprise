#!/usr/bin/env bash
# The bench: drive each reference specification through the factory against
# a real stack and record a scorecard graded by tests a person wrote.
#
#   scripts/bench/run.sh [--out DIR] [SPEC_DIR...]
#
# Without SPEC_DIR, every bench/specs/*/ holding a spec.json. The stack is the
# e2e's (scripts/e2e/run.sh): a Postgres and a NATS you provide, the backend,
# the workspace daemon and, by default, a fake LLM proxy
# (scripts/bench/fake_llm.py) that answers each spec from its fake/ folder.
# Per specification:
#
#   1. a company (autonomy 2, Sprint 0 left planned so it costs nothing) with
#      a PO, a Developer, QA and a ScrumMaster, writing to its own folder;
#   2. the specification posted as data (POST /v1/specifications, the
#      spec.json) and approved, which the check must allow;
#   3. the PO plans the sprint from it (QA's acceptance-test tasks first),
#      the sprint starts and runs until it closes or BENCH_TIMEOUT passes;
#   4. the acceptance tests in <spec>/acceptance/, written by a person and
#      never shown to the agents, run on the produced folder; each criterion
#      passes or fails from their result (scripts/bench/scorecard.py grade);
#   5. the numbers: criteria passed / total, tasks done at the first attempt
#      (done with one delivery), rework requests per task, blocked share,
#      wall-clock from the sprint's start to its close, and tokens from the
#      audit log's turns whose usage the provider reported.
#
# The scorecard lands in DIR (default bench/scorecards) as <stamp>.json and
# <stamp>.md; scripts/bench/compare.py diffs it with the previous one.
#
# Environment (defaults in brackets; the BENCH_ variables fall back to the
# e2e's E2E_ ones, so one Postgres and NATS serve both):
#   BENCH_POSTGRES_HOST [127.0.0.1]  BENCH_POSTGRES_PORT [5432]
#   BENCH_POSTGRES_USER [factory]    BENCH_POSTGRES_PASSWORD [$POSTGRES_PASSWORD]
#   BENCH_POSTGRES_DB [factory_bench]  (dropped and recreated on every run)
#   BENCH_NATS_URL [nats://127.0.0.1:4222]
#   BENCH_BACKEND_PORT [18100]       BENCH_LLM_PORT [15200]
#   BENCH_PYTHON [python]            the backend's interpreter (poetry env)
#   BENCH_WORKDIR [mktemp]           logs and the produced projects
#   BENCH_TIMEOUT [600]              seconds to wait for each sprint to close
#   BENCH_LLM_URL [empty]            a real LLM proxy (llm-proxy/) instead of
#                                    the fake; it spends tokens, so CI sets it
#                                    only behind a flag. Empty: the fake.
#   BENCH_RESEARCH_TOOLS [false with the fake, true otherwise]
#
# With the fake, every criterion must pass (the circuit check) and the exit
# status says so. With a real proxy the run exits 0 once the scorecard is
# written, whatever the models scored: compare.py judges the numbers.
set -u
REPO=$(cd "$(dirname "$0")/../.." && pwd)
OUT_DIR="$REPO/bench/scorecards"
SPECS=()
while [ $# -gt 0 ]; do
  case "$1" in
    --out=*) OUT_DIR="${1#--out=}"; shift ;;
    --out) [ $# -ge 2 ] || { echo "--out requires a value" >&2; exit 2; }; OUT_DIR="$2"; shift 2 ;;
    -h|--help) sed -n '2,47p' "$0"; exit 0 ;;
    *) SPECS+=("$(cd "$1" 2>/dev/null && pwd || echo "$1")"); shift ;;
  esac
done
case "$OUT_DIR" in /*) ;; *) OUT_DIR="$PWD/$OUT_DIR" ;; esac
if [ ${#SPECS[@]} -eq 0 ]; then
  for d in "$REPO"/bench/specs/*/; do [ -f "$d/spec.json" ] && SPECS+=("${d%/}"); done
fi
[ ${#SPECS[@]} -gt 0 ] || { echo "no reference specification found" >&2; exit 2; }
for d in "${SPECS[@]}"; do
  [ -f "$d/spec.json" ] && [ -d "$d/acceptance" ] || { echo "$d needs a spec.json and an acceptance/ folder" >&2; exit 2; }
done

PG_HOST=${BENCH_POSTGRES_HOST:-${E2E_POSTGRES_HOST:-127.0.0.1}}
PG_PORT=${BENCH_POSTGRES_PORT:-${E2E_POSTGRES_PORT:-5432}}
PG_USER=${BENCH_POSTGRES_USER:-${E2E_POSTGRES_USER:-factory}}
PG_PASSWORD=${BENCH_POSTGRES_PASSWORD:-${E2E_POSTGRES_PASSWORD:-${POSTGRES_PASSWORD:-}}}
PG_DB=${BENCH_POSTGRES_DB:-factory_bench}
NATS_URL=${BENCH_NATS_URL:-${E2E_NATS_URL:-nats://127.0.0.1:4222}}
# Its own JetStream namespace, apart from an e2e or a stack on the same broker.
NATS_NAMESPACE=${BENCH_NATS_NAMESPACE:-bench}
BACKEND_PORT=${BENCH_BACKEND_PORT:-18100}
LLM_PORT=${BENCH_LLM_PORT:-15200}
PY=${BENCH_PYTHON:-${E2E_PYTHON:-python}}
TIMEOUT=${BENCH_TIMEOUT:-600}
LLM_URL=${BENCH_LLM_URL:-}
WORK=${BENCH_WORKDIR:-$(mktemp -d -t fabrique-bench-XXXXXX)}
mkdir -p "$WORK/projects" "$WORK/audit" "$WORK/entries"
rm -f "$WORK"/entries/*.json
API=http://127.0.0.1:$BACKEND_PORT
TEST_COMMAND="$PY -m pytest -q -p no:cacheprovider"

port_busy() { (echo > "/dev/tcp/127.0.0.1/$1") 2>/dev/null; }
healthy() { curl -sf -m 2 "$API/health" | "$PY" -c 'import sys, json; sys.exit(json.load(sys.stdin).get("status") != "healthy")' 2>/dev/null; }
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
  FAKE_LLM_PORT=$LLM_PORT FAKE_LLM_PROMPTS="$WORK/fake_llm.prompts.log" \
    "$PY" "$REPO/scripts/bench/fake_llm.py" > "$WORK/llm.log" 2>&1 & LPID=$!
  LLM_URL=http://127.0.0.1:$LLM_PORT
  LLM_LABEL=fake
  RESEARCH=${BENCH_RESEARCH_TOOLS:-false}
else
  LLM_LABEL=$LLM_URL
  RESEARCH=${BENCH_RESEARCH_TOOLS:-true}
fi
NATS_URL=$NATS_URL NATS_NAMESPACE=$NATS_NAMESPACE WORKSPACE_ROOT="$WORK/projects" \
  "$PY" "$REPO/vscode-daemon/daemon.py" > "$WORK/daemon.log" 2>&1 & DPID=$!
cleanup() { kill ${BPID:-} $DPID ${LPID:-} 2>/dev/null; wait 2>/dev/null; }
trap cleanup EXIT
for _ in $(seq 1 30); do grep -q "Daemon ready" "$WORK/daemon.log" 2>/dev/null && break; sleep 1; done
grep -q "Daemon ready" "$WORK/daemon.log" || { echo "daemon did not come up; see $WORK/daemon.log"; exit 2; }
PGENV=(POSTGRES_HOST="$PG_HOST" POSTGRES_PORT="$PG_PORT" POSTGRES_USER="$PG_USER" POSTGRES_PASSWORD="$PG_PASSWORD" POSTGRES_DB="$PG_DB")
(cd "$REPO/backend" && env "${PGENV[@]}" ALEMBIC_QUIET=1 "$PY" -m alembic upgrade head) > "$WORK/alembic.log" 2>&1 \
  || { echo "the migrations did not apply; see $WORK/alembic.log"; cat "$WORK/alembic.log"; exit 2; }
(
  cd "$REPO/backend" && exec env "${PGENV[@]}" \
    NATS_URL="$NATS_URL" NATS_NAMESPACE="$NATS_NAMESPACE" LLM_PROXY_URL="$LLM_URL" NEO4J_URI=bolt://127.0.0.1:1 \
    AUTH_REQUIRED=false JWT_SECRET_KEY=bench-secret-bench-secret-bench-secret-bench \
    CONSOLE_ASSISTANTS_ENABLED=false SUPPORT_AGENT_ENABLED=false REVIEWER_AGENT_ENABLED=false \
    DELIVERY_TESTS_ENABLED=true DELIVERY_TEST_COMMAND="$TEST_COMMAND" DELIVERY_SANDBOX=host \
    RESEARCH_TOOLS_ENABLED="$RESEARCH" AUTO_SELF_IMPROVEMENT_ENABLED=false AGENT_GRAPH_ENABLED=false \
    AUDIT_LOG_PATH="$WORK/audit" PYTHONPATH=.:app "$PY" -m uvicorn app.main:app --host 127.0.0.1 --port "$BACKEND_PORT"
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
sprint_status() { api "$API/v1/sprints?company_id=$1" | json "next((s['status'] for s in d if s['id'] == $2), '') if isinstance(d, list) else ''"; }

run_spec() {  # run_spec <spec dir>: writes $WORK/entries/<name>.json
  local dir=$1 name status=error error="" cid=0 spec_id=0 sid=0 wall started
  name=$(basename "$dir")
  local project="$WORK/projects/$name" company body spec approved plan="" new
  rm -rf "$project"; mkdir -p "$project"; echo "# $name" > "$project/README.md"
  echo "== $name"
  company=$(post /v1/init_company "{\"company_name\": \"Bench $name\", \"project_path\": \"$project\",
    \"autonomy_level\": 2, \"start_now\": false, \"default_scope_size\": \"M\",
    \"agents\": [{\"role\": \"PO\", \"name\": \"Pat\"}, {\"role\": \"Developer\", \"name\": \"Dev\"},
                 {\"role\": \"QA\", \"name\": \"Quinn\"}, {\"role\": \"ScrumMaster\", \"name\": \"Sam\"}]}")
  cid=$(echo "$company" | json "d.get('company_id') or 0")
  [ "$cid" != 0 ] || error="init_company failed: ${company:0:300}"
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
    status=timeout
    while (( SECONDS - started < TIMEOUT )); do
      if [ "$(sprint_status "$cid" "$sid")" = completed ]; then status=completed; break; fi
      sleep 3
    done
    [ "$status" = completed ] || error="the sprint did not close within ${TIMEOUT}s"
  fi
  wall=$((SECONDS - started))
  echo "   sprint: $status in ${wall}s${error:+ ($error)}"
  "$PY" "$REPO/scripts/bench/scorecard.py" grade --spec "$dir" --project "$project" --python "$PY" --out "$WORK/$name.grade.json"
  echo "   criteria passed: $(json "d.get('passed'), 'of', d.get('total')" < "$WORK/$name.grade.json")"
  "$PY" "$REPO/scripts/bench/scorecard.py" collect --api "$API" --name "$name" --status "$status" \
    --company "$cid" --sprint "$sid" --spec-id "$spec_id" --grade "$WORK/$name.grade.json" --wall "$wall" \
    --pg "$PG_HOST" "$PG_PORT" "$PG_USER" "$PG_PASSWORD" "$PG_DB" --error "$error" --out "$WORK/entries/$name.json"
}

for dir in "${SPECS[@]}"; do run_spec "$dir"; done

CARD=$(cd "$REPO" && "$PY" scripts/bench/scorecard.py write --out-dir "$OUT_DIR" --llm "$LLM_LABEL" "$WORK"/entries/*.json) \
  || { echo "BENCH FAILED: the scorecard could not be written"; exit 1; }
echo "scorecard: $CARD"
cat "${CARD%.json}.md"
echo "backend errors (Neo4j aside): $(grep '"level": "ERROR"' "$WORK/backend.log" | grep -vc Neo4j)"
if [ "$LLM_LABEL" = fake ]; then
  # The fake gets every spec right: anything less is a broken circuit.
  if "$PY" -c 'import json, sys; t = json.load(open(sys.argv[1]))["totals"]; sys.exit(not (t["specs_completed"] == t["specs"] and t["criteria_passed"] == t["criteria_total"] > 0))' "$CARD"; then
    echo "BENCH PASSED (fake LLM: every spec completed, every criterion passed)"
  else
    echo "BENCH FAILED (fake LLM: the circuit did not run green; logs in $WORK)"; exit 1
  fi
fi
exit 0
