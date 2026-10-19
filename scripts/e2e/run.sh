#!/usr/bin/env bash
# End-to-end run of the company loop against a real stack.
#
# Starts the backend, the workspace daemon and a fake LLM proxy against a
# Postgres and a NATS you provide (docker compose up postgres nats, or any
# local pair), then drives one company through the loop and checks each
# step from the outside, through the API:
#
#   1. init_company plans the foundation sprint; its one documentation task
#      is too short for the text checks, is sent back twice and then blocked
#      for a person; the sprint closes anyway, with its retrospective, and the
#      ScrumMaster's proposal is adopted (autonomy level 3).
#   2. The blocked task is listed in the inbox.
#   3. start_sprint with carry_over_ids gives it another attempt beside a new
#      task; the new attempt's prompts carry "Previous attempt"; the new task
#      is delivered, reviewed and approved; the sprint closes.
#   4. The inbox no longer lists the original task.
#
# Environment (defaults in brackets):
#   E2E_POSTGRES_HOST [127.0.0.1]  E2E_POSTGRES_PORT [5432]
#   E2E_POSTGRES_USER [factory]    E2E_POSTGRES_PASSWORD [$POSTGRES_PASSWORD]
#   E2E_POSTGRES_DB [factory_e2e]  (dropped and recreated on every run)
#   E2E_NATS_URL [nats://127.0.0.1:4222]
#   E2E_BACKEND_PORT [18000]       E2E_LLM_PORT [15100]
#   E2E_PYTHON [python]            the backend's interpreter (poetry env)
#   E2E_WORKDIR [mktemp]           logs and the project folder
#   E2E_TIMEOUT [300]              seconds to wait for each sprint to close
#
# Exit status is 0 only when every check passed. Logs stay in E2E_WORKDIR.
set -u
REPO=$(cd "$(dirname "$0")/../.." && pwd)
PG_HOST=${E2E_POSTGRES_HOST:-127.0.0.1}
PG_PORT=${E2E_POSTGRES_PORT:-5432}
PG_USER=${E2E_POSTGRES_USER:-factory}
PG_PASSWORD=${E2E_POSTGRES_PASSWORD:-${POSTGRES_PASSWORD:-}}
PG_DB=${E2E_POSTGRES_DB:-factory_e2e}
NATS_URL=${E2E_NATS_URL:-nats://127.0.0.1:4222}
BACKEND_PORT=${E2E_BACKEND_PORT:-18000}
LLM_PORT=${E2E_LLM_PORT:-15100}
PY=${E2E_PYTHON:-python}
TIMEOUT=${E2E_TIMEOUT:-300}
WORK=${E2E_WORKDIR:-$(mktemp -d -t fabrique-e2e-XXXXXX)}
mkdir -p "$WORK/project" "$WORK/audit"
echo "# E2E project" > "$WORK/project/README.md"
API=http://127.0.0.1:$BACKEND_PORT
PROMPTS=$WORK/fake_llm.prompts.log
FAILED=0

check() {  # check <description> <shell test...>
  local what=$1; shift
  if "$@"; then echo "  ok   $what"; else echo "  FAIL $what"; FAILED=1; fi
}

# A fresh database: the backend creates its own schema at startup.
"$PY" - "$PG_HOST" "$PG_PORT" "$PG_USER" "$PG_PASSWORD" "$PG_DB" <<'EOF' || { echo "could not recreate the database"; exit 2; }
import sys
import psycopg2
host, port, user, password, db = sys.argv[1:]
conn = psycopg2.connect(host=host, port=port, user=user, password=password, dbname="postgres")
conn.autocommit = True
with conn.cursor() as cur:
    cur.execute(f'DROP DATABASE IF EXISTS "{db}"')
    # UTF8 whatever the cluster default is: sprint names carry em dashes.
    cur.execute(f"CREATE DATABASE \"{db}\" ENCODING 'UTF8' TEMPLATE template0 LC_COLLATE 'C' LC_CTYPE 'C'")
conn.close()
EOF

rm -f "$PROMPTS"
FAKE_LLM_PORT=$LLM_PORT FAKE_LLM_PROMPTS=$PROMPTS "$PY" "$REPO/scripts/e2e/fake_llm.py" > "$WORK/llm.log" 2>&1 & LPID=$!
NATS_URL=$NATS_URL WORKSPACE_ROOT="$WORK/project" "$PY" "$REPO/vscode-daemon/daemon.py" > "$WORK/daemon.log" 2>&1 & DPID=$!
(
  cd "$REPO/backend" && exec env \
    POSTGRES_HOST=$PG_HOST POSTGRES_PORT=$PG_PORT POSTGRES_USER=$PG_USER POSTGRES_PASSWORD=$PG_PASSWORD POSTGRES_DB=$PG_DB \
    NATS_URL=$NATS_URL LLM_PROXY_URL=http://127.0.0.1:$LLM_PORT NEO4J_URI=bolt://127.0.0.1:1 \
    AUTH_REQUIRED=false JWT_SECRET_KEY=e2e-secret-e2e-secret-e2e-secret-e2e CONSOLE_ASSISTANTS_ENABLED=false \
    AUTO_SELF_IMPROVEMENT_ENABLED=false AGENT_GRAPH_ENABLED=false AUDIT_LOG_PATH="$WORK/audit" \
    PYTHONPATH=.:app "$PY" -m uvicorn app.main:app --host 127.0.0.1 --port "$BACKEND_PORT"
) > "$WORK/backend.log" 2>&1 & BPID=$!
cleanup() { kill $BPID $DPID $LPID 2>/dev/null; wait 2>/dev/null; }
trap cleanup EXIT

for _ in $(seq 1 60); do curl -sf "$API/health" >/dev/null && break; sleep 1; done
curl -sf "$API/health" >/dev/null || { echo "backend did not come up; see $WORK/backend.log"; exit 2; }
echo "stack up: $API (logs in $WORK)"

api() { curl -s "$@"; }
json() { "$PY" -c "import sys, json; d = json.load(sys.stdin); print($1)"; }
sprint_status() { api "$API/v1/sprints?company_id=$CID" | json "next((s['status'] for s in d if s['id'] == $1), '')"; }
board() { api "$API/v1/sprints/$1/board" | json "' '.join(f\"{i['title']}={i['status']}\" for c in d['columns'].values() for i in c)"; }
wait_sprint() {  # wait_sprint <sprint id>
  local start=$SECONDS
  while (( SECONDS - start < TIMEOUT )); do
    [ "$(sprint_status "$1")" = "completed" ] && return 0
    sleep 3
  done
  return 1
}

echo "1. init_company → foundation sprint"
COMPANY=$(api -X POST "$API/v1/init_company" -H 'Content-Type: application/json' -d "{
  \"company_name\": \"E2E Co\", \"project_path\": \"$WORK/project\", \"autonomy_level\": 3,
  \"agents\": [{\"role\": \"Developer\", \"name\": \"Dev Ana\"}, {\"role\": \"QA\", \"name\": \"QA Quinn\"}, {\"role\": \"ScrumMaster\", \"name\": \"Sam\"}]}")
CID=$(echo "$COMPANY" | json "d.get('company_id', '')")
S0=$(echo "$COMPANY" | json "d.get('sprint_id', '')")
[ -n "$CID" ] && [ -n "$S0" ] || { echo "init_company failed: $COMPANY"; exit 2; }
check "the foundation sprint closes" wait_sprint "$S0"
echo "     board: $(board "$S0")"
check "its task is blocked for a person" test "$(board "$S0")" = "Create testing strategy document=blocked"
check "the quality gate asked for rework twice" test "$(api "$API/v1/messages?company_id=$CID&kind=request" | json "len(d['messages'])")" = 2
check "the ScrumMaster posted the retrospective" test "$(api "$API/v1/messages?company_id=$CID&kind=retrospective" | json "len(d['messages'])")" = 1
check "the ScrumMaster answered the escalation" test "$(api "$API/v1/messages?company_id=$CID&kind=decision" | json "sum(1 for m in d['messages'] if m['body'].startswith('Triage decision: another attempt'))")" = 1
check "a procedure was proposed and adopted (autonomy 3)" test "$(api "$API/v1/procedures?company_id=$CID" | json "sum(1 for p in d['procedures'] if p['status'] == 'active')")" = 1

echo "2. the inbox"
BLOCKED_ID=$(api "$API/v1/inbox?company_id=$CID" | json "d['blocked_tasks'][0]['id'] if d['blocked_tasks'] else ''")
check "lists the blocked task" test -n "$BLOCKED_ID"

echo "3. start_sprint with carry_over_ids"
NEW=$(api -X POST "$API/v1/start_sprint" -H 'Content-Type: application/json' -d "{
  \"company_id\": $CID, \"sprint_name\": \"Sprint 1\", \"start_date\": \"2026-01-02\", \"scope_size\": \"S\",
  \"backlog_items\": [{\"title\": \"Add parser\", \"description\": \"A function with a test.\", \"assignee\": \"Developer\"}],
  \"carry_over_ids\": [${BLOCKED_ID:-0}]}")
S1=$(echo "$NEW" | json "d.get('sprint_id', '')")
check "the sprint starts with two tasks" test "$(echo "$NEW" | json "len(d.get('backlog_item_ids', []))")" = 2
check "Sprint 1 closes" wait_sprint "$S1"
echo "     board: $(board "$S1")"
check "the new task is done and the retried one blocked again" test "$(board "$S1")" = "Add parser=done Create testing strategy document=blocked"
check "the retried task's prompts carry the previous attempt" test "$(grep -c '## Previous attempt' "$PROMPTS")" -ge 1
check "and the ScrumMaster's brief" test "$(grep -c 'Brief for the next attempt: Write the full document' "$PROMPTS")" -ge 1
check "files were written to the project" test -f "$WORK/project/src/add_parser.py" -a -f "$WORK/project/tests/test_add_parser.py"
check "the delivery was verified" test "$(api "$API/v1/agent_runs?company_id=$CID" | json "sum(1 for r in d['agent_runs'] if r['status'] == 'verified')")" -ge 1

echo "4. the inbox after the carry-over"
check "no longer lists the original task" test "$(api "$API/v1/inbox?company_id=$CID" | json "[t['id'] for t in d['blocked_tasks']]")" != "[$BLOCKED_ID]"
check "lists the new attempt" test "$(api "$API/v1/inbox?company_id=$CID" | json "len(d['blocked_tasks'])")" = 1
check "counts no run of the company's own for a person" test "$(api "$API/v1/inbox?company_id=$CID" | json "d['pending_runs']")" = 0

echo "backend errors (Neo4j aside): $(grep '"level": "ERROR"' "$WORK/backend.log" | grep -vc Neo4j)"
if [ "$FAILED" = 0 ]; then echo "E2E PASSED"; else echo "E2E FAILED (logs in $WORK)"; fi
exit $FAILED
