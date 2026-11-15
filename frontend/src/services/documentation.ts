/**
 * Documentation packs (issue #530): a project's management documents (charter,
 * scope statement, WBS, schedule, RACI, risk register, lessons learned) and its
 * C4 diagrams, compiled by La Fabrique from its records. Every statement cites
 * its source; prose a model wrote is marked as a claim. A Manager compiles and
 * writes an approved pack to `docs/project/`; only an Owner approves, and only
 * while the lint finds no drift from the records. Viewers read.
 */
import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query'
import { ApiError, api, fetchApi } from './api'

export type PackStatus = 'draft' | 'approved' | 'superseded'
export type C4Kind = 'person' | 'system' | 'external' | 'container' | 'datastore' | 'component'

export interface PackSource {
  mark: string
  kind: string
  id: string
  sha256?: string
  label?: string
  claim?: boolean
}

export interface PackDocument {
  key: string
  title: string
  framework: string
  file: string
  markdown: string
  sources: PackSource[]
  missing: string[]
  claims: { section: string; role: string }[]
  /** Sections only a person can write (business case, budget, risk appetite): templates, never filled by an agent. */
  persons: string[]
  /** The PMBOK Guide 8th edition performance domain and focus area; empty for the index and the C4 diagrams. */
  domain: string
  focus_area: string
  input_sha256: string
}

export interface C4Node {
  id: string
  label: string
  kind: C4Kind
  detail: string
  group: string | null
}

export interface C4Diagram {
  key: string
  level: 'context' | 'container' | 'component'
  title: string
  nodes: C4Node[]
  edges: { source: string; target: string; label: string }[]
  groups: { id: string; label: string }[]
  notes: string[]
  mermaid: string
  input_sha256: string
}

export interface LintFinding {
  severity: 'blocking' | 'warning' | 'info'
  where: string
  message: string
}

export interface PackLint {
  ok: boolean
  stale: boolean
  blocking: number
  findings: LintFinding[]
  documents: { key: string; stale: boolean; missing: string[]; claims: number; sources: number }[]
  diagrams: { key: string; stale: boolean }[]
}

export interface PackSummary {
  id: number
  company_id: number
  version: number
  status: PackStatus
  specification_id: number | null
  architecture_id: number | null
  compiled_by: string
  inputs_sha256: string
  approved_at: string | null
  written_files: { path: string; sha256: string }[]
  written_at: string | null
  created_at: string | null
}

export interface DocumentationPack extends PackSummary {
  documents: PackDocument[]
  diagrams: C4Diagram[]
  lint: PackLint
}

/** A refusal with the lint's blocking findings (the pack drifted from the records). */
export class PackConflict extends ApiError {
  constructor(status: number, message: string, public findings: LintFinding[] = []) {
    super(status, message, message)
    this.name = 'PackConflict'
  }
}

async function approve(id: number): Promise<DocumentationPack> {
  try {
    return await api.post<DocumentationPack>(`/v1/documentation/${id}/approve`, {})
  } catch (error) {
    if (error instanceof ApiError && error.data && typeof error.data === 'object') {
      const structured = error.data as { message?: string; findings?: LintFinding[] }
      throw new PackConflict(error.status, structured.message ?? error.message, Array.isArray(structured.findings) ? structured.findings : [])
    }
    throw error
  }
}

export const documentationApi = {
  list: (companyId: number) => api.get<{ packs: PackSummary[] }>(`/v1/companies/${companyId}/documentation`),
  get: (id: number) => api.get<DocumentationPack>(`/v1/documentation/${id}`),
  /** Compiles from the records; the TechWriter's prose is one model call, so sent once and waited on. */
  compile: (companyId: number, prose: boolean) =>
    fetchApi<DocumentationPack>(`/v1/companies/${companyId}/documentation`, {
      method: 'POST', body: JSON.stringify({ prose }), once: true, timeoutMs: 180_000,
    }),
  approve,
  /** Writes through the workspace daemon: sent once, and a 503 (no daemon) is not retried. */
  write: (id: number) =>
    fetchApi<DocumentationPack>(`/v1/documentation/${id}/write`, { method: 'POST', body: '{}', once: true, timeoutMs: 120_000 }),
}

export const packsKey = (companyId: number) => ['documentation-packs', companyId] as const
export const packKey = (id: number) => ['documentation-pack', id] as const

export function usePacks(companyId: number | null) {
  return useQuery({
    queryKey: packsKey(companyId ?? 0),
    queryFn: () => documentationApi.list(companyId!),
    enabled: companyId !== null,
  })
}

export function usePack(id: number | null) {
  return useQuery({
    queryKey: packKey(id ?? 0),
    queryFn: () => documentationApi.get(id!),
    enabled: id !== null,
  })
}

/** Every write answers with the full pack: keep it, and refresh the list. */
function useSettle() {
  const queryClient = useQueryClient()
  return (pack: DocumentationPack) => {
    queryClient.setQueryData(packKey(pack.id), pack)
    queryClient.invalidateQueries({ queryKey: packsKey(pack.company_id) })
  }
}

export function useCompilePack() {
  const settle = useSettle()
  return useMutation({
    mutationFn: ({ companyId, prose }: { companyId: number; prose: boolean }) => documentationApi.compile(companyId, prose),
    onSuccess: settle,
  })
}

export function useApprovePack() {
  const settle = useSettle()
  return useMutation({ mutationFn: (id: number) => documentationApi.approve(id), onSuccess: settle })
}

export function useWritePack() {
  const settle = useSettle()
  return useMutation({ mutationFn: (id: number) => documentationApi.write(id), onSuccess: settle })
}
