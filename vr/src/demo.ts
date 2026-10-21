import type { Action, World } from './model'

/** A step of the demo's first minute: what happens, and when, after the room opens. */
export interface TimelineStep {
  /** Milliseconds after the room opens. */
  at: number
  actions: Action[]
}

/**
 * The company keeps working after a judge puts the headset on: a delivery,
 * the Verifier's observations, a review request, a brief, a new run in the
 * inbox. One pass, so the room is alive in its first minute and nothing
 * loops behind the person's back.
 */
export function demoTimeline(): TimelineStep[] {
  return [
    {
      at: 6_000,
      actions: [
        { type: 'event', text: 'Developer wrote src/checkout/rate_limit.py and its tests' },
        { type: 'agent', agent: { role: 'Developer', state: 'active', activity: 'Running the tests for rate_limit' } },
      ],
    },
    {
      at: 14_000,
      actions: [
        { type: 'event', text: 'Verifier: files_written pass (2 files), tests_run pass — `pytest` passed' },
        { type: 'task', task: { id: 33, status: 'in_review' } },
        { type: 'agent', agent: { role: 'Developer', state: 'waiting', activity: 'Handed "Rate limit the checkout API" to QA' } },
        { type: 'agent', agent: { role: 'QA', state: 'active', activity: 'Reviewing "Rate limit the checkout API"' } },
      ],
    },
    {
      at: 24_000,
      actions: [
        { type: 'event', text: 'QA → Developer (request): NEEDS WORK: cover the burst case in the tests' },
        { type: 'task', task: { id: 33, status: 'in_progress', rework: 1 } },
        { type: 'agent', agent: { role: 'Developer', state: 'active', activity: 'Rework: the burst case' } },
        { type: 'agent', agent: { role: 'QA', state: 'waiting', activity: 'Asked for one rework' } },
      ],
    },
    {
      at: 34_000,
      actions: [
        { type: 'event', text: 'Architect → Developer (answer): token bucket per key; test the burst window first' },
        { type: 'agent', agent: { role: 'Architect', state: 'waiting', activity: 'Briefed the burst case' } },
      ],
    },
    {
      at: 46_000,
      actions: [
        {
          type: 'run',
          run: {
            id: 104,
            producer: 'github',
            externalRef: 'pr-104 · Fix the CSV header',
            verdict: 'verified',
            observations: ['artifact_nonempty: pass — 640 bytes', 'claimed_files_present: pass — export/csv.py in the diff'],
            suggestion: { decision: 'approved', reason: 'A one-line fix the verifier confirmed; the claims match the diff.' },
          },
        },
        { type: 'event', text: 'Reviewer agent suggested approving pr-104 · Fix the CSV header' },
        { type: 'agent', agent: { role: 'Reviewer agent', state: 'waiting', activity: 'Suggested on 3 runs' } },
      ],
    },
    {
      at: 58_000,
      actions: [
        { type: 'event', text: 'Developer delivered "Rate limit the checkout API" again, burst case covered' },
        { type: 'task', task: { id: 33, status: 'in_review' } },
        { type: 'agent', agent: { role: 'Developer', state: 'waiting', activity: 'Delivered the rework' } },
        { type: 'agent', agent: { role: 'QA', state: 'active', activity: 'Reviewing the rework' } },
      ],
    },
  ]
}

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
