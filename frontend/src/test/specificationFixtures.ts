/** Specifications the page tests share: a draft the check blocks, and an approved one. */
import type { Specification } from '../services/specifications'

export const DRAFT: Specification = {
  id: 5, company_id: 4, title: 'Checkout', summary: 'Buy things.', status: 'draft', version: 1, supersedes_id: null,
  requirements: [{ id: 'REQ-1', title: 'Cart', description: 'Add items.', criteria: [{ id: 'REQ-1.1', given: 'a cart', when: 'an item is added', then: 'it is listed' }] }],
  quality: [{ id: 'NFR-1', attribute: 'Latency', target: 'under 200 ms' }],
  constraints: ['Postgres only'],
  questions: [{ question: 'Which currencies?', answer: null }],
  drafted_by: 'PO', approved_by: null, approved_at: null, created_at: null, updated_at: null,
  check: {
    ok: false, blocking: 1, warnings: 1,
    findings: [
      { severity: 'warning', where: 'NFR-1', code: 'vague_target', message: 'The target has no number' },
      { severity: 'blocking', where: 'Q1', code: 'unanswered', message: 'The question is unanswered' },
    ],
  },
}

export const APPROVED: Specification = {
  ...DRAFT, id: 3, title: 'Login', status: 'approved', version: 2, supersedes_id: 2, drafted_by: 'ana@acme.test',
  approved_by: 'ana@acme.test', approved_at: '2026-10-02T12:00:00Z', questions: [],
  check: { ok: true, blocking: 0, warnings: 0, findings: [] },
}
