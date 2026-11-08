/**
 * Architectures: how the agents will build what an approved specification
 * asks for. The Architect drafts one from the specification (or a person
 * writes it), a Manager answers its open questions and approves it once the
 * deterministic check finds nothing blocking, and an approved one is written
 * to the repository as `docs/ARCHITECTURE.md` through the workspace daemon.
 * Viewers read; Managers write.
 */
import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query'
import type { Source } from '../components/SourcesConsulted'
import { API_BASE } from '../lib/apiBase'
import { ApiError, api, fetchApi } from './api'
import type { Check, Finding, Question } from './specifications'
import { useAuthStore } from '../store/useAuthStore'

export type ArchitectureStatus = 'draft' | 'approved' | 'superseded'
export type InterfaceKind = 'http' | 'function' | 'event' | 'cli' | 'schema' | 'other'

export const INTERFACE_KINDS: InterfaceKind[] = ['http', 'function', 'event', 'cli', 'schema', 'other']

export interface ArchModule {
  /** `MOD-1`. */
  id: string
  name: string
  responsibility: string
  path: string
  /** Module ids. */
  depends_on: string[]
  /** Requirement ids of the specification, like `REQ-2`. */
  covers: string[]
}

export interface ArchInterface {
  /** `IF-1`. */
  id: string
  name: string
  kind: InterfaceKind
  /** The module that provides it. */
  module_id: string
  /** Multi-line. */
  contract: string
  /** Module ids. */
  consumers: string[]
}

export interface ArchDecision {
  /** `ADR-1`. */
  id: string
  title: string
  context: string
  decision: string
  consequences: string
}

export interface Architecture {
  id: number
  company_id: number
  specification_id: number
  title: string
  overview: string
  status: ArchitectureStatus
  version: number
  supersedes_id: number | null
  modules: ArchModule[]
  interfaces: ArchInterface[]
  decisions: ArchDecision[]
  conventions: string[]
  questions: Question[]
  /** The sources the Architect consulted while drafting (the research tools); observations, not its claims. */
  sources?: Source[]
  /** `Architect`, or the email of the person who wrote it. */
  drafted_by: string | null
  approved_by: string | null
  approved_at: string | null
  /** Set once the daemon wrote the document to the repository; the hash is what it read back. */
  written_path: string | null
  written_sha256: string | null
  written_at: string | null
  created_at: string | null
  updated_at: string | null
  /** `Finding.where` is `MOD-2`, `IF-1`, `ADR-1`, `REQ-3`, `Q1` or `arch`. */
  check: Check
}

/** What a person writes; ids are assigned by the backend on save, and names map to ids. */
export interface ArchitectureBody {
  title: string
  overview: string
  modules: { name: string; responsibility: string; path: string; depends_on: string[]; covers: string[] }[]
  interfaces: { name: string; kind: InterfaceKind; module: string; contract: string; consumers: string[] }[]
  decisions: { title: string; context: string; decision: string; consequences: string }[]
  conventions: string[]
}

export interface ArchitectureDraftRequest {
  specification_id: number
  /** Revise this draft from its answered questions instead of starting afresh. */
  architecture_id?: number
}

/** A 409 that carries more than a message: the findings that block an approval, or the revision already in draft. */
export class ArchitectureConflict extends ApiError {
  constructor(status: number, message: string, public findings: Finding[] = [], public architectureId: number | null = null) {
    super(status, message, message)
    this.name = 'ArchitectureConflict'
  }
}

function authHeaders(contentType?: string) {
  const token = useAuthStore.getState().getValidToken()
  const headers = new Headers()
  if (contentType) headers.set('Content-Type', contentType)
  if (token) headers.set('Authorization', ['Bearer', token].join(' '))
  return headers
}

/** A decision POST through the shared client (its 401 handling included); a structured refusal becomes an `ArchitectureConflict`. */
async function postDecision<T>(endpoint: string): Promise<T> {
  try {
    return await api.post<T>(endpoint, {})
  } catch (error) {
    if (error instanceof ApiError && error.data && typeof error.data === 'object') {
      const structured = error.data as { message?: string; findings?: Finding[]; architecture_id?: number }
      throw new ArchitectureConflict(
        error.status,
        structured.message ?? error.message,
        Array.isArray(structured.findings) ? structured.findings : [],
        typeof structured.architecture_id === 'number' ? structured.architecture_id : null,
      )
    }
    throw error
  }
}

/** The Markdown document as the repository gets it: text, with the token (an `<a href>` could not send it). */
async function fetchDocument(id: number): Promise<string> {
  const response = await fetch(`${API_BASE}/v1/architectures/${id}/document`, { headers: authHeaders() })
  if (!response.ok) {
    let detail: string | undefined
    try {
      const body = await response.json()
      if (typeof body?.detail === 'string') detail = body.detail
    } catch {
      detail = undefined
    }
    throw new ApiError(response.status, detail ?? `HTTP ${response.status}`, detail)
  }
  return response.text()
}

