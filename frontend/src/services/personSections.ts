/**
 * The person sections of a documentation pack (issue #530, phase 2): what only
 * a person can write (the charter's business case, benefits, budget and
 * sponsor, the risk appetite, the checks a person judges). A Viewer reads each
 * section's current statement and its history; an Owner or a Manager writes or
 * withdraws one (`routers/person_sections.py`, audited). A statement is a claim
 * by its author: the backend records who, in which role and when, and computes
 * its digest; nothing here sends an author, a time or a digest. Writing or
 * withdrawing one makes a compiled pack stale, so its lint is read again.
 */
import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query'
import { api, fetchApi } from './api'

/** One revision of a section: a claim by `author`, or a withdrawal (`text` null). */
export interface PersonStatement {
  id: number
  section: string
  text: string | null
  withdrawn: boolean
  author: string
  author_role: string
  recorded_at: string | null
  supersedes_id: number | null
  statement_sha256: string
}

export interface PersonSection {
  /** `<document>.<section>`, such as `charter.budget`. */
  key: string
  /** The pack document it belongs to (`charter`, `risks`, `checks`). */
  document: string
  /** The current statement; null when never filled or withdrawn. */
  statement: PersonStatement | null
}

export interface PersonSectionList {
  company_id: number
  text_limit: number
  sections: PersonSection[]
}

export interface PersonSectionHistory {
  section: string
  revisions: PersonStatement[]
}

const base = (companyId: number) => `/v1/companies/${companyId}/documentation/person-sections`
const one = (companyId: number, section: string) => `${base(companyId)}/${encodeURIComponent(section)}`

export const personSectionsApi = {
  list: (companyId: number) => api.get<PersonSectionList>(base(companyId)),
  history: (companyId: number, section: string) => api.get<PersonSectionHistory>(`${one(companyId, section)}/history`),
  /** Each write is a new audited revision: sent once, never resent. */
  write: (companyId: number, section: string, text: string) =>
    fetchApi<PersonStatement>(one(companyId, section), { method: 'PUT', body: JSON.stringify({ text }), once: true }),
  withdraw: (companyId: number, section: string) =>
    fetchApi<PersonStatement>(one(companyId, section), { method: 'DELETE', once: true }),
}

export const personSectionsKey = (companyId: number) => ['documentation-person-sections', companyId] as const
export const personSectionHistoryKey = (companyId: number, section: string) =>
  ['documentation-person-section-history', companyId, section] as const

export function usePersonSections(companyId: number | null) {
  return useQuery({
    queryKey: personSectionsKey(companyId ?? 0),
    queryFn: () => personSectionsApi.list(companyId!),
    enabled: companyId !== null,
  })
}

export function usePersonSectionHistory(companyId: number, section: string, enabled = true) {
  return useQuery({
    queryKey: personSectionHistoryKey(companyId, section),
    queryFn: () => personSectionsApi.history(companyId, section),
    enabled,
  })
}

/** After a write or a withdrawal: the sections, that section's history and the packs' lint are read again. */
function useSettleSection() {
  const queryClient = useQueryClient()
  return (companyId: number, section: string) => {
    queryClient.invalidateQueries({ queryKey: personSectionsKey(companyId) })
    queryClient.invalidateQueries({ queryKey: personSectionHistoryKey(companyId, section) })
    queryClient.invalidateQueries({ queryKey: ['documentation-pack'] })
  }
}

export function useWritePersonSection() {
  const settle = useSettleSection()
  return useMutation({
    mutationFn: ({ companyId, section, text }: { companyId: number; section: string; text: string }) =>
      personSectionsApi.write(companyId, section, text),
    onSuccess: (_, { companyId, section }) => settle(companyId, section),
  })
}

export function useWithdrawPersonSection() {
  const settle = useSettleSection()
  return useMutation({
    mutationFn: ({ companyId, section }: { companyId: number; section: string }) => personSectionsApi.withdraw(companyId, section),
    onSuccess: (_, { companyId, section }) => settle(companyId, section),
  })
}
