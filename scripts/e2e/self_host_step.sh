# Step 9d of scripts/e2e/run.sh, sourced from it: the factory builds itself (issue #329).
#
# scripts/self_host/self_host.py makes a "La Fabrique" company whose project
# is a snapshot of this repository (never this checkout), with the Git
# workflow, the delivery tests and the sandbox on; imports La Fabrique's own
# specification and architecture and checks that the stored record renders
# docs/ARCHITECTURE.md and docs/adr/ unchanged; turns issue #535 into a
# specification, revises the architecture for it, plans a sprint with the
# acceptance tests first and runs it with the fake model; a person reads the
# sprint's integration and merges it locally; the documentation pack is
# compiled in English and Spanish. Run twice: the second run makes nothing new.
# The e2e's sandbox has none of the repository's toolchains, so the company
# runs one root command (the sprint's tests), not La Fabrique's suites per
# folder (`--profile sprint-tests`, scripts/self_host/README.md).
# Uses run.sh's check, json, API, PY, REPO, WORK, SANDBOX, TEST_COMMAND, TIMEOUT.

echo "9d. the factory builds itself (issue #329)"
SELF=$WORK/self-host
mkdir -p "$SELF"
rm -rf "$SELF/la-fabrique" "$SELF/la-fabrique.self_host.json"
self_host() {  # self_host <report name> <harness arguments...>
  local name=$1; shift
  "$PY" "$REPO/scripts/self_host/self_host.py" --api "$API" --checkout "$SELF/la-fabrique" \
    --sandbox "$SANDBOX" --profile sprint-tests --test-command "$TEST_COMMAND tests" --timeout "$TIMEOUT" \
    --report "$SELF/$name.json" "$@" > "$SELF/$name.log" 2>&1
}
report() { "$PY" -c "import sys, json; d = json.load(open(sys.argv[1])); print($2)" "$SELF/$1.json"; }
self_host first --steps checkout,company,records,issue,wait
check "the harness ran without an API error" test "$(report first "d.get('error', '')")" = ""
check "the company's project is a snapshot of this repository, outside it, as one commit" test "$(report first "d['checkout']['created'], d['checkout']['files'] > 1000")" = "True True"
check "an existing repository: init_company planned no foundation sprint, as the daemon observed (retrospective F8)" test "$(report first "(d['company'].get('foundation_sprint') or {}).get('planned'), (d['company'].get('foundation_sprint') or {}).get('reason')")" = "False repository_with_code"
check "with the Git workflow on its main branch, the delivery tests named, and the sandbox set ($SANDBOX)" test "$(report first "d['company']['git_workflow'], d['company']['git_base_branch'], d['company']['test_command'].endswith(' tests'), d['company']['sandbox']")" = "True main True $SANDBOX"
check "La Fabrique's own specification and architecture pass the product's checks and are approved" test "$(report first "[d['records'][k] for k in ('specification_status', 'specification_blocking', 'architecture_status', 'architecture_blocking', 'modules', 'decisions')]")" = "['approved', 0, 'approved', 0, 15, 6]"
check "the stored record renders docs/ARCHITECTURE.md as the repository has it" test "$(report first "d['records']['document_matches']")" = True
check "and docs/adr/0012 to 0017, one per decision, as the repository has them" test "$(report first "len(d['records']['adrs']), d['records']['adrs'][0], d['records']['adr_differences']")" = "6 docs/adr/0012-dependency-rule.md []"
check "the daemon wrote it into the snapshot, and git sees no change" test "$(report first "d['records']['written_path'], d['records']['written_unchanged_in_git']")" = "docs/ARCHITECTURE.md True"
check "issue #535 is a specification with five criteria, approved without a blocking finding" test "$(report first "d['issue']['criteria'], [f for f in d['issue']['specification_findings'] if f[0] == 'blocking']")" = "['REQ-1.1', 'REQ-1.2', 'REQ-2.1', 'REQ-3.1', 'REQ-3.2'] []"
check "the Architect's revision covers it with the contexts, and adds ADR-7" test "$(report first "d['issue']['architecture_findings'], d['issue']['new_decision']")" = "[] ADR-7"
check "the PO planned the acceptance tests first, one task per requirement, nothing uncovered" test "$(report first "[(t['kind'], t['assignee']) for t in d['issue']['plan']], d['issue']['uncovered']")" = "[('acceptance_tests', 'QA'), ('acceptance_tests', 'QA'), ('acceptance_tests', 'QA'), (None, 'Developer'), (None, 'Developer'), (None, 'Developer')] []"
check "the sprint on La Fabrique closes" test "$(report first "d['wait']['status']")" = completed
check "QA's three acceptance-test tasks are done" test "$(report first "sum(1 for b in d['wait']['board'] if b.startswith('Acceptance tests for REQ-') and b.endswith('=done'))")" = 3
check "and at least two of the three implementations" test "$(report first "sum(1 for b in d['wait']['board'] if b.startswith('Add ') and b.endswith('=done'))")" -ge 2
echo "     board: $(report first "' '.join(d['wait']['board'])")"
# What the retrospective (docs/retrospectives/2026-11-self-hosting.md) reads:
# an implementation task's branch holds only its own dependencies' acceptance
# tests, so the Verifier takes the others as edited and restores them there.
SELF_SPRINT=$(report first "d['issue']['sprint_id']")
echo "     deliveries refuted for an acceptance test of another requirement: $(api "$API/v1/sprints/$SELF_SPRINT/board" | json "sum(1 for c in d['columns'].values() for i in c if i['kind'] != 'acceptance_tests' for m in i['comments'] if 'acceptance test edited' in m['text'])")"
self_host review --steps review --accept "Reviewed by a person against the specification of issue #535 (e2e)." --merge
check "each task was delivered on its own branch of the snapshot" test "$(report review "len([b for b in d['review']['branches'] if b.startswith('fabrique/task-')])")" = 6
check "the sprint's integration is the one request a person reads: merged into main locally when its suite passed, refused when it failed" test "$(report review "(lambda i, m: bool(i) and ((i['status'] == 'tests_passed' and 'refused' not in m) or (i['status'] == 'tests_failed' and 'The suite failed on the integrated branch' in m.get('refused', ''))))(d['review']['integration'], d['review']['merged'] or {})")" = True
check "a person's review of the integration is recorded before its merge (F7): approved when its suite passed, refused when it failed" test "$(report review "(lambda i, r: bool(i) and ((i['status'] == 'tests_passed' and r.get('outcome') == 'approved') or (i['status'] == 'tests_failed' and 'suite failed' in r.get('refused', ''))))(d['review']['integration'], d['review']['integration_review'] or {})")" = True
echo "     integration: $(report review "d['review']['integration'].get('status'), d['review']['base_log'][:2]")"
self_host pack --steps pack
check "the documentation pack compiles for La Fabrique in English and Spanish, every document written, nothing stale" test "$(report pack "[(p['language'], len(p['documents']) > 5, p['empty_documents'], p['lint_blocking']) for p in d['pack'].values()]")" = "[('en', True, [], 0), ('es', True, [], 0)]"
echo "     pack: $(report pack "{k: (len(p['documents']), p['missing_sections'], p['lint_warnings']) for k, p in d['pack'].items()}") (documents, sections without a record, warnings)"
self_host again --steps company,records,issue
check "run again, the harness finds what it made instead of making it twice" test "$(report again "d['company']['created'], d['records']['specification_id'] == $(report first "d['records']['specification_id']"), d['issue']['sprint_id'] == $(report first "d['issue']['sprint_id']")")" = "False True True"
