import type { Http } from './client'

/** The project wiki (issue #445): what the project knows, compiled from the evidence. */
export const WIKI_KINDS = ['module', 'concept', 'glossary', 'decisions'] as const
export type WikiKind = typeof WIKI_KINDS[number]
export type WikiStatus = 'current' | 'stale' | 'proposed'

/** A citation as the backend resolved it against the records: never trusted from the text. */
export interface WikiSource {
  kind: string
  ref: string
  sha256?: string
  known: boolean
  verdict?: string | null
  title?: string | null
  status?: string | null
  run_id?: number | null
  task_id?: number | null
}

/** One block of a page: a statement shows its sources beside it. */
export interface WikiBlock {
  kind: string
  line: number
  text: string
  sources: WikiSource[]
  statement: boolean
}

export interface WikiLintReason {
  rule: string
  text: string
}

export interface WikiVersion {
  id: number
  page_id: number
  number: number
  author_kind: 'agent' | 'person'
  author: string
  sprint_id: number | null
  title: string
  body: string
  sources: WikiSource[]
  findings: { line: number; excerpt: string }[]
  status: 'current' | 'proposed' | 'rejected' | 'superseded'
  decided_by: string | null
  decided_at: string | null
  decision_reason: string | null
  created_at: string | null
  blocks?: WikiBlock[]
}

export interface WikiPageSummary {
  id: number
  slug: string
  kind: WikiKind
  title: string
  status: WikiStatus
  version: number
  person_locked: boolean
  proposed_version_id: number | null
  module: { id: string | null; name: string | null; path: string | null } | null
  lint_reasons: Record<string, unknown>[]
  linted_at: string | null
  updated_at: string | null
}

export interface WikiPageDetail extends WikiPageSummary {
  body: string
  language: string
  blocks: WikiBlock[]
  sources: WikiSource[]
  links: { kind: string; target: string; sha256: string | null }[]
  lint: WikiLintReason[]
  current_version: WikiVersion | null
  proposed_version: WikiVersion | null
}

export interface WikiListResponse {
  pages: WikiPageSummary[]
  kinds: WikiKind[]
  language: string
  settings: { write_to_repo: boolean }
}

export interface WikiPageWrite {
  kind: WikiKind
  title: string
  body: string
}

export const wikiEndpoints = (api: Http) => ({
  getWiki: (companyId: number) => api.get<WikiListResponse>(`/v1/companies/${companyId}/wiki`),

  getWikiPage: (companyId: number, slug: string) =>
    api.get<WikiPageDetail>(`/v1/companies/${companyId}/wiki/${encodeURIComponent(slug)}`),

  getWikiVersions: (companyId: number, slug: string) =>
    api.get<{ versions: WikiVersion[] }>(`/v1/companies/${companyId}/wiki/${encodeURIComponent(slug)}/versions`),

  writeWikiPage: (companyId: number, slug: string, data: WikiPageWrite) =>
    api.put<WikiPageDetail>(`/v1/companies/${companyId}/wiki/${encodeURIComponent(slug)}`, data),

  acceptWikiVersion: (versionId: number) =>
    api.post<WikiPageDetail>(`/v1/wiki_versions/${versionId}/accept`),

  rejectWikiVersion: (versionId: number, reason: string) =>
    api.post<WikiPageDetail>(`/v1/wiki_versions/${versionId}/reject`, { reason: reason || null }),

  lintWiki: (companyId: number) =>
    api.post<{ stale: Record<string, unknown[]> }>(`/v1/companies/${companyId}/wiki-lint`),

  setWikiSettings: (companyId: number, writeToRepo: boolean) =>
    api.put<{ write_to_repo: boolean }>(`/v1/companies/${companyId}/wiki-settings`, { write_to_repo: writeToRepo }),
})
