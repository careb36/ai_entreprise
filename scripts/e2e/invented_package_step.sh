# Step 9e of scripts/e2e/run.sh, sourced from it: a delivery that names a
# package no registry has is refuted through the real loop (issue #549).
#
# The fake model's first delivery of "Add retry helper" also writes
# requirements-dev.txt naming fabrique-e2e-invented-package
# (fake_llm.INVENTED_PACKAGE); the backend looks it up at the fake registry
# (RESEARCH_PYPI_URL), which answers 404. The checks read what was observed:
# the backend's registry lookup on the evidence, the Verifier's grounded
# check, and the gate's send-back as the task's event and the board's comment,
# never what the producer said of its delivery. The next delivery names no
# package and passes the same check. The project has no stack marker, so no
# install runs for the file: the refusal is the grounded check's alone.
# Uses run.sh's check, api, json, wait_sprint, prompt_seen, run_evidence, API,
# CID and LLM_PORT.

echo "9e. a delivery naming a package no registry has is refuted (issue #549)"
INVENTED=fabrique-e2e-invented-package  # fake_llm.INVENTED_PACKAGE
NEW7=$(api -X POST "$API/v1/start_sprint" -H 'Content-Type: application/json' -d "{
  \"company_id\": $CID, \"sprint_name\": \"Sprint 7\", \"start_date\": \"2026-01-08\", \"scope_size\": \"S\",
  \"backlog_items\": [{\"title\": \"Add retry helper\", \"description\": \"A function with a test.\", \"assignee\": \"Developer\"}]}")
S7=$(echo "$NEW7" | json "d.get('sprint_id', '')")
RETRY=$(echo "$NEW7" | json "d.get('backlog_item_ids', [''])[0]")
check "Sprint 7 closes" wait_sprint "$S7"
RETRY_RUNS=$(api "$API/v1/agent_runs?company_id=$CID&external_ref=task-$RETRY" | json "' '.join(str(r['id']) for r in sorted(d['agent_runs'], key=lambda r: r['id']))")
RETRY_FIRST=${RETRY_RUNS%% *}; RETRY_LAST=${RETRY_RUNS##* }
grounded() {  # grounded <run id> <python expression over c, the latest verification's grounded check>
  api "$API/v1/agent_runs/$1/verifications?company_id=$CID" | json "(lambda c: ($2))(next((c for c in d['verifications'][-1]['checks'] if c['name'] == 'grounded'), {}))"; }
contains() { case "$1" in *"$2"*) return 0 ;; esac; return 1; }
check "the backend looked the package up at the registry itself and recorded the registry's 404 on the evidence" test "$(run_evidence "$RETRY_FIRST" "[(r['package'], r['ecosystem'], r['status'], r['exists'], r['by'], r['url']) for r in e.get('registry_lookups') or []]")" = "[('$INVENTED', 'pypi', 404, False, 'verifier', 'http://127.0.0.1:$LLM_PORT/pypi/$INVENTED/json')]"
check "the Verifier's grounded check refuted that delivery, naming the package no registry has" test "$(grounded "$RETRY_FIRST" "c.get('outcome'), c.get('missing_packages'), c.get('registry_answers')")" = "('refuted', ['pypi:$INVENTED'], 1)"
OBSERVED=$(grounded "$RETRY_FIRST" "c.get('observation', '')")
GATE=$(api "$API/v1/tasks/$RETRY/events" | json "next((e['payload'].get('text', '') for e in d['events'] if e['actor'] == 'gate' and e['payload'].get('author') == 'Verifier' and e['task_status'] == 'todo'), '')")
check "the grounded check's observation names the package" contains "$OBSERVED" "no registry has pypi:$INVENTED"
gate_reason() { [ -n "$OBSERVED" ] && contains "$GATE" "NEEDS WORK: invented dependency or import" && contains "$GATE" "$OBSERVED"; }
check "the task's events carry the gate's send-back, by the Verifier, with that observation as the reason" gate_reason
check "the board reads the task from its events, whose chain is intact" test "$(api "$API/v1/tasks/$RETRY/events" | json "d['source'], d['chain']['status']")" = "('events', 'intact')"
check "the board shows the reason on the task" test "$(api "$API/v1/sprints/$S7/board" | json "sum(1 for c in d['columns'].values() for i in c if i['id'] == $RETRY for m in i['comments'] if m.get('author') == 'Verifier' and m['text'].startswith('NEEDS WORK: invented dependency or import') and 'no registry has pypi:$INVENTED' in m['text'])")" -ge 1
check "QA never reviewed the refuted delivery" test "$(prompt_seen "reviewing the following deliverable for the task: 'Add retry helper'.*requirements-dev.txt")" = no
check "the next delivery names no package, and the same check passes it" test "$RETRY_LAST" != "$RETRY_FIRST" -a "$(grounded "$RETRY_LAST" "c.get('outcome'), c.get('missing_packages')")" = "('pass', [])"
echo "     board: $(board "$S7")"