export const architecturesApi = {
  list: (specificationId: number) =>
    api.get<{ architectures: Architecture[] }>('/v1/architectures', { specification_id: specificationId }),
  get: (id: number) => api.get<Architecture>(`/v1/architectures/${id}`),
  document: (id: number) => fetchDocument(id),
  create: (specificationId: number, body: ArchitectureBody) =>
    api.post<Architecture>('/v1/architectures', { specification_id: specificationId, ...body }),
  update: (id: number, body: ArchitectureBody) => api.put<Architecture>(`/v1/architectures/${id}`, body),
  /** The Architect drafts or revises: an LLM call, sent once and waited on. */
  draft: (request: ArchitectureDraftRequest) =>
    fetchApi<Architecture>('/v1/architectures/draft', { method: 'POST', body: JSON.stringify(request), once: true, timeoutMs: 240_000 }),
  answer: (id: number, index: number, answer: string) =>
    api.post<Architecture>(`/v1/architectures/${id}/questions/${index}`, { answer }),
  approve: (id: number) => postDecision<Architecture>(`/v1/architectures/${id}/approve`),
  revise: (id: number) => postDecision<Architecture>(`/v1/architectures/${id}/revise`),
  /** Writes the document through the workspace daemon: sent once, and a 503 (no daemon) is not retried. */
  write: (id: number) =>
    fetchApi<Architecture>(`/v1/architectures/${id}/write`, { method: 'POST', body: '{}', once: true, timeoutMs: 120_000 }),
}

export const architecturesKey = (specificationId: number) => ['architectures', specificationId] as const
export const architectureKey = (id: number) => ['architecture', id] as const

export function useArchitectures(specificationId: number | null) {
  return useQuery({
    queryKey: architecturesKey(specificationId ?? 0),
    queryFn: () => architecturesApi.list(specificationId!),
    enabled: specificationId !== null,
  })
}

export function useArchitecture(id: number | null) {
  return useQuery({
    queryKey: architectureKey(id ?? 0),
    queryFn: () => architecturesApi.get(id!),
    enabled: id !== null,
  })
}

/** Every write answers with the full architecture: keep it, and refresh the specification's list. */
function useSettle() {
  const queryClient = useQueryClient()
  return (architecture: Architecture) => {
    queryClient.setQueryData(architectureKey(architecture.id), architecture)
    queryClient.invalidateQueries({ queryKey: architecturesKey(architecture.specification_id) })
  }
}

export function useDraftArchitecture() {
  const settle = useSettle()
  return useMutation({ mutationFn: (request: ArchitectureDraftRequest) => architecturesApi.draft(request), onSuccess: settle })
}

export function useCreateArchitecture() {
  const settle = useSettle()
  return useMutation({
    mutationFn: ({ specification_id, ...body }: { specification_id: number } & ArchitectureBody) => architecturesApi.create(specification_id, body),
    onSuccess: settle,
  })
}

export function useUpdateArchitecture() {
  const settle = useSettle()
  return useMutation({
    mutationFn: ({ id, body }: { id: number; body: ArchitectureBody }) => architecturesApi.update(id, body),
    onSuccess: settle,
  })
}

export function useAnswerArchitectureQuestion() {
  const settle = useSettle()
  return useMutation({
    mutationFn: ({ id, index, answer }: { id: number; index: number; answer: string }) => architecturesApi.answer(id, index, answer),
    onSuccess: settle,
  })
}

export function useApproveArchitecture() {
  const settle = useSettle()
  return useMutation({ mutationFn: (id: number) => architecturesApi.approve(id), onSuccess: settle })
}

/** A new draft (version + 1) that supersedes the approved one once approved itself. */
export function useReviseArchitecture() {
  const settle = useSettle()
  return useMutation({ mutationFn: (id: number) => architecturesApi.revise(id), onSuccess: settle })
}

/** Writes `docs/ARCHITECTURE.md` to the company's project through the daemon; the hash in the answer is what it read back. */
export function useWriteArchitecture() {
  const settle = useSettle()
  return useMutation({ mutationFn: (id: number) => architecturesApi.write(id), onSuccess: settle })
}

export function architecturePath(specificationId: number, id: number | 'new') {
  return id === 'new'
    ? `/company/specifications/${specificationId}/architecture/new`
    : `/company/specifications/${specificationId}/architectures/${id}`
}

/** The architecture a specification page shows: the approved one first, else the newest draft, else the newest. */
export function latestArchitecture(architectures: Architecture[]): Architecture | null {
  return architectures.find(a => a.status === 'approved')
    ?? architectures.find(a => a.status === 'draft')
    ?? architectures[0]
    ?? null
}
