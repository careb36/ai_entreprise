/** Architectures the page tests share: a draft the check blocks, and an approved one, both of the approved specification (#3). */
import type { Architecture } from '../services/architectures'

export const DRAFT_ARCHITECTURE: Architecture = {
  id: 21, company_id: 4, specification_id: 3, title: 'Login design', overview: 'A session service behind an HTTP API.',
  status: 'draft', version: 1, supersedes_id: null,
  modules: [
    { id: 'MOD-1', name: 'Sessions', responsibility: 'Issues and checks tokens.', path: 'src/sessions', depends_on: [], covers: ['REQ-1'] },
    { id: 'MOD-2', name: 'Web', responsibility: 'The HTTP layer.', path: 'src/web', depends_on: ['MOD-1'], covers: [] },
  ],
  interfaces: [
    { id: 'IF-1', name: 'Session API', kind: 'http', module_id: 'MOD-1', contract: 'POST /login\nDELETE /login', consumers: ['MOD-2'] },
  ],
  decisions: [
    { id: 'ADR-1', title: 'Stateless tokens', context: 'Several web nodes.', decision: 'Signed JWTs.', consequences: 'No session store.' },
  ],
  conventions: ['Tests beside the code'],
  questions: [{ question: 'Which token lifetime?', answer: null }],
  drafted_by: 'Architect', approved_by: null, approved_at: null,
  written_path: null, written_sha256: null, written_at: null, created_at: null, updated_at: null,
  check: {
    ok: false, blocking: 1, warnings: 1,
    findings: [
      { severity: 'warning', where: 'MOD-2', code: 'covers_nothing', message: 'The module covers no requirement' },
      { severity: 'blocking', where: 'Q1', code: 'unanswered', message: 'The question is unanswered' },
    ],
  },
}

export const APPROVED_ARCHITECTURE: Architecture = {
  ...DRAFT_ARCHITECTURE, id: 22, status: 'approved', version: 2, supersedes_id: 21, questions: [],
  drafted_by: 'ana@acme.test', approved_by: 'ana@acme.test', approved_at: '2026-10-03T12:00:00Z',
  check: { ok: true, blocking: 0, warnings: 0, findings: [] },
}
