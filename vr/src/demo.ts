import type { World } from './model'

/**
 * A company a judge can open without a backend: one sprint in progress, its
 * agents, a task blocked for a person and two runs from another producer
 * waiting for a decision, one of them with the Reviewer agent's suggestion.
 */
export function demoWorld(): World {
  const at = Date.now()
  return {
    mode: 'demo',
    company: 'Acme Payments',
    sprint: 'Sprint 3 — Checkout',
    tasks: [
      { id: 31, title: 'Add retry to the payment webhook', status: 'done', assignee: 'Developer', rework: 1 },
      { id: 32, title: 'Idempotency keys for refunds', status: 'in_review', assignee: 'QA', rework: 0 },
      { id: 33, title: 'Rate limit the checkout API', status: 'in_progress', assignee: 'Developer', rework: 0 },
      { id: 34, title: 'Document the refund flow', status: 'todo', assignee: 'TechWriter', rework: 0 },
      { id: 35, title: 'Migrate order totals to integer cents', status: 'todo', assignee: 'Developer', rework: 0 },
      {
        id: 36,
        title: 'Create testing strategy document',
        status: 'blocked',
        assignee: 'QA',
        rework: 2,
        reason: 'Quality gate: the deliverable was cut short twice (under 180 words, no sections). Blocked for a person.',
      },
    ],
    agents: [
      { role: 'PO', state: 'waiting', activity: 'Backlog groomed for Sprint 3' },
      { role: 'Architect', state: 'idle', activity: 'Briefed "Rate limit the checkout API"' },
      { role: 'Developer', state: 'active', activity: 'Writing src/checkout/rate_limit.py' },
      { role: 'QA', state: 'active', activity: 'Reviewing "Idempotency keys for refunds"' },
      { role: 'ScrumMaster', state: 'waiting', activity: 'Triage: retry with a brief' },
      { role: 'Reviewer agent', state: 'waiting', activity: 'Suggested on 2 runs' },
    ],
    runs: [
      {
        id: 101,
        producer: 'github',
        externalRef: 'pr-101 · Add a greeting endpoint',
        verdict: 'verified',
        observations: ['artifact_nonempty: pass — 1,204 bytes', 'claimed_files_present: pass — app/greeting.py in the diff', 'tests_run: pass — `pytest` passed'],
        suggestion: { decision: 'approved', reason: 'The verifier confirmed the file and the tests; the claims match the observations.' },
      },
      {
        id: 102,
        producer: 'github',
        externalRef: 'pr-102 · Rewrite the refund job',
        verdict: 'refuted',
        observations: ['artifact_nonempty: pass — 8,912 bytes', 'tests_run: refuted — `pytest` exited 1: test_refund_twice failed'],
        suggestion: { decision: 'rejected', reason: 'The test run was refuted: a refund can be issued twice. The claim of passing tests is not supported.' },
      },
      {
        id: 103,
        producer: 'copilot-agent',
        externalRef: 'issue-57 · Add CSV export',
        verdict: 'unverifiable',
        observations: ['tests_run: unverifiable — no test command detected'],
      },
    ],
    selected: null,
    log: [
      { at: at - 240_000, text: 'Developer delivered "Add retry to the payment webhook"' },
      { at: at - 180_000, text: 'Verifier: files_written pass, tests_run pass' },
      { at: at - 120_000, text: 'QA approved "Add retry to the payment webhook"' },
      { at: at - 60_000, text: 'Quality gate blocked "Create testing strategy document" after 2 reworks' },
      { at: at - 30_000, text: 'ScrumMaster answered the escalation: retry with a brief' },
    ],
  }
}
