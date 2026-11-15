import type { C4Diagram, DocumentationPack } from '../services/documentation'

export const EVIL = '<img src=x onerror="alert(1)"><script>alert(2)</script>'

export const DIAGRAM: C4Diagram = {
  key: 'c4-container',
  level: 'container',
  title: 'Containers',
  nodes: [
    { id: 'n1', label: 'Board API', kind: 'container', detail: 'Serves tasks.', group: 'g1' },
    { id: 'n2', label: 'Store', kind: 'container', detail: 'Keeps tasks.', group: 'g1' },
    { id: 'n3', label: EVIL, kind: 'container', detail: EVIL, group: 'g1' },
    { id: 'n4', label: 'Users of Public API', kind: 'person', detail: '', group: null },
  ],
  edges: [
    { source: 'n1', target: 'n2', label: 'depends on' },
    { source: 'n3', target: 'n1', label: 'calls Tasks HTTP API' },
    { source: 'n4', target: 'n1', label: 'uses http' },
  ],
  groups: [{ id: 'g1', label: 'Board architecture' }],
  notes: ['Each module is shown as a container.'],
  mermaid: 'flowchart TB\n  n1["Board API"]\n',
  input_sha256: 'ab'.repeat(32),
}

export function pack(overrides: Partial<DocumentationPack> = {}): DocumentationPack {
  return {
    id: 11,
    company_id: 4,
    version: 2,
    status: 'draft',
    specification_id: 3,
    architecture_id: 5,
    compiled_by: 'TechWriter',
    language: 'en',
    inputs_sha256: 'cd'.repeat(32),
    approved_at: null,
    written_files: [],
    written_at: null,
    created_at: '2026-11-15T10:00:00',
    documents: [
      {
        key: 'charter', title: 'Project charter', framework: 'PMBOK', file: 'docs/project/charter.md',
        markdown: `# Project charter\n\n- Name: Board \\[S1\\]\n\n${EVIL}\n\n[click](javascript:alert(3))\n\n| A | B |\n|---|---|\n| 1 | 2 |\n\n## Sources\n\n- S1: Specification 3`,
        sources: [{ mark: 'S1', kind: 'specification', id: '3' }], missing: [], claims: [{ section: 'background', role: 'TechWriter' }],
        persons: ['Business justification', 'Budget envelope'], domain: 'Governance', focus_area: 'Initiating',
        input_sha256: '1',
      },
      {
        key: 'risks', title: 'Risk register', framework: 'PMBOK', file: 'docs/project/risks.md',
        markdown: '# Risk register\n\n_No source recorded: no risk is recorded._', sources: [], missing: ['risks'], claims: [], persons: [], domain: 'Risk', focus_area: 'Planning', input_sha256: '2',
      },
    ],
    diagrams: [DIAGRAM],
    lint: { ok: true, stale: false, blocking: 0, findings: [{ severity: 'info', where: 'risks', message: 'No record for: risks.' }], documents: [], diagrams: [] },
    ...overrides,
  }
}
