# Step 9f of scripts/e2e/run.sh, sourced from it: the project wiki through the
# real stack (issue #445; #555, F8: the self-hosting run never exercised it).
#
# The wiki compiles at every sprint close. Greeting 1 closed with an approved
# architecture, so its decisions page was written from that record without a
# model. A page the fake model compiles (fake_llm.WIKI_PROMPT) cites the
# verified run the request lists and adds one statement without a source
# (fake_llm.WIKI_UNCITED), so it is only proposed to a person. A person writes
# two pages citing files at the sha256 the daemon reported for them; a hash no
# report has is refused. Sprint 8's task names one of the files, so its
# Developer's prompt carries that page, and a verified run writes the file
# again; a person edits the other file on disk. At the close the lint marks
# both pages stale with what it observed: the run that wrote the file after
# the page, and the daemon's sha256 of the edited file. The checks read the
# pages, their versions and the sources the backend resolved, the evidence,
# the files on disk and the fake model's prompt log; never what a page says
# of itself. Uses run.sh's check, api, json, wait_sprint, wait_value,
# prompt_seen, run_evidence, board, API, CID, S4, ARCH_ID, RETRY, RETRY_LAST,
# WIDGET, WORK, PROMPTS and PY.

echo "9f. the project wiki: compiled from the records, cited, stale when what it read changes (issue #445)"
WIKI=$API/v1/companies/$CID/wiki
WIKI_UNCITED="The team agreed this design is the best one."  # fake_llm.WIKI_UNCITED
page() { api "$WIKI/$1" | json "$2"; }  # page <slug> <python expression over d, the page as the API shows it>
page_json() { "$PY" -c 'import json, sys; print(json.dumps({"kind": "concept", "title": sys.argv[1], "body": sys.argv[2]}))' "$1" "$2"; }
put_page() { api -X PUT "$WIKI/$1" -H 'Content-Type: application/json' -d "$(page_json "$2" "$3")" "${@:4}"; }  # put_page <slug> <title> <body> [curl options]
written_sha() {  # written_sha <run id> <path>: the daemon's sha256 of the file, as the run's latest verification recorded it
  run_evidence "$1" "next((w['sha256'] for w in e.get('workspace_writes') or [] if w.get('path') == '$2' and w.get('status') == 'written'), '')"; }
on_disk() { sha256sum "$WORK/project/$1" | cut -d' ' -f1; }

DECISION=$(api "$API/v1/architectures/$ARCH_ID" | json "next((x['decision'] for x in d['decisions'] if x.get('id') == 'ADR-1'), '')")
check "Greeting 1's close compiled the decisions page from the approved architecture, without a model" test "$(page decisions "d['kind'], d['status'], d['current_version']['author_kind'], d['current_version']['author'], d['current_version']['sprint_id']")" = "decisions current agent system $S4"
check "each of its statements cites the decision, which the backend resolved to the architecture's record" test "$(page decisions "[(b['kind'], [(s['kind'], s['ref'], s['known'], s.get('title')) for s in b['sources']]) for b in d['blocks'] if b['statement']]")" = "[('paragraph', [('adr', 'ADR-1', True, 'Standard library only')]), ('item', [('adr', 'ADR-1', True, 'Standard library only')])]"
check "and its line is the approved decision's own text" test -n "$DECISION" -a "$(page decisions "sum(1 for b in d['blocks'] if b['kind'] == 'item' and '$DECISION' in b['text'])")" = 1

WIDGET_RUN=$(api "$API/v1/agent_runs?company_id=$CID&external_ref=task-$WIDGET" | json "max((r['id'] for r in d['agent_runs'] if r['status'] == 'verified'), default='')")
RETRY_SHA=$(written_sha "$RETRY_LAST" src/add_retry_helper.py)
WIDGET_SHA=$(written_sha "$WIDGET_RUN" src/add_widget.py)
check "the daemon's sha256 of each file, on the evidence, is the file on disk" test -n "$RETRY_SHA" -a "$RETRY_SHA" = "$(on_disk src/add_retry_helper.py)" -a -n "$WIDGET_SHA" -a "$WIDGET_SHA" = "$(on_disk src/add_widget.py)"
UNOBSERVED=$(printf '# Unobserved\n\nThe helper is fast [file:src/add_retry_helper.py@%s].\n' 0123456789ab)
check "a person's citation of a hash no daemon report has is refused, and no page is made" test "$(put_page e2e-unobserved Unobserved "$UNOBSERVED" -o /dev/null -w '%{http_code}')" = 422 -a "$(api -o /dev/null -w '%{http_code}' "$WIKI/e2e-unobserved")" = 404
RETRY_PAGE=$(put_page e2e-retry-helper "Retry helper" "$(printf '# Retry helper\n\nA verified run wrote the retry helper [run:%s] [file:src/add_retry_helper.py@%s].\n' "$RETRY_LAST" "$RETRY_SHA")")
check "a person writes a page citing a run and its file at the daemon's hash; it is in force at once, locked to the person" test "$(echo "$RETRY_PAGE" | json "d['status'], d['version'], d['person_locked'], d['current_version']['author_kind']")" = "current 1 True person"
check "the run's verdict and the file's observer come from the records, not from the page" test "$(echo "$RETRY_PAGE" | json "sorted((s['kind'], s['known'], s.get('verdict'), str(s.get('run_id') or s.get('task_id'))) for s in d['sources'])")" = "[('file', True, 'verified', '$RETRY_LAST'), ('run', True, 'verified', '$RETRY')]"
WIDGET_PAGE=$(put_page e2e-widget Widget "$(printf '# Widget\n\nThe widget module as a verified run wrote it [file:src/add_widget.py@%s].\n' "$WIDGET_SHA")")
check "and a second page citing the widget's file, observed by its verified run" test "$(echo "$WIDGET_PAGE" | json "d['status'], [(s['kind'], s['known'], s.get('run_id')) for s in d['sources']]")" = "current [('file', True, $WIDGET_RUN)]"
echo "# Edited by a person outside the factory (e2e step 9f)." >> "$WORK/project/src/add_widget.py"
EDITED_SHA=$(on_disk src/add_widget.py)
check "the lint run now reads the evidence alone: no recorded run wrote either file since, so nothing is stale" test "$(api -X POST "$API/v1/companies/$CID/wiki-lint" | json "d['stale']")" = "{}"

