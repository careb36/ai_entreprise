import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query'
import { QUERY_KEYS } from '../services/queryKeys'
import { api, type WikiPageWrite } from '../services/api'

/** The company's wiki pages (no bodies) and its settings. */
export function useWiki(companyId: number | null) {
  return useQuery({
    queryKey: QUERY_KEYS.wiki(companyId ?? 0),
    queryFn: () => api.getWiki(companyId as number),
    enabled: companyId !== null,
  })
}

/** One page: its blocks with their sources, the proposal waiting, the lint. */
export function useWikiPage(companyId: number | null, slug: string | null) {
  return useQuery({
    queryKey: QUERY_KEYS.wikiPage(companyId ?? 0, slug ?? ''),
    queryFn: () => api.getWikiPage(companyId as number, slug as string),
    enabled: companyId !== null && !!slug,
  })
}

export function useWikiVersions(companyId: number | null, slug: string | null, enabled: boolean) {
  return useQuery({
    queryKey: QUERY_KEYS.wikiVersions(companyId ?? 0, slug ?? ''),
    queryFn: () => api.getWikiVersions(companyId as number, slug as string),
    enabled: enabled && companyId !== null && !!slug,
  })
}

function useInvalidateWiki(companyId: number) {
  const queryClient = useQueryClient()
  return () => queryClient.invalidateQueries({ queryKey: QUERY_KEYS.wiki(companyId) })
}

export function useWriteWikiPage(companyId: number) {
  const invalidate = useInvalidateWiki(companyId)
  return useMutation({
    mutationFn: ({ slug, data }: { slug: string; data: WikiPageWrite }) => api.writeWikiPage(companyId, slug, data),
    onSuccess: invalidate,
  })
}

export function useDecideWikiVersion(companyId: number) {
  const invalidate = useInvalidateWiki(companyId)
  return useMutation({
    mutationFn: ({ id, accept, reason }: { id: number; accept: boolean; reason?: string }) =>
      accept ? api.acceptWikiVersion(id) : api.rejectWikiVersion(id, reason ?? ''),
    onSuccess: invalidate,
  })
}

export function useLintWiki(companyId: number) {
  const invalidate = useInvalidateWiki(companyId)
  return useMutation({ mutationFn: () => api.lintWiki(companyId), onSuccess: invalidate })
}

export function useWikiSettings(companyId: number) {
  const invalidate = useInvalidateWiki(companyId)
  return useMutation({
    mutationFn: (writeToRepo: boolean) => api.setWikiSettings(companyId, writeToRepo),
    onSuccess: invalidate,
  })
}