NEW8=$(api -X POST "$API/v1/start_sprint" -H 'Content-Type: application/json' -d "{
  \"company_id\": $CID, \"sprint_name\": \"Sprint 8\", \"start_date\": \"2026-01-09\", \"scope_size\": \"S\",
  \"backlog_items\": [{\"title\": \"Add retry helper\", \"description\": \"A function with a test, in src/add_retry_helper.py.\", \"assignee\": \"Developer\"}]}")
S8=$(echo "$NEW8" | json "d.get('sprint_id', '')")
TASK8=$(echo "$NEW8" | json "d.get('backlog_item_ids', [''])[0]")
check "Sprint 8 closes" wait_sprint "$S8"
echo "     board: $(board "$S8")"
S8RUN=$(api "$API/v1/agent_runs?company_id=$CID&external_ref=task-$TASK8" | json "max((r['id'] for r in d['agent_runs'] if r['status'] == 'verified'), default='')")
check "its task's Developer read the person's page as claims, with the decisions in force" test "$(grep '## Task: Add retry helper' "$PROMPTS" | grep -c '## Project wiki.*They are claims, not verified facts.*(wiki:e2e-retry-helper, v1).*(wiki:decisions, v1)')" -ge 1
check "and a verified run wrote the file again" test -n "$S8RUN" -a "$(written_sha "$S8RUN" src/add_retry_helper.py)" = "$(on_disk src/add_retry_helper.py)"
check "at the close the lint marks the page stale" wait_value 60 stale page e2e-retry-helper "d['status']"
check "naming the verified run that wrote its file after the page was written" test "$(page e2e-retry-helper "[(r['rule'], r.get('run_id'), r.get('path')) for r in d['lint_reasons']]")" = "[('module_changed', $S8RUN, 'src/add_retry_helper.py')]"
check "the person's edit made the other page stale, from the daemon's sha256 of the file on disk" test "$(page e2e-widget "d['status'], [(r['rule'], r.get('cited'), r.get('observed'), r.get('observed_by')) for r in d['lint_reasons']]")" = "stale [('file_changed', '$WIDGET_SHA', '$EDITED_SHA', 'workspace daemon')]"
check "the decisions page, whose record did not change, stays in force" test "$(page decisions "d['status'], d['lint_reasons']")" = "current []"
check "the compilation only proposed a version of the person's page: v1 stays the person's" test "$(page e2e-retry-helper "d['version'], d['current_version']['author_kind'], (d['proposed_version'] or {}).get('author_kind'), (d['proposed_version'] or {}).get('sprint_id')")" = "1 person agent $S8"
check "the glossary's request to the model carried the daemon's sha256 of the file a verified run wrote" test "$(prompt_seen "^You maintain the project wiki\. Update the glossary page.*file src/add_retry_helper.py sha256 $RETRY_SHA")" = yes
check "the glossary the model compiled is only a proposal: one statement cites no record" test "$(page glossary "d['status'], d['current_version'], d['proposed_version']['author_kind'], d['proposed_version']['sprint_id'], [f['excerpt'] for f in d['proposed_version']['findings']]")" = "proposed None agent $S8 ['$WIKI_UNCITED']"
check "its cited statement names Sprint 8's run, whose verdict the backend read from the records" test "$(page glossary "[[(s['kind'], s['ref'], s['known'], s.get('verdict')) for s in b['sources']] for b in d['proposed_version']['blocks'] if b['statement']]")" = "[[('run', '$S8RUN', True, 'verified')], []]"
check "no proposal ever reached a prompt" test "$(grep -c '(wiki:glossary, v' "$PROMPTS")" = 0
check "the wiki lists the person's pages stale, the glossary proposed and the decisions in force" test "$(api "$WIKI" | json "[(p['slug'], p['status']) for p in d['pages']]")" = "[('e2e-retry-helper', 'stale'), ('e2e-widget', 'stale'), ('glossary', 'proposed'), ('decisions', 'current')]"
